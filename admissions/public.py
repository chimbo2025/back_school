"""Public parent endpoints.

There is no parent password in this version, so the public surface is kept
deliberately small: a parent must know both the student number and the phone
number registered for that student. Responses never list other parents and
failures are deliberately generic. Both lookup and status endpoints are
rate-limited in ``admissions.throttles``.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Allocation, Receipt, Student
from .normalization import normalize_phone
from .services.allocation import build_fee_lines, money, plan_payment
from .throttles import PublicLookupThrottle
from .verification import DEFAULT_SETTINGS, get_setting

ZERO = Decimal('0.00')


def _as_float(value):
    return float(money(value))


def _student_account(student):
    """Read model used by the public status page.

    New payments always have persisted allocations. Seeded/legacy payments may
    not, so we replay confirmed payments through the same allocation engine to
    show a sensible balance instead of claiming every fee is unpaid.
    """
    fee_lines = build_fee_lines(student.grade)
    total_fees = sum((line.amount for line in fee_lines), ZERO)
    payments = list(student.payments.order_by('date', 'created_at'))
    total_paid = sum((money(p.amount) for p in payments), ZERO)

    active_allocations = list(
        Allocation.objects.filter(student=student, reversed=False)
    )
    credits = list(student.credits.all())
    persisted_credit = sum((money(c.remaining_amount) for c in credits), ZERO)

    paid_by_line = {}
    allocated_payment_ids = set()
    for allocation in active_allocations:
        paid_by_line[allocation.fee_line_id] = (
            paid_by_line.get(allocation.fee_line_id, ZERO)
            + money(allocation.amount)
        )
        allocated_payment_ids.add(allocation.payment_id)

    # Seed/legacy payments may predate persisted allocations. Replay only the
    # payments that have none, on top of the allocations that do exist, so a
    # mixed history still shows the right balance.
    unallocated_ids = {
        payment.id for payment in payments if payment.id not in allocated_payment_ids
    }
    unallocated = [
        money(payment.amount) for payment in payments if payment.id in unallocated_ids
    ]
    extra_credit = ZERO
    if unallocated:
        plan = plan_payment(
            sum(unallocated, ZERO),
            fee_lines,
            existing_allocations=active_allocations,
        )
        for planned in plan.allocations:
            paid_by_line[planned.fee_line_id] = (
                paid_by_line.get(planned.fee_line_id, ZERO)
                + money(planned.amount)
            )
        # A seeded overpayment already has a persisted Credit; do not count it
        # twice when replaying the payments that produced it.
        credit_for_unallocated = sum(
            (
                money(credit.remaining_amount)
                for credit in credits
                if credit.payment_id in unallocated_ids
            ),
            ZERO,
        )
        extra_credit = max(plan.credit - credit_for_unallocated, ZERO)

    allocated = sum(paid_by_line.values(), ZERO)
    credit = persisted_credit + extra_credit

    lines = []
    for line in fee_lines:
        paid = paid_by_line.get(line.id, ZERO)
        due = line.amount - paid
        line_status = 'UNPAID' if paid <= 0 else 'PAID' if due <= 0 else 'PARTIAL'
        lines.append({
            'id': line.id,
            'fee_group_name': line.fee_group_name,
            'fee_item_label': line.fee_item_label,
            'amount': _as_float(line.amount),
            'paid': _as_float(paid),
            'due': _as_float(max(due, ZERO)),
            'status': line_status,
        })

    return {
        'total_fees': _as_float(total_fees),
        'total_paid': _as_float(total_paid),
        'total_allocated': _as_float(allocated),
        'balance': _as_float(max(total_fees - allocated, ZERO)),
        'credit': _as_float(credit),
        'fee_lines': lines,
    }


def _student_summary(student):
    account = _student_account(student)
    return {
        'id': student.id,
        'student_no': student.application_no,
        'name': student.full_name,
        'grade': student.grade.name,
        'status': student.status,
        'total_fees': account['total_fees'],
        'total_paid': account['total_paid'],
        'balance': account['balance'],
        'credit': account['credit'],
    }


def _generic_not_found():
    return Response(
        {'detail': 'No matching student was found. Check the student number and phone number.'},
        status=status.HTTP_404_NOT_FOUND,
    )


class PublicLookupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicLookupThrottle]

    def get(self, request):
        phone = normalize_phone(request.query_params.get('phone', ''))
        if not phone:
            return Response(
                {'detail': 'Enter a valid phone number. / Weka namba sahihi ya simu.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        students = (
            Student.objects.select_related('grade')
            .filter(parent_phone_normalized=phone)
            .order_by('application_no')
        )
        if not students:
            return _generic_not_found()

        return Response({
            'phone': phone,
            'students': [_student_summary(student) for student in students],
        })


class PublicStudentStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicLookupThrottle]

    def get(self, request, student_no):
        phone = normalize_phone(request.query_params.get('phone', ''))
        if not phone:
            return _generic_not_found()

        student = (
            Student.objects.select_related('grade')
            .filter(application_no__iexact=student_no, parent_phone_normalized=phone)
            .first()
        )
        if student is None:
            return _generic_not_found()

        account = _student_account(student)

        claims = []
        for claim in student.claims.order_by('-submitted_at'):
            claims.append({
                'id': claim.id,
                'amount': _as_float(claim.amount),
                'transaction_id': claim.transaction_id,
                'payment_date': claim.payment_date.isoformat() if claim.payment_date else '',
                'status': claim.status,
                'submitted_at': claim.submitted_at.isoformat() if claim.submitted_at else '',
                'statement_amount': (
                    _as_float(claim.statement_amount)
                    if claim.statement_amount is not None else None
                ),
                'variance': _as_float(claim.variance) if claim.variance is not None else None,
                'match_reason': claim.match_reason,
                'rejection_reason': claim.rejection_reason,
                'payment_id': claim.matched_payment_id,
            })

        payments = []
        for payment in student.payments.select_related('receipt').order_by('-date', '-created_at'):
            receipt_no = getattr(getattr(payment, 'receipt', None), 'receipt_no', None)
            payments.append({
                'id': payment.id,
                'amount': _as_float(payment.amount),
                'method': payment.method,
                'date': payment.date.isoformat() if payment.date else '',
                'reference': payment.reference,
                'source': payment.source,
                'receipt_no': receipt_no,
            })

        receipts = []
        for receipt in student.receipts.order_by('-issued_at'):
            receipts.append({
                'receipt_no': receipt.receipt_no,
                'payment_id': receipt.payment_id,
                'issued_at': receipt.issued_at.isoformat() if receipt.issued_at else '',
                'snapshot': receipt.snapshot,
            })

        return Response({
            'student': {
                'id': student.id,
                'student_no': student.application_no,
                'name': student.full_name,
                'grade': student.grade.name,
                'status': student.status,
                'academic_year': student.academic_year,
                'parent_name': student.parent_name,
                'parent_phone': student.parent_phone,
            },
            'account': account,
            'claims': claims,
            'payments': payments,
            'receipts': receipts,
            'contact': {
                'phone': get_setting('contact_phone') or DEFAULT_SETTINGS['contact_phone'],
                'whatsapp': get_setting('whatsapp') or DEFAULT_SETTINGS['whatsapp'],
            },
        })


class PublicReceiptView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicLookupThrottle]

    def get(self, request, receipt_no):
        phone = normalize_phone(request.query_params.get('phone', ''))
        if not phone:
            return _generic_not_found()

        receipt = (
            Receipt.objects.select_related('student__grade', 'payment')
            .filter(receipt_no__iexact=receipt_no, student__parent_phone_normalized=phone)
            .first()
        )
        if receipt is None:
            return _generic_not_found()

        payment = receipt.payment
        return Response({
            'receipt_no': receipt.receipt_no,
            'issued_at': receipt.issued_at.isoformat() if receipt.issued_at else '',
            'student': {
                'student_no': receipt.student.application_no,
                'name': receipt.student.full_name,
                'grade': receipt.student.grade.name,
            },
            'payment': {
                'id': payment.id,
                'amount': _as_float(payment.amount),
                'date': payment.date.isoformat() if payment.date else '',
                'reference': payment.reference,
                'method': payment.method,
                'source': payment.source,
            },
            'snapshot': receipt.snapshot,
        })


class PublicConfigView(APIView):
    """Small public configuration for the parent status page."""

    permission_classes = [AllowAny]
    throttle_classes = [PublicLookupThrottle]

    def get(self, request):
        return Response({
            'contact_phone': get_setting('contact_phone') or DEFAULT_SETTINGS['contact_phone'],
            'whatsapp': get_setting('whatsapp') or DEFAULT_SETTINGS['whatsapp'],
            'not_found_days': get_setting('not_found_days') or DEFAULT_SETTINGS['not_found_days'],
        })
