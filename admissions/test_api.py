from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    Allocation, AppSetting, AuditLog, Credit, FeeGroup, FeeItem, Grade, Payment,
    PaymentClaim, Receipt, StatementRow, Student,
)
from .throttles import ClaimSubmitThrottle, PublicLookupThrottle


class PaymentApiTests(TestCase):
    """End-to-end API rules for the Phase 2 payment flow."""

    def setUp(self):
        cache.clear()

        self.grade = Grade.objects.create(id='grade-api', name='Grade 1')
        self._group('fg-adm', 'ADMISSION FEE', 'fi-adm', 'Admission', 100000)
        self._group('fg-sch', 'SCHOOL FEE', 'fi-sch', 'Term 1 Tuition', 400000)
        self._group('fg-tra', 'TRANSPORT FEE', 'fi-tra', 'School Bus', 200000)
        self._group('fg-oth', 'OTHER FEE', 'fi-oth', 'Exam Fee', 50000)

        self.student = self._student(
            'stu-api-1', 'BFS-2026-1001', 'Amina', '0777123456'
        )
        self.sibling = self._student(
            'stu-api-2', 'BFS-2026-1013', 'Hamisi', '0777 123 456'
        )
        self.other = self._student(
            'stu-api-3', 'BFS-2026-1002', 'Zainab', '0682 987 654'
        )

        self.accountant = User.objects.create_user(
            'accountant', password='x', is_staff=True
        )
        self.admin = User.objects.create_superuser('admin', password='x')

        self.anon = APIClient()
        self.staff = APIClient()
        self.staff.force_authenticate(self.accountant)
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)

    def _group(self, group_id, name, item_id, label, amount):
        group = FeeGroup.objects.create(id=group_id, name=name)
        item = FeeItem.objects.create(id=item_id, label=label, amount=Decimal(amount))
        group.items.add(item)
        self.grade.fee_groups.add(group)

    def _student(self, student_id, app_no, first_name, phone):
        return Student.objects.create(
            id=student_id,
            application_no=app_no,
            first_name=first_name,
            last_name='Juma',
            gender='Female',
            dob='2015-01-01',
            address='Zanzibar',
            parent_name='Bi Mwanaisha Ali',
            parent_phone=phone,
            parent_address='Zanzibar',
            parent_relationship='Mother',
            grade=self.grade,
            academic_year='2026',
        )

    def _line(self, group_id, item_id):
        return f'{group_id}::{item_id}'

    def _claim(self, student=None, amount=100000, txn='MP-API-1', phone='0777123456',
               requested=None):
        payload = {
            'student': (student or self.student).id,
            'amount': amount,
            'transaction_id': txn,
            'payment_date': '2026-02-01',
            'payer_phone': phone,
            'method': 'Lipa Namba',
        }
        if requested is not None:
            payload['requested_allocations'] = requested
        return self.anon.post('/api/claims/', payload, format='json')

    def _import(self, rows, file_hash='hash-api-1', mapping=None):
        return self.staff.post(
            '/api/statements/imports/',
            {
                'file_name': 'statement.csv',
                'file_hash': file_hash,
                'mapping': mapping or {},
                'rows': rows,
            },
            format='json',
        )

    def _row(self, txn, amount, phone='0777123456', direction='IN', date='2026-02-01'):
        return {
            'transaction_id': txn,
            'date': date,
            'amount': amount,
            'sender_phone': phone,
            'sender_name': 'BI MWANAISHA ALI',
            'receiver_till': 'LIPA NAMBA',
            'direction': direction,
        }

    # --- claims ---------------------------------------------------------

    def test_public_claim_with_valid_split_is_saved(self):
        response = self._claim(
            requested=[
                {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 100000}
            ]
        )

        self.assertEqual(response.status_code, 201, response.data)
        claim = PaymentClaim.objects.get(id=response.data['id'])
        self.assertEqual(claim.status, 'PENDING')
        self.assertEqual(claim.requested_allocations[0]['amount'], 100000)

    def test_claim_with_split_total_that_does_not_match_is_rejected(self):
        response = self._claim(
            amount=100000,
            requested=[
                {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 60000}
            ],
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('requested_allocations', response.data)

    def test_duplicate_claim_transaction_id_is_blocked(self):
        self.assertEqual(self._claim(txn='MP-API-DUP').status_code, 201)
        second = self._claim(txn='mp-api-dup')

        self.assertEqual(second.status_code, 400, second.data)
        self.assertIn('already been submitted', str(second.data))

    def test_anonymous_cannot_list_claims(self):
        response = self.anon.get('/api/claims/')

        self.assertIn(response.status_code, (401, 403))

    def test_claim_submission_is_throttled(self):
        previous = getattr(ClaimSubmitThrottle, 'rate', None)
        ClaimSubmitThrottle.rate = '2/min'
        try:
            self.assertEqual(self._claim(txn='MP-THROTTLE-1').status_code, 201)
            self.assertEqual(self._claim(txn='MP-THROTTLE-2').status_code, 201)
            blocked = self._claim(txn='MP-THROTTLE-3')
            self.assertEqual(blocked.status_code, 429, blocked.data)
        finally:
            if previous is None:
                del ClaimSubmitThrottle.rate
            else:
                ClaimSubmitThrottle.rate = previous
            cache.clear()

    # --- public parent surface -----------------------------------------

    def test_public_lookup_returns_all_children_for_a_phone(self):
        response = self.anon.get('/api/public/lookup/', {'phone': '0777 123 456'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [s['student_no'] for s in response.data['students']],
            ['BFS-2026-1001', 'BFS-2026-1013'],
        )

    def test_public_status_requires_the_registered_phone(self):
        wrong = self.anon.get(
            f'/api/public/students/{self.student.application_no}/',
            {'phone': '0700000000'},
        )
        self.assertEqual(wrong.status_code, 404)

        right = self.anon.get(
            f'/api/public/students/{self.student.application_no}/',
            {'phone': '0777123456'},
        )
        self.assertEqual(right.status_code, 200, right.data)
        self.assertEqual(right.data['student']['student_no'], 'BFS-2026-1001')
        self.assertIn('account', right.data)
        self.assertIn('claims', right.data)

    def test_public_lookup_is_throttled(self):
        previous = getattr(PublicLookupThrottle, 'rate', None)
        PublicLookupThrottle.rate = '2/min'
        try:
            self.assertEqual(
                self.anon.get('/api/public/lookup/', {'phone': '0777123456'}).status_code,
                200,
            )
            self.assertEqual(
                self.anon.get('/api/public/lookup/', {'phone': '0777123456'}).status_code,
                200,
            )
            blocked = self.anon.get(
                '/api/public/lookup/', {'phone': '0777123456'}
            )
            self.assertEqual(blocked.status_code, 429)
        finally:
            if previous is None:
                del PublicLookupThrottle.rate
            else:
                PublicLookupThrottle.rate = previous
            cache.clear()

    # --- statement import ----------------------------------------------

    def test_exact_statement_row_auto_confirms_the_claim(self):
        claim_response = self._claim()
        self.assertEqual(claim_response.status_code, 201, claim_response.data)
        claim_id = claim_response.data['id']

        response = self._import([self._row('MP-API-1', 100000)])

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data['payments']), 1)
        claim = PaymentClaim.objects.get(id=claim_id)
        self.assertEqual(claim.status, 'CONFIRMED')
        self.assertEqual(claim.statement_amount, Decimal('100000.00'))
        self.assertIsNotNone(claim.matched_payment_id)

        row = StatementRow.objects.get(transaction_id='MP-API-1')
        self.assertEqual(row.status, 'MATCHED')
        self.assertEqual(row.student_id, self.student.id)

        self.assertEqual(Payment.objects.count(), 1)
        self.assertTrue(Receipt.objects.filter(payment=claim.matched_payment).exists())
        self.assertTrue(
            Allocation.objects.filter(payment=claim.matched_payment, source='PRIORITY').exists()
        )

    def test_exact_match_applies_the_parent_split_request(self):
        response = self._claim(
            amount=100000,
            requested=[
                {'fee_line_id': self._line('fg-sch', 'fi-sch'), 'amount': 100000}
            ],
        )
        self.assertEqual(response.status_code, 201, response.data)

        imported = self._import([self._row('MP-API-1', 100000)])
        self.assertEqual(imported.status_code, 201, imported.data)

        allocation = Allocation.objects.get(
            payment__reference='MP-API-1',
            fee_line_id=self._line('fg-sch', 'fi-sch'),
        )
        self.assertEqual(allocation.amount, Decimal('100000.00'))
        self.assertEqual(allocation.source, 'SPLIT')

    def test_amount_mismatch_goes_to_review_then_approval_uses_statement_amount(self):
        claim_response = self._claim()
        claim_id = claim_response.data['id']

        response = self._import([self._row('MP-API-1', 90000)])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['payments'], [])

        claim = PaymentClaim.objects.get(id=claim_id)
        self.assertEqual(claim.status, 'NEEDS_REVIEW')
        self.assertIn('Amount differs', claim.match_reason)
        self.assertEqual(claim.statement_amount, Decimal('90000.00'))
        row = StatementRow.objects.get(transaction_id='MP-API-1')
        self.assertEqual(row.match_type, 'AMOUNT_MISMATCH')

        approved = self.staff.post(
            f'/api/claims/{claim_id}/approve/',
            {'reason': 'Confirmed on till statement'},
            format='json',
        )
        self.assertEqual(approved.status_code, 201, approved.data)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'CONFIRMED')
        self.assertEqual(claim.matched_payment.amount, Decimal('90000.00'))
        self.assertEqual(claim.variance, Decimal('-10000.00'))

    def test_reimporting_the_same_file_is_idempotent(self):
        self.assertEqual(self._claim().status_code, 201)
        first = self._import([self._row('MP-API-1', 100000)], file_hash='same-file')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(len(first.data['payments']), 1)

        second = self._import([self._row('MP-API-1', 100000)], file_hash='same-file')

        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(second.data['duplicate_file'])
        self.assertEqual(second.data['payments'], [])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(StatementRow.objects.count(), 1)

    def test_unclaimed_row_suggests_student_and_can_be_assigned(self):
        response = self._import([self._row('MP-API-UNCLAIMED', 150000)])
        self.assertEqual(response.status_code, 201, response.data)

        row = StatementRow.objects.get(transaction_id='MP-API-UNCLAIMED')
        self.assertEqual(row.status, 'UNCLAIMED')
        suggestions = response.data['rows'][0]['suggested_students']
        self.assertEqual(suggestions[0]['application_no'], 'BFS-2026-1001')
        self.assertEqual(suggestions[0]['reason'], 'Parent phone')

        assigned = self.staff.post(
            f'/api/statements/{row.id}/assign_row/',
            {'student_id': self.student.id, 'note': 'Verified by accountant'},
            format='json',
        )
        self.assertEqual(assigned.status_code, 201, assigned.data)
        row.refresh_from_db()
        self.assertEqual(row.status, 'ASSIGNED')
        self.assertEqual(row.student_id, self.student.id)
        self.assertTrue(Receipt.objects.filter(payment__reference='MP-API-UNCLAIMED').exists())
        self.assertTrue(
            AuditLog.objects.filter(action='ROW_ASSIGN', payment__reference='MP-API-UNCLAIMED').exists()
        )

    def test_import_saves_the_column_mapping(self):
        mapping = {'transaction_id': 'TXN', 'amount': 'AMOUNT', 'date': 'DATE'}
        response = self._import([self._row('MP-API-MAP', 50000)], mapping=mapping)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            AppSetting.objects.get(key='csv_mapping').value,
            mapping,
        )

    def test_statement_import_requires_staff(self):
        response = self.anon.post(
            '/api/statements/imports/',
            {'rows': [self._row('MP-API-NOAUTH', 100000)]},
            format='json',
        )
        self.assertIn(response.status_code, (401, 403))

    def test_public_receipt_requires_the_registered_phone(self):
        claim_id = self._claim().data['id']
        self._import([self._row('MP-API-1', 100000)])
        receipt = Receipt.objects.get(payment__reference='MP-API-1')

        wrong = self.anon.get(
            f'/api/public/receipts/{receipt.receipt_no}/', {'phone': '0700000000'}
        )
        self.assertEqual(wrong.status_code, 404)

        right = self.anon.get(
            f'/api/public/receipts/{receipt.receipt_no}/', {'phone': '0777123456'}
        )
        self.assertEqual(right.status_code, 200, right.data)
        self.assertEqual(right.data['student']['student_no'], 'BFS-2026-1001')
        self.assertEqual(right.data['payment']['reference'], 'MP-API-1')
        self.assertEqual(PaymentClaim.objects.get(id=claim_id).status, 'CONFIRMED')

    # --- roles and manual payments -------------------------------------

    def test_accountant_cannot_change_settings_but_admin_can(self):
        denied = self.staff.put(
            '/api/settings/not_found_days/', {'value': 5}, format='json'
        )
        self.assertEqual(denied.status_code, 403)

        allowed = self.admin_client.put(
            '/api/settings/not_found_days/', {'value': 5}, format='json'
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(AppSetting.objects.get(key='not_found_days').value, 5)
        self.assertTrue(AuditLog.objects.filter(action='SETTINGS_CHANGE').exists())

    def test_manual_lipa_payment_is_blocked_and_bank_payment_requires_a_reason(self):
        lipa = self.admin_client.post(
            '/api/payments/',
            {
                'student': self.student.id,
                'amount': 100000,
                'method': 'Lipa Namba',
                'date': '2026-02-01',
                'reference': 'MP-MANUAL-LIPA',
            },
            format='json',
        )
        self.assertEqual(lipa.status_code, 400, lipa.data)
        self.assertEqual(lipa.data['error'], 'statement_required')

        no_reason = self.admin_client.post(
            '/api/payments/',
            {
                'student': self.student.id,
                'amount': 100000,
                'method': 'Bank',
                'date': '2026-02-01',
                'reference': 'MP-MANUAL-BANK',
            },
            format='json',
        )
        self.assertEqual(no_reason.status_code, 400, no_reason.data)
        self.assertEqual(no_reason.data['error'], 'reason_required')

        bank = self.admin_client.post(
            '/api/payments/',
            {
                'student': self.student.id,
                'amount': 100000,
                'method': 'Bank',
                'date': '2026-02-01',
                'reference': 'MP-MANUAL-BANK',
                'notes': 'Bank slip verified at the office',
            },
            format='json',
        )
        self.assertEqual(bank.status_code, 201, bank.data)
        self.assertTrue(Receipt.objects.filter(payment__reference='MP-MANUAL-BANK').exists())
        self.assertTrue(
            Allocation.objects.filter(payment__reference='MP-MANUAL-BANK').exists()
        )

    def test_accountant_cannot_record_a_manual_payment(self):
        response = self.staff.post(
            '/api/payments/',
            {
                'student': self.student.id,
                'amount': 100000,
                'method': 'Bank',
                'date': '2026-02-01',
                'reference': 'MP-STAFF-BANK',
                'notes': 'Should be admin only',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_apply_a_credit_with_a_reason(self):
        self.assertEqual(self._claim().status_code, 201)
        self._import([self._row('MP-API-1', 100000)])
        payment = Payment.objects.get(reference='MP-API-1')
        credit = Credit.objects.create(
            id='credit-api',
            student=self.student,
            payment=payment,
            amount=50000,
            remaining_amount=50000,
            reason='Overpayment held as credit',
        )

        denied = self.staff.post(
            f'/api/credits/{credit.id}/apply/',
            {
                'target_fee_line_id': self._line('fg-sch', 'fi-sch'),
                'amount': 50000,
                'reason': 'Apply credit to tuition',
            },
            format='json',
        )
        self.assertEqual(denied.status_code, 403)

        applied = self.admin_client.post(
            f'/api/credits/{credit.id}/apply/',
            {
                'target_fee_line_id': self._line('fg-sch', 'fi-sch'),
                'amount': 50000,
                'reason': 'Apply credit to tuition',
            },
            format='json',
        )
        self.assertEqual(applied.status_code, 201, applied.data)
        credit.refresh_from_db()
        self.assertEqual(credit.remaining_amount, Decimal('0.00'))
        self.assertTrue(
            Allocation.objects.filter(
                payment=payment,
                student=self.student,
                fee_line_id=self._line('fg-sch', 'fi-sch'),
                source='CREDIT',
                amount=Decimal('50000.00'),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action='CREDIT_APPLY', payment=payment).exists()
        )

    def test_reallocation_requires_an_admin_and_a_reason(self):
        self.assertEqual(self._claim().status_code, 201)
        self._import([self._row('MP-API-1', 100000)])
        payment = Payment.objects.get(reference='MP-API-1')

        denied = self.staff.post(
            '/api/allocations/reallocate/',
            {
                'payment_id': payment.id,
                'target_fee_line_id': self._line('fg-tra', 'fi-tra'),
                'amount': 50000,
                'reason': 'Parent asked for bus fees',
            },
            format='json',
        )
        self.assertEqual(denied.status_code, 403)

        no_reason = self.admin_client.post(
            '/api/allocations/reallocate/',
            {
                'payment_id': payment.id,
                'target_fee_line_id': self._line('fg-tra', 'fi-tra'),
                'amount': 50000,
            },
            format='json',
        )
        self.assertEqual(no_reason.status_code, 400, no_reason.data)

        moved = self.admin_client.post(
            '/api/allocations/reallocate/',
            {
                'payment_id': payment.id,
                'target_fee_line_id': self._line('fg-tra', 'fi-tra'),
                'amount': 50000,
                'reason': 'Parent asked for bus fees',
            },
            format='json',
        )
        self.assertEqual(moved.status_code, 201, moved.data)
        self.assertTrue(
            Allocation.objects.filter(
                payment=payment,
                fee_line_id=self._line('fg-tra', 'fi-tra'),
                reversed=False,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action='REALLOCATE', payment=payment).exists()
        )
