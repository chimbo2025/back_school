"""Authoritative matching rules for till statements.

A till row only proves that *someone* paid an amount at a time. This module
decides whether a row belongs to a parent claim, needs review, or is unclaimed
money that an accountant should assign. It also suggests a student for
unclaimed rows by matching the payer phone to registered parents, emergency
contacts and previous claims.

The functions accept Django model objects or plain dictionaries, which keeps
them easy to unit test without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from ..normalization import normalize_phone

NOT_FOUND_DEFAULT_DAYS = 3
ZERO = Decimal('0.00')

OPEN_CLAIM_STATUSES = ('PENDING', 'NEEDS_REVIEW', 'NOT_FOUND')
INACTIVE_ROW_STATUSES = ('DUPLICATE', 'SKIPPED')


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


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0).replace(',', '').strip())
    except Exception:
        return ZERO


@dataclass(frozen=True)
class MatchableRow:
    transaction_id: str = ''
    amount: Decimal = ZERO
    sender_phone: str = ''
    direction: str = 'IN'
    date: object = None
    sender_name: str = ''
    receiver_till: str = ''


@dataclass
class RowMatch:
    row: MatchableRow
    row_number: int
    status: str  # MATCHED | UNCLAIMED | DUPLICATE | SKIPPED
    claim_id: str | None = None
    student_id: str | None = None
    match_type: str | None = None
    reason: str = ''


@dataclass
class ClaimMatch:
    claim_id: str
    status: str  # MATCHED | NEEDS_REVIEW | UNMATCHED
    match_type: str | None = None
    reason: str = ''
    row_number: int | None = None


@dataclass
class MatchResult:
    rows: list[RowMatch] = field(default_factory=list)
    claims: list[ClaimMatch] = field(default_factory=list)
    exact_count: int = 0
    needs_review_count: int = 0
    unclaimed_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0


@dataclass
class StudentSuggestion:
    student: object
    reason: str
    matched_phone: str


@dataclass
class NotFoundResult:
    not_found: list = field(default_factory=list)
    restored: list = field(default_factory=list)


def coerce_row(row) -> MatchableRow:
    if isinstance(row, MatchableRow):
        return row
    return MatchableRow(
        transaction_id=(_field(row, 'transaction_id', 'transactionId', default='') or '').strip().upper(),
        amount=_decimal(_field(row, 'amount', default=0)),
        sender_phone=_field(row, 'sender_phone', 'senderPhone', default='') or '',
        direction=(_field(row, 'direction', default='IN') or 'IN').strip().upper(),
        date=_field(row, 'date'),
        sender_name=_field(row, 'sender_name', 'senderName', default='') or '',
        receiver_till=_field(row, 'receiver_till', 'receiverTill', default='') or '',
    )


def phones_for_parent(student, students=None, claims=None) -> list[str]:
    """Every phone that may legitimately pay for this student.

    Parent phone, emergency contact, sibling parent/emergency phones (same
    parent phone) and payer phones the parent already used on a claim.
    """
    if student is None:
        return []

    students = students if students is not None else [student]
    claims = claims or []

    target_parent = normalize_phone(_field(student, 'parent_phone', 'parentPhone', default=''))
    phones: set[str] = set()

    def add(phone):
        normalized = normalize_phone(phone or '')
        if normalized:
            phones.add(normalized)

    def add_student(candidate):
        add(_field(candidate, 'parent_phone', 'parentPhone', default=''))
        add(_field(candidate, 'emergency_contact_phone', 'emergencyContactPhone', default=''))

    # Siblings are students whose parent phone normalises to the same number.
    siblings = []
    for candidate in students:
        if target_parent and normalize_phone(
            _field(candidate, 'parent_phone', 'parentPhone', default='')
        ) == target_parent:
            siblings.append(candidate)
    if not siblings:
        siblings = [student]

    for sibling in siblings:
        add_student(sibling)
        sibling_id = str(_field(sibling, 'id', default=''))
        for claim in claims:
            if str(_field(claim, 'student_id', 'studentId', default='')) == sibling_id:
                add(_field(claim, 'payer_phone', 'payerPhone', default=''))

    return sorted(phones)


def suggest_students(row, students, claims=None) -> list[StudentSuggestion]:
    """Suggest students for an unclaimed row by payer phone.

    Suggestions are intentionally a heuristic: the accountant sees the reason
    and still has to assign the row in one click. The parent phone is ranked
    above an emergency contact, which is ranked above a phone used on an older
    claim.
    """
    claims = claims or []
    row = coerce_row(row)
    sender = normalize_phone(row.sender_phone)
    if not sender:
        return []

    priorities = {'Parent phone': 0, 'Emergency contact': 1, 'Previous claim payer': 2}
    found: dict[str, StudentSuggestion] = {}

    def offer(student, reason):
        student_id = str(_field(student, 'id', default=''))
        if not student_id:
            return
        current = found.get(student_id)
        if current is None or priorities.get(reason, 99) < priorities.get(current.reason, 99):
            found[student_id] = StudentSuggestion(
                student=student,
                reason=reason,
                matched_phone=sender,
            )

    for student in students:
        if normalize_phone(_field(student, 'parent_phone', 'parentPhone', default='')) == sender:
            offer(student, 'Parent phone')
        if normalize_phone(
            _field(student, 'emergency_contact_phone', 'emergencyContactPhone', default='')
        ) == sender:
            offer(student, 'Emergency contact')

    for claim in claims:
        if normalize_phone(_field(claim, 'payer_phone', 'payerPhone', default='')) != sender:
            continue
        student_id = str(_field(claim, 'student_id', 'studentId', default=''))
        student = next((s for s in students if str(_field(s, 'id', default='')) == student_id), None)
        if student is not None:
            offer(student, 'Previous claim payer')

    return sorted(
        found.values(),
        key=lambda suggestion: (
            priorities.get(suggestion.reason, 99),
            str(_field(suggestion.student, 'application_no', 'applicationNo', default='')),
        ),
    )


def is_duplicate_transaction(
    transaction_id,
    payment_references=(),
    statement_transactions=(),
    claim_transactions=(),
) -> bool:
    txn = (transaction_id or '').strip().upper()
    if not txn:
        return False
    return txn in {
        (value or '').strip().upper()
        for value in [*payment_references, *statement_transactions, *claim_transactions]
        if value
    }


def match_rows(
    rows,
    claims,
    students,
    existing_transaction_ids=(),
) -> MatchResult:
    """Match statement rows to open claims using the approved rules.

    * Exact: transaction ID, amount and a saved parent phone all match.
    * Needs review: transaction ID matches but amount or phone differs.
    * Unclaimed: no claim has that transaction ID.
    * Duplicate: the transaction ID already exists or repeats in this file.
    * Skipped: outgoing, missing transaction ID or invalid amount.
    """
    result = MatchResult()
    open_claims = [
        c for c in claims
        if (_field(c, 'status', default='') or '').upper() in OPEN_CLAIM_STATUSES
    ]
    claims_by_id = {
        str(_field(c, 'id', default='')): c for c in open_claims
    }
    claim_results: dict[str, ClaimMatch] = {}
    used_claim_ids: set[str] = set()
    seen_txns = {
        (txn or '').strip().upper()
        for txn in existing_transaction_ids
        if txn
    }

    for claim in open_claims:
        if not (_field(claim, 'transaction_id', 'transactionId', default='') or '').strip():
            claim_id = str(_field(claim, 'id', default=''))
            claim_results[claim_id] = ClaimMatch(
                claim_id=claim_id,
                status='NEEDS_REVIEW',
                reason='Claim has no transaction ID. / Hakuna namba ya muamala.',
            )

    for index, raw_row in enumerate(rows, start=1):
        row = coerce_row(raw_row)
        txn = row.transaction_id

        if row.direction == 'OUT':
            result.skipped_count += 1
            result.rows.append(
                RowMatch(
                    row=row,
                    row_number=index,
                    status='SKIPPED',
                    reason='Outgoing payment / Malipo yanayotoka',
                )
            )
            continue

        if not txn or row.amount <= 0:
            result.skipped_count += 1
            result.rows.append(
                RowMatch(
                    row=row,
                    row_number=index,
                    status='SKIPPED',
                    reason='Missing transaction ID or invalid amount.',
                )
            )
            continue

        if txn in seen_txns:
            result.duplicate_count += 1
            result.rows.append(
                RowMatch(
                    row=row,
                    row_number=index,
                    status='DUPLICATE',
                    reason=f'Transaction ID {txn} already imported or already paid.',
                )
            )
            continue
        seen_txns.add(txn)

        claim = next(
            (
                c for c in open_claims
                if str(_field(c, 'id', default='')) not in used_claim_ids
                and (_field(c, 'transaction_id', 'transactionId', default='') or '').strip().upper() == txn
            ),
            None,
        )

        if claim is None:
            result.unclaimed_count += 1
            result.rows.append(
                RowMatch(row=row, row_number=index, status='UNCLAIMED')
            )
            continue

        claim_id = str(_field(claim, 'id', default=''))
        student_id = str(_field(claim, 'student_id', 'studentId', default=''))
        student = next(
            (s for s in students if str(_field(s, 'id', default='')) == student_id),
            None,
        )
        phones = phones_for_parent(student, students, claims) if student else []
        sender = normalize_phone(row.sender_phone)
        phone_ok = bool(sender) and sender in phones
        amount_ok = row.amount == _decimal(_field(claim, 'amount', default=0))
        used_claim_ids.add(claim_id)

        if phone_ok and amount_ok:
            result.exact_count += 1
            result.rows.append(
                RowMatch(
                    row=row,
                    row_number=index,
                    status='MATCHED',
                    claim_id=claim_id,
                    student_id=student_id,
                    match_type='EXACT',
                )
            )
            claim_results[claim_id] = ClaimMatch(
                claim_id=claim_id,
                status='MATCHED',
                match_type='EXACT',
                row_number=index,
            )
            continue

        problems = []
        if not amount_ok:
            problems.append(
                f'Amount differs: statement {row.amount:,.2f} Tsh vs claim '
                f'{_decimal(_field(claim, "amount", default=0)):,.2f} Tsh'
            )
        if not phone_ok:
            saved = ', '.join(phones) if phones else 'none'
            problems.append(
                f'Payer phone {row.sender_phone or "(none)"} does not match any phone '
                f'saved for this parent ({saved})'
            )
        match_type = (
            'AMOUNT_PHONE_MISMATCH' if not phone_ok and not amount_ok
            else 'PHONE_MISMATCH' if not phone_ok
            else 'AMOUNT_MISMATCH'
        )
        reason = '; '.join(problems)
        result.needs_review_count += 1
        result.rows.append(
            RowMatch(
                row=row,
                row_number=index,
                status='MATCHED',
                claim_id=claim_id,
                student_id=student_id,
                match_type=match_type,
                reason=reason,
            )
        )
        claim_results[claim_id] = ClaimMatch(
            claim_id=claim_id,
            status='NEEDS_REVIEW',
            match_type=match_type,
            reason=reason,
            row_number=index,
        )

    for claim in open_claims:
        claim_id = str(_field(claim, 'id', default=''))
        if (
            (_field(claim, 'transaction_id', 'transactionId', default='') or '').strip()
            and claim_id not in claim_results
        ):
            claim_results[claim_id] = ClaimMatch(
                claim_id=claim_id,
                status='UNMATCHED',
            )

    result.claims = list(claim_results.values())
    return result


def find_not_found(
    claims,
    statement_rows,
    days: int = NOT_FOUND_DEFAULT_DAYS,
    now=None,
) -> NotFoundResult:
    """Pure version of the NOT_FOUND rule, used by the command and tests."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=days)

    rows_by_claim: dict[str, list] = {}
    for row in statement_rows or []:
        if (_field(row, 'status', default='') or '').upper() in INACTIVE_ROW_STATUSES:
            continue
        claim_id = _field(row, 'claim_id', 'claim', default=None)
        if claim_id is None:
            continue
        # A model FK returns the related object; use its PK when present.
        claim_id = str(getattr(claim_id, 'pk', claim_id))
        rows_by_claim.setdefault(claim_id, []).append(row)

    not_found, restored = [], []
    for claim in claims or []:
        claim_id = str(_field(claim, 'id', default=''))
        status = (_field(claim, 'status', default='') or '').upper()
        has_row = bool(rows_by_claim.get(claim_id))

        if status in ('PENDING', 'NEEDS_REVIEW') and not has_row:
            submitted = _field(claim, 'submitted_at', 'submittedAt')
            submitted = _as_datetime(submitted)
            if submitted is not None and submitted <= cutoff:
                not_found.append(claim)
        elif status == 'NOT_FOUND' and has_row:
            restored.append(claim)

    return NotFoundResult(not_found=not_found, restored=restored)


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
    return None
