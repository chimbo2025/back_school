"""Authoritative allocation rules for school fees.

The browser keeps a small TypeScript copy for instant preview, but every
confirmed payment is allocated here. The service is deliberately independent
of the payment source (parent claim, statement import, admin assignment or a
future provider callback): callers only need a student and an amount.

Money is handled with ``Decimal`` and rounded to cents. A payment can be:

* a lump sum, allocated in the configured fee-group priority order,
* an itemised request from the parent, applied first and capped by what is
  still due on each line,
* a reallocation by an accountant/admin.

Any money left after every fee line is full becomes a persisted ``Credit``.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Sequence

from django.db import transaction
from django.utils import timezone

from ..models import Allocation, AuditLog, Credit, Payment, Receipt, Student
from ..utils import new_id

CENT = Decimal('0.01')
ZERO = Decimal('0.00')

DEFAULT_ALLOCATION_PRIORITY = [
    {'feeGroupName': 'ADMISSION FEE', 'priority': 1},
    {'feeGroupName': 'SCHOOL FEE', 'priority': 2},
    {'feeGroupName': 'TRANSPORT FEE', 'priority': 3},
    {'feeGroupName': 'OTHER FEE', 'priority': 4},
]


class AllocationError(ValueError):
    """Base class for allocation problems that are safe to show to staff."""


class SplitValidationError(AllocationError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(self.errors))


class DuplicateTransactionError(AllocationError):
    pass


def money(value) -> Decimal:
    """Coerce a value to a two-decimal Decimal without raising."""
    if value is None or value == '':
        return ZERO
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).replace(',', '').strip())
        except (InvalidOperation, ValueError, TypeError):
            return ZERO
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _field(obj, *names, default=None):
    for name in names:
        value = _get(obj, name)
        if value is not None:
            return value
    return default


@dataclass(frozen=True)
class FeeLine:
    id: str
    fee_group_id: str
    fee_group_name: str
    fee_item_id: str
    fee_item_label: str
    amount: Decimal
    priority: int


@dataclass(frozen=True)
class PlannedAllocation:
    fee_line_id: str
    fee_group_name: str
    fee_item_label: str
    amount: Decimal
    source: str = 'PRIORITY'


@dataclass
class PaymentPlan:
    requested_total: Decimal
    allocations: list[PlannedAllocation] = field(default_factory=list)
    credit: Decimal = ZERO

    @property
    def allocated_total(self) -> Decimal:
        return sum((a.amount for a in self.allocations), ZERO)


@dataclass
class ReallocationPlan:
    reversed_ids: list[str]
    allocations: list[PlannedAllocation]
    credit: Decimal = ZERO

    @property
    def allocated_total(self) -> Decimal:
        return sum((a.amount for a in self.allocations), ZERO)


@dataclass
class ConfirmationResult:
    payment: Payment
    allocations: list[Allocation]
    credit: Credit | None
    receipt: Receipt | None
    created: bool


def _priority_rules(rules: Sequence | None):
    return rules or DEFAULT_ALLOCATION_PRIORITY


def build_fee_lines(grade, rules: Sequence | None = None) -> list[FeeLine]:
    """Flatten a grade's fee groups/items into sortable fee lines.

    Unknown groups get priority ``99`` so they are still allocated after the
    configured groups, exactly like the frontend preview.
    """
    priority_map = {}
    for index, rule in enumerate(_priority_rules(rules)):
        name = (_field(rule, 'feeGroupName', 'fee_group_name', default='') or '').strip().upper()
        priority = _field(rule, 'priority', default=index + 1)
        if name:
            priority_map[name] = int(priority)

    groups = grade.fee_groups
    groups = groups.all() if hasattr(groups, 'all') else (groups or [])

    lines: list[FeeLine] = []
    for group in groups:
        group_name = _get(group, 'name', '') or ''
        priority = priority_map.get(group_name.strip().upper(), 99)
        items = _get(group, 'items')
        items = items.all() if hasattr(items, 'all') else (items or [])
        for item in items:
            group_id = str(_get(group, 'id', ''))
            item_id = str(_get(item, 'id', ''))
            lines.append(
                FeeLine(
                    id=f'{group_id}::{item_id}',
                    fee_group_id=group_id,
                    fee_group_name=group_name,
                    fee_item_id=item_id,
                    fee_item_label=_get(item, 'label', '') or '',
                    amount=money(_get(item, 'amount', 0)),
                    priority=priority,
                )
            )
    lines.sort(key=lambda line: (line.priority, line.fee_group_id, line.fee_item_id))
    return lines


def _line_paid_map(allocations: Iterable) -> dict[str, Decimal]:
    paid: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for alloc in allocations or []:
        if _get(alloc, 'reversed', False):
            continue
        fee_line_id = _field(alloc, 'fee_line_id', 'feeLineId')
        amount = money(_field(alloc, 'amount', default=0))
        if fee_line_id and amount > 0:
            paid[fee_line_id] += amount
    return paid


def validate_requested_split(requested_allocations, amount, fee_lines: Sequence[FeeLine] | None = None):
    """Return a list of human-readable split errors (empty means valid).

    The parent may choose what a payment covers, but the split is only a
    request: the total must equal the amount they say they paid. If the till
    statement later shows a different amount, the service applies the request
    up to the statement amount and allocates the rest by priority.
    """
    if requested_allocations in (None, [], {}):
        return []
    if not isinstance(requested_allocations, list):
        return ['Requested split must be a list. / Mgawanyo lazima uwe orodha.']

    amount = money(amount)
    line_map = {line.id: line for line in fee_lines} if fee_lines else None
    errors: list[str] = []
    seen: set[str] = set()
    total = ZERO

    for index, item in enumerate(requested_allocations, start=1):
        if not isinstance(item, dict):
            errors.append(f'Split line {index} is invalid.')
            continue
        fee_line_id = (item.get('fee_line_id') or item.get('feeLineId') or '').strip()
        if not fee_line_id:
            errors.append(f'Split line {index} is missing a fee line.')
            continue
        if line_map is not None and fee_line_id not in line_map:
            errors.append(f'Split line {index} refers to an unknown fee line.')
            continue
        if fee_line_id in seen:
            errors.append(f'Split line {index} repeats the same fee line.')
            continue
        seen.add(fee_line_id)
        line_amount = money(item.get('amount'))
        if line_amount <= 0:
            errors.append(f'Split line {index} must be greater than zero.')
            continue
        total += line_amount

    if not errors:
        if total != amount:
            errors.append(
                f'Split total {total:,.2f} Tsh does not equal the amount paid {amount:,.2f} Tsh.'
            )
    return errors


def plan_payment(
    amount,
    fee_lines: Sequence[FeeLine],
    existing_allocations: Iterable = (),
    requested_allocations=None,
) -> PaymentPlan:
    """Plan how one payment fills the student's fee lines.

    ``existing_allocations`` are the active allocations from earlier payments.
    They are used to calculate what is still due on each line, so a lump sum
    and a split request obey the same rules.
    """
    amount = money(amount)
    if amount <= 0:
        raise AllocationError('Payment amount must be greater than zero.')

    line_map = {line.id: line for line in fee_lines}
    paid = _line_paid_map(existing_allocations)
    remaining = amount
    planned: list[PlannedAllocation] = []
    requested_total = ZERO

    def place(line: FeeLine, requested: Decimal, source: str):
        nonlocal remaining
        if remaining <= 0 or requested <= 0:
            return
        due = line.amount - paid[line.id]
        if due <= 0:
            return
        placed = min(requested, due, remaining)
        if placed <= 0:
            return
        planned.append(
            PlannedAllocation(
                fee_line_id=line.id,
                fee_group_name=line.fee_group_name,
                fee_item_label=line.fee_item_label,
                amount=placed,
                source=source,
            )
        )
        paid[line.id] += placed
        remaining -= placed

    requested_items = requested_allocations if isinstance(requested_allocations, list) else []
    for item in requested_items:
        fee_line_id = (item.get('fee_line_id') or item.get('feeLineId') or '').strip()
        line = line_map.get(fee_line_id)
        if line is None:
            continue
        requested_amount = money(item.get('amount'))
        requested_total += requested_amount
        place(line, requested_amount, 'SPLIT')

    for line in fee_lines:
        if remaining <= 0:
            break
        place(line, remaining, 'PRIORITY')

    return PaymentPlan(
        requested_total=requested_total,
        allocations=planned,
        credit=money(remaining),
    )


def plan_reallocation(
    *,
    payment_id: str,
    payment_amount,
    fee_lines: Sequence[FeeLine],
    existing_allocations: Iterable,
    target_fee_line_id: str,
    amount,
    performed_by: str = 'system',
    reason: str = '',
) -> ReallocationPlan:
    """Move money from one payment onto a different fee line.

    The payment's existing allocations are reversed in full, the requested
    amount is placed on the target line first, then the rest of the payment is
    refilled in priority order. Leftover money becomes a credit instead of
    being silently discarded.
    """
    amount = money(amount)
    payment_amount = money(payment_amount)
    if amount <= 0:
        raise AllocationError('Reallocation amount must be greater than zero.')

    active = [a for a in (existing_allocations or []) if not _get(a, 'reversed', False)]
    payment_allocs = [
        a for a in active if str(_field(a, 'payment_id', 'paymentId') or '') == str(payment_id)
    ]
    freed = sum((money(_get(a, 'amount')) for a in payment_allocs), ZERO)
    if amount > freed:
        raise AllocationError(
            f'Cannot reallocate {amount:,.2f} Tsh: only {freed:,.2f} Tsh is available from this payment.'
        )

    line_map = {line.id: line for line in fee_lines}
    target_line = line_map.get(target_fee_line_id)
    if target_line is None:
        raise AllocationError('Target fee line not found.')

    others = [
        a for a in active if str(_field(a, 'payment_id', 'paymentId') or '') != str(payment_id)
    ]
    paid = _line_paid_map(others)

    target_due = target_line.amount - paid[target_line.id]
    if amount > target_due:
        raise AllocationError(
            f'Target line only has {target_due:,.2f} Tsh due; cannot allocate {amount:,.2f} Tsh.'
        )

    remaining = payment_amount
    planned: list[PlannedAllocation] = []

    def place(line: FeeLine, requested: Decimal):
        nonlocal remaining
        if remaining <= 0 or requested <= 0:
            return
        due = line.amount - paid[line.id]
        if due <= 0:
            return
        placed = min(requested, due, remaining)
        if placed <= 0:
            return
        planned.append(
            PlannedAllocation(
                fee_line_id=line.id,
                fee_group_name=line.fee_group_name,
                fee_item_label=line.fee_item_label,
                amount=placed,
                source='REALLOCATION',
            )
        )
        paid[line.id] += placed
        remaining -= placed

    # The target line goes first so the admin's intent is guaranteed.
    place(target_line, amount)
    for line in fee_lines:
        if remaining <= 0:
            break
        place(line, remaining)

    reversed_ids = [
        str(_get(a, 'id')) for a in payment_allocs if _get(a, 'id') is not None
    ]
    return ReallocationPlan(
        reversed_ids=reversed_ids,
        allocations=planned,
        credit=money(remaining),
    )


def build_receipt_snapshot(student, payment, allocations: Iterable = ()) -> dict:
    """Readable snapshot stored with a receipt so later fee edits cannot change it."""
    return {
        'student_no': getattr(student, 'application_no', ''),
        'student_name': getattr(student, 'full_name', '') or '',
        'grade': getattr(getattr(student, 'grade', None), 'name', '') or '',
        'amount': str(money(getattr(payment, 'amount', 0))),
        'transaction_id': getattr(payment, 'reference', ''),
        'payment_date': (
            payment.date.isoformat() if getattr(payment, 'date', None) else ''
        ),
        'method': getattr(payment, 'method', '') or '',
        'notes': getattr(payment, 'notes', '') or '',
        'allocations': [
            {
                'fee_line_id': _get(a, 'fee_line_id', ''),
                'fee_group_name': _get(a, 'fee_group_name', ''),
                'fee_item_label': _get(a, 'fee_item_label', ''),
                'amount': str(money(_get(a, 'amount', 0))),
                'source': _get(a, 'source', ''),
            }
            for a in (allocations or [])
        ],
    }


def _new_receipt_no() -> str:
    return f'RCPT-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}'


def confirm_payment(
    *,
    student: Student,
    amount,
    reference: str,
    payment_date=None,
    method: str = '',
    notes: str = '',
    source: str = 'CLAIM',
    statement_row=None,
    claim=None,
    requested_allocations=None,
    performed_by: str = 'system',
    provider: str = '',
    external_id: str | None = None,
    raw_payload=None,
    rules: Sequence | None = None,
    row_status: str = 'MATCHED',
    row_match_type: str = 'EXACT',
    audit_action: str | None = None,
) -> ConfirmationResult:
    """Create a confirmed payment and everything that follows it.

    This is the single entry point for claims, CSV matches, one-click
    assignments and future provider callbacks. It runs in one transaction:

    1. reject a duplicate reference (unless the same provider ``external_id``
       was already processed, which is idempotent),
    2. create the ``Payment`` using the statement amount,
    3. allocate the split request first, then the priority order,
    4. persist any leftover as a ``Credit``,
    5. create the receipt snapshot and audit entry,
    6. update the claim/statement row when supplied.
    """
    amount = money(amount)
    if amount <= 0:
        raise AllocationError('Payment amount must be greater than zero.')

    reference = (reference or '').strip().upper()
    if not reference:
        raise AllocationError('A transaction ID is required to confirm a payment.')

    if external_id:
        existing = Payment.objects.filter(external_id=external_id).first()
        if existing:
            return ConfirmationResult(
                payment=existing,
                allocations=list(existing.allocations.filter(reversed=False)),
                credit=existing.credits.first(),
                receipt=getattr(existing, 'receipt', None),
                created=False,
            )

    payment_date = payment_date or timezone.localdate()

    with transaction.atomic():
        if Payment.objects.filter(reference=reference).exists():
            raise DuplicateTransactionError(
                f'Transaction ID "{reference}" has already been confirmed.'
            )

        payment = Payment.objects.create(
            id=new_id('pay'),
            student=student,
            amount=amount,
            method=method or 'Lipa Namba',
            date=payment_date,
            reference=reference,
            notes=notes or '',
            source=source,
            statement_row=statement_row,
            provider=provider or '',
            external_id=external_id or None,
            raw_payload=raw_payload or {},
        )

        existing_allocations = Allocation.objects.filter(student=student, reversed=False)
        fee_lines = build_fee_lines(student.grade, rules)
        requested = (
            requested_allocations
            if requested_allocations is not None
            else (getattr(claim, 'requested_allocations', []) or [])
        )
        plan = plan_payment(amount, fee_lines, existing_allocations, requested)

        created_allocations = []
        for planned in plan.allocations:
            allocation = Allocation.objects.create(
                id=new_id('alloc'),
                payment=payment,
                student=student,
                fee_line_id=planned.fee_line_id,
                fee_group_name=planned.fee_group_name,
                fee_item_label=planned.fee_item_label,
                amount=planned.amount,
                source=planned.source,
                reason=notes or '',
                created_by=performed_by,
            )
            created_allocations.append(allocation)

        credit = None
        if plan.credit > 0:
            credit = Credit.objects.create(
                id=new_id('credit'),
                student=student,
                payment=payment,
                amount=plan.credit,
                remaining_amount=plan.credit,
                reason=notes or 'Overpayment received',
                created_by=performed_by,
            )

        receipt = Receipt.objects.create(
            id=new_id('rcpt'),
            receipt_no=_new_receipt_no(),
            payment=payment,
            student=student,
            snapshot=build_receipt_snapshot(student, payment, created_allocations),
            issued_by=performed_by,
        )

        if claim is not None:
            claim.status = 'CONFIRMED'
            claim.statement_amount = amount
            claim.variance = money(amount - money(getattr(claim, 'amount', 0)))
            claim.matched_payment = payment
            claim.reviewed_at = timezone.now()
            claim.reviewed_by = performed_by
            claim.match_reason = ''
            claim.save()

        if statement_row is not None:
            statement_row.status = row_status
            statement_row.claim = claim
            statement_row.student = student
            statement_row.match_type = row_match_type
            statement_row.match_reason = notes or ''
            statement_row.save()

        AuditLog.objects.create(
            id=new_id('audit'),
            student=student,
            payment=payment,
            action=audit_action or ('CLAIM_APPROVE' if claim is not None else 'PAYMENT_CONFIRM'),
            performed_by=performed_by,
            after={
                'payment_id': payment.id,
                'amount': str(amount),
                'transaction_id': reference,
                'credit': str(plan.credit),
            },
            reason=notes or 'Payment confirmed against the till statement',
        )

        return ConfirmationResult(
            payment=payment,
            allocations=created_allocations,
            credit=credit,
            receipt=receipt,
            created=True,
        )
