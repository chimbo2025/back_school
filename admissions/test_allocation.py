from datetime import date
from decimal import Decimal

from django.test import TestCase

from .models import (
    Allocation, FeeGroup, FeeItem, Grade, Payment, PaymentClaim,
    Receipt, StatementImport, StatementRow, Student,
)
from .services.allocation import (
    AllocationError,
    DuplicateTransactionError,
    ZERO,
    build_fee_lines,
    confirm_payment,
    plan_payment,
    plan_reallocation,
    validate_requested_split,
)


class AllocationServiceTests(TestCase):
    """Rules that must hold for every payment source."""

    def setUp(self):
        self.grade = Grade.objects.create(id='grade-1', name='Grade 1')

        self.admission = self._group(
            'fg-adm', 'ADMISSION FEE', 'fi-adm', 'Admission', 100000
        )
        self.school = self._group(
            'fg-sch', 'SCHOOL FEE', 'fi-sch', 'Term 1 Tuition', 400000
        )
        self.transport = self._group(
            'fg-tra', 'TRANSPORT FEE', 'fi-tra', 'School Bus', 200000
        )
        self.other = self._group(
            'fg-oth', 'OTHER FEE', 'fi-oth', 'Exam Fee', 50000
        )

        self.student = Student.objects.create(
            id='stu-alloc',
            application_no='BFS-2026-7001',
            first_name='Amina',
            last_name='Juma',
            gender='Female',
            dob='2015-01-01',
            address='Zanzibar',
            parent_name='Bi Mwanaisha Ali',
            parent_phone='0777123456',
            parent_address='Zanzibar',
            parent_relationship='Mother',
            grade=self.grade,
            academic_year='2026',
        )
        self.lines = build_fee_lines(self.grade)

    def _group(self, group_id, name, item_id, label, amount):
        group = FeeGroup.objects.create(id=group_id, name=name)
        item = FeeItem.objects.create(id=item_id, label=label, amount=Decimal(amount))
        group.items.add(item)
        self.grade.fee_groups.add(group)
        return group

    def _line(self, group_id, item_id):
        return f'{group_id}::{item_id}'

    def test_fee_lines_are_built_in_priority_order(self):
        self.assertEqual(
            [(line.fee_group_name, line.amount) for line in self.lines],
            [
                ('ADMISSION FEE', Decimal('100000.00')),
                ('SCHOOL FEE', Decimal('400000.00')),
                ('TRANSPORT FEE', Decimal('200000.00')),
                ('OTHER FEE', Decimal('50000.00')),
            ],
        )
        self.assertEqual(self.lines[0].id, self._line('fg-adm', 'fi-adm'))

    def test_lump_sum_fills_every_line_by_priority(self):
        plan = plan_payment(Decimal('750000'), self.lines)

        self.assertEqual(
            [(a.fee_line_id, a.amount) for a in plan.allocations],
            [
                (self._line('fg-adm', 'fi-adm'), Decimal('100000.00')),
                (self._line('fg-sch', 'fi-sch'), Decimal('400000.00')),
                (self._line('fg-tra', 'fi-tra'), Decimal('200000.00')),
                (self._line('fg-oth', 'fi-oth'), Decimal('50000.00')),
            ],
        )
        self.assertTrue(all(a.source == 'PRIORITY' for a in plan.allocations))
        self.assertEqual(plan.credit, ZERO)

    def test_itemised_split_is_applied_before_priority(self):
        requested = [
            {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 400000},
            {'fee_line_id': self._line('fg-tra', 'fi-tra'), 'amount': 200000},
        ]
        self.assertEqual(validate_requested_split(requested, 600000, self.lines), [])

        plan = plan_payment(600000, self.lines, requested_allocations=requested)

        self.assertEqual(
            [(a.fee_line_id, a.amount, a.source) for a in plan.allocations],
            [
                (self._line('fg-sch', 'fi-sch'), Decimal('400000.00'), 'SPLIT'),
                (self._line('fg-tra', 'fi-tra'), Decimal('200000.00'), 'SPLIT'),
            ],
        )
        self.assertEqual(plan.credit, ZERO)

    def test_split_total_must_equal_the_amount_paid(self):
        requested = [
            {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 500000},
        ]
        errors = validate_requested_split(requested, 600000, self.lines)
        self.assertTrue(errors)
        self.assertIn('does not equal the amount paid', errors[0])

    def test_split_with_unknown_or_duplicate_lines_is_rejected(self):
        errors = validate_requested_split(
            [
                {'fee_line_id': 'missing::line', 'amount': 100000},
                {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 100000},
                {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 100000},
            ],
            300000,
            self.lines,
        )
        self.assertTrue(any('unknown fee line' in e for e in errors))
        self.assertTrue(any('repeats the same fee line' in e for e in errors))

    def test_partial_payment_fills_admission_then_school_fee(self):
        plan = plan_payment(150000, self.lines)

        self.assertEqual(
            [(a.fee_line_id, a.amount) for a in plan.allocations],
            [
                (self._line('fg-adm', 'fi-adm'), Decimal('100000.00')),
                (self._line('fg-sch', 'fi-sch'), Decimal('50000.00')),
            ],
        )
        self.assertEqual(plan.credit, ZERO)

    def test_overpayment_creates_a_persisted_credit_and_receipt(self):
        result = confirm_payment(
            student=self.student,
            amount=800000,
            reference='MP-OVER-1',
            payment_date=date(2026, 2, 1),
            method='Lipa Namba',
            performed_by='accountant',
        )

        self.assertTrue(result.created)
        self.assertEqual(result.payment.amount, Decimal('800000.00'))
        self.assertEqual(
            sum((a.amount for a in result.allocations), ZERO),
            Decimal('750000.00'),
        )
        self.assertIsNotNone(result.credit)
        self.assertEqual(result.credit.remaining_amount, Decimal('50000.00'))
        self.assertIsNotNone(result.receipt)
        self.assertEqual(result.receipt.snapshot['amount'], '800000.00')
        self.assertEqual(len(result.receipt.snapshot['allocations']), 4)
        self.assertTrue(Receipt.objects.filter(payment=result.payment).exists())

    def test_split_is_capped_then_remainder_uses_priority(self):
        requested = [
            {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 250000},
        ]
        result = confirm_payment(
            student=self.student,
            amount=280000,
            reference='MP-SPLIT-VAR-1',
            payment_date=date(2026, 2, 2),
            method='Lipa Namba',
            requested_allocations=requested,
            performed_by='accountant',
        )

        self.assertEqual(
            [(a.fee_line_id, a.amount, a.source) for a in result.allocations],
            [
                (self._line('fg-sch', 'fi-sch'), Decimal('250000.00'), 'SPLIT'),
                (self._line('fg-adm', 'fi-adm'), Decimal('30000.00'), 'PRIORITY'),
            ],
        )

    def test_statement_amount_wins_and_variance_is_stored(self):
        import_record = StatementImport.objects.create(
            id='imp-var', file_name='statement.csv', file_hash='hash-var'
        )
        row = StatementRow.objects.create(
            id='row-var',
            import_record=import_record,
            transaction_id='MP-VAR-1',
            date=date(2026, 2, 2),
            amount=Decimal('280000'),
            sender_phone='0778407818',
        )
        claim = PaymentClaim.objects.create(
            id='claim-var',
            student=self.student,
            amount=Decimal('250000'),
            transaction_id='MP-VAR-1',
            payment_date=date(2026, 2, 2),
            payer_phone='0778407818',
        )

        result = confirm_payment(
            student=self.student,
            amount=row.amount,
            reference=row.transaction_id,
            payment_date=row.date,
            method='Lipa Namba',
            claim=claim,
            statement_row=row,
            performed_by='accountant',
        )

        claim.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(result.payment.amount, Decimal('280000.00'))
        self.assertEqual(claim.status, 'CONFIRMED')
        self.assertEqual(claim.statement_amount, Decimal('280000.00'))
        self.assertEqual(claim.variance, Decimal('30000.00'))
        self.assertEqual(row.status, 'MATCHED')
        self.assertEqual(
            sum((a.amount for a in result.allocations), ZERO),
            Decimal('280000.00'),
        )

    def test_duplicate_transaction_id_cannot_be_confirmed_twice(self):
        confirm_payment(
            student=self.student,
            amount=100000,
            reference='MP-DUP-1',
            payment_date=date(2026, 2, 3),
            method='Lipa Namba',
        )
        with self.assertRaises(DuplicateTransactionError):
            confirm_payment(
                student=self.student,
                amount=100000,
                reference='mp-dup-1',
                payment_date=date(2026, 2, 3),
                method='Lipa Namba',
            )

    def test_reallocation_reverses_and_refills_the_payment(self):
        payment = Payment.objects.create(
            id='pay-realloc',
            student=self.student,
            amount=Decimal('500000'),
            method='M-Pesa',
            date=date(2026, 2, 4),
            reference='MP-REALLOC-1',
        )
        admission_alloc = Allocation.objects.create(
            id='alloc-adm',
            payment=payment,
            student=self.student,
            fee_line_id=self._line('fg-adm', 'fi-adm'),
            fee_group_name='ADMISSION FEE',
            fee_item_label='Admission',
            amount=Decimal('100000'),
            source='PRIORITY',
        )
        school_alloc = Allocation.objects.create(
            id='alloc-sch',
            payment=payment,
            student=self.student,
            fee_line_id=self._line('fg-sch', 'fi-sch'),
            fee_group_name='SCHOOL FEE',
            fee_item_label='Term 1 Tuition',
            amount=Decimal('400000'),
            source='PRIORITY',
        )

        plan = plan_reallocation(
            payment_id=payment.id,
            payment_amount=payment.amount,
            fee_lines=self.lines,
            existing_allocations=list(payment.allocations.all()),
            target_fee_line_id=self._line('fg-tra', 'fi-tra'),
            amount=200000,
            performed_by='accountant',
            reason='Parent asked for bus fees',
        )

        self.assertEqual(
            set(plan.reversed_ids), {admission_alloc.id, school_alloc.id}
        )
        self.assertEqual(
            [(a.fee_line_id, a.amount) for a in plan.allocations],
            [
                (self._line('fg-tra', 'fi-tra'), Decimal('200000.00')),
                (self._line('fg-adm', 'fi-adm'), Decimal('100000.00')),
                (self._line('fg-sch', 'fi-sch'), Decimal('200000.00')),
            ],
        )
        self.assertTrue(all(a.source == 'REALLOCATION' for a in plan.allocations))
        self.assertEqual(plan.credit, ZERO)

    def test_reallocation_cannot_exceed_the_payment(self):
        payment = Payment.objects.create(
            id='pay-small',
            student=self.student,
            amount=Decimal('100000'),
            method='M-Pesa',
            date=date(2026, 2, 5),
            reference='MP-SMALL-1',
        )
        Allocation.objects.create(
            id='alloc-small',
            payment=payment,
            student=self.student,
            fee_line_id=self._line('fg-adm', 'fi-adm'),
            amount=Decimal('100000'),
            source='PRIORITY',
        )
        with self.assertRaises(AllocationError):
            plan_reallocation(
                payment_id=payment.id,
                payment_amount=payment.amount,
                fee_lines=self.lines,
                existing_allocations=list(payment.allocations.all()),
                target_fee_line_id=self._line('fg-sch', 'fi-sch'),
                amount=200000,
            )

    def test_provider_callback_is_idempotent(self):
        first = confirm_payment(
            student=self.student,
            amount=200000,
            reference='PROV-1',
            payment_date=date(2026, 2, 6),
            method='Lipa Namba',
            source='PROVIDER',
            provider='M-Pesa',
            external_id='ext-123',
            raw_payload={'callback': 'first'},
        )
        second = confirm_payment(
            student=self.student,
            amount=200000,
            reference='PROV-1',
            payment_date=date(2026, 2, 6),
            method='Lipa Namba',
            source='PROVIDER',
            provider='M-Pesa',
            external_id='ext-123',
            raw_payload={'callback': 'second'},
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.payment.id, second.payment.id)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Allocation.objects.count(), len(first.allocations))
        self.assertEqual(Receipt.objects.count(), 1)
