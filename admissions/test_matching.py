from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from .services.matching import (
    find_not_found,
    is_duplicate_transaction,
    match_rows,
    phones_for_parent,
    suggest_students,
)


def student(student_id, app_no, parent, emergency=''):
    return {
        'id': student_id,
        'application_no': app_no,
        'parent_phone': parent,
        'emergency_contact_phone': emergency,
    }


def claim(claim_id, student_id, amount, txn, payer, status='PENDING'):
    return {
        'id': claim_id,
        'student_id': student_id,
        'amount': Decimal(amount),
        'transaction_id': txn,
        'payer_phone': payer,
        'status': status,
    }


def row(txn, amount, phone, direction='IN'):
    return {
        'transaction_id': txn,
        'amount': Decimal(amount),
        'sender_phone': phone,
        'direction': direction,
    }


class MatchingServiceTests(SimpleTestCase):
    def setUp(self):
        # Two children of Bi Mwanaisha Ali, plus a separate parent.
        self.students = [
            student('s1', 'BFS-2026-1001', '0777123456', '0777111222'),
            student('s2', 'BFS-2026-1013', '0777123456', '0777222333'),
            student('s3', 'BFS-2026-1002', '0682987654', '0777333444'),
        ]
        self.claims = [
            claim('c1', 's1', '100000', 'T1', '0777123456'),
            claim('c2', 's2', '200000', 'T2', '0777123456'),
            claim('c3', 's3', '100000', 'T3', '0682987654'),
            claim('c4', 's1', '100000', 'T4', '0777123456'),
            claim('c5', 's1', '50000', '', '0777123456'),
        ]

    def test_parent_with_two_children_shares_phones(self):
        phones = phones_for_parent(self.students[0], self.students, self.claims)
        self.assertEqual(
            phones,
            ['0777111222', '0777123456', '0777222333'],
        )

    def test_exact_rows_confirm_both_children(self):
        result = match_rows(
            [
                row('T1', '100000', '0777123456'),
                row('T2', '200000', '0777123456'),
            ],
            self.claims,
            self.students,
        )
        self.assertEqual(result.exact_count, 2)
        self.assertEqual(result.needs_review_count, 0)
        statuses = {c.claim_id: c.status for c in result.claims}
        self.assertEqual(statuses['c1'], 'MATCHED')
        self.assertEqual(statuses['c2'], 'MATCHED')

    def test_phone_mismatch_goes_to_review_with_a_reason(self):
        result = match_rows(
            [row('T3', '100000', '0711111111')],
            self.claims,
            self.students,
        )
        self.assertEqual(result.needs_review_count, 1)
        match = next(r for r in result.rows if r.claim_id == 'c3')
        self.assertEqual(match.status, 'MATCHED')
        self.assertEqual(match.match_type, 'PHONE_MISMATCH')
        self.assertIn('does not match', match.reason)
        self.assertEqual(next(c for c in result.claims if c.claim_id == 'c3').status, 'NEEDS_REVIEW')

    def test_amount_mismatch_goes_to_review_with_a_reason(self):
        result = match_rows(
            [row('T4', '90000', '0777123456')],
            self.claims,
            self.students,
        )
        match = next(r for r in result.rows if r.claim_id == 'c4')
        self.assertEqual(match.match_type, 'AMOUNT_MISMATCH')
        self.assertIn('Amount differs', match.reason)
        self.assertIn('statement 90,000.00', match.reason)

    def test_claim_without_transaction_id_needs_review(self):
        result = match_rows([], self.claims, self.students)
        c5 = next(c for c in result.claims if c.claim_id == 'c5')
        self.assertEqual(c5.status, 'NEEDS_REVIEW')
        self.assertIn('no transaction ID', c5.reason)

    def test_unclaimed_row_suggests_student_by_emergency_phone(self):
        suggestions = suggest_students(
            row('T9', '150000', '0777111222'),
            self.students,
            self.claims,
        )
        self.assertEqual([s.student['id'] for s in suggestions], ['s1'])
        self.assertEqual(suggestions[0].reason, 'Emergency contact')

    def test_unclaimed_row_with_shared_parent_phone_suggests_both_children(self):
        suggestions = suggest_students(
            row('T9', '150000', '0777123456'),
            self.students,
            self.claims,
        )
        self.assertEqual(
            [s.student['application_no'] for s in suggestions],
            ['BFS-2026-1001', 'BFS-2026-1013'],
        )

    def test_unknown_phone_has_no_suggestion(self):
        self.assertEqual(
            suggest_students(row('T9', '150000', '0700000000'), self.students, self.claims),
            [],
        )

    def test_duplicate_and_invalid_rows_are_skipped(self):
        result = match_rows(
            [
                row('T1', '100000', '0777123456'),
                row('T1', '100000', '0777123456'),
                row('T8', '90000', '0777123456', direction='OUT'),
                row('', '90000', '0777123456'),
            ],
            self.claims,
            self.students,
            existing_transaction_ids=['T0'],
        )
        self.assertEqual(result.exact_count, 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.skipped_count, 2)

    def test_reimporting_the_same_statement_marks_rows_duplicate(self):
        first = match_rows(
            [row('T1', '100000', '0777123456')],
            self.claims,
            self.students,
        )
        self.assertEqual(first.exact_count, 1)

        second = match_rows(
            [row('T1', '100000', '0777123456')],
            self.claims,
            self.students,
            existing_transaction_ids=['T1'],
        )
        self.assertEqual(second.exact_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertEqual(second.rows[0].status, 'DUPLICATE')

    def test_is_duplicate_checks_payments_rows_and_claims(self):
        self.assertTrue(
            is_duplicate_transaction(
                ' t1 ',
                payment_references=['T1'],
                statement_transactions=[],
                claim_transactions=[],
            )
        )
        self.assertTrue(
            is_duplicate_transaction('T2', statement_transactions=['t2'])
        )
        self.assertTrue(
            is_duplicate_transaction('T3', claim_transactions=['t3'])
        )
        self.assertFalse(is_duplicate_transaction('', payment_references=['T1']))
        self.assertFalse(is_duplicate_transaction('T9', payment_references=['T1']))

    def test_not_found_after_configured_days_and_restore(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc)
        claims = [
            {**claim('old', 's1', '100000', 'OLD', '0777123456'), 'submitted_at': now - timedelta(days=4)},
            {**claim('new', 's1', '100000', 'NEW', '0777123456'), 'submitted_at': now - timedelta(days=1)},
            {**claim('found', 's1', '100000', 'FOUND', '0777123456'), 'submitted_at': now - timedelta(days=5)},
            {**claim('restore', 's1', '100000', 'RESTORE', '0777123456', status='NOT_FOUND'), 'submitted_at': now - timedelta(days=5)},
        ]
        statement_rows = [
            {'claim_id': 'found', 'status': 'MATCHED'},
            {'claim_id': 'restore', 'status': 'MATCHED'},
        ]

        result = find_not_found(claims, statement_rows, days=3, now=now)

        self.assertEqual([c['id'] for c in result.not_found], ['old'])
        self.assertEqual([c['id'] for c in result.restored], ['restore'])
