from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import FeeGroup, FeeItem, Grade, PaymentClaim, Student


class GradeFeeGroupUpdateTests(TestCase):
    """Regression tests for editing a grade's nested fee structure."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user('admin', password='x'))

        self.admission = FeeGroup.objects.create(id='fg-adm', name='ADMISSION FEE')
        self.term = FeeItem.objects.create(id='item-term', label='Tuition', amount=1200000)
        self.admission.items.add(self.term)

        self.grade = Grade.objects.create(id='grade-1', name='Grade 1')
        self.grade.fee_groups.add(self.admission)

    def test_adding_a_fee_group_keeps_existing_groups(self):
        response = self.client.patch(
            '/api/grades/grade-1/',
            {
                'fee_groups': [
                    {
                        'id': 'fg-adm',
                        'name': 'ADMISSION FEE',
                        'items': [
                            {'id': 'item-term', 'label': 'Tuition', 'amount': 1200000}
                        ],
                    },
                    {
                        'id': 'fg-other',
                        'name': 'OTHER FEE',
                        'items': [
                            {'id': 'item-other', 'label': 'Exam Fee', 'amount': 25000}
                        ],
                    },
                ]
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.grade.refresh_from_db()
        ids = set(self.grade.fee_groups.values_list('id', flat=True))
        self.assertEqual(ids, {'fg-adm', 'fg-other'})

        # The admin console refetches the list endpoint after saving; it must
        # contain the nested groups/items or the UI shows an empty breakdown.
        listed = self.client.get('/api/grades/')
        self.assertEqual(listed.status_code, 200)
        row = next(g for g in listed.data['results'] if g['id'] == 'grade-1')
        self.assertEqual(
            {fg['id'] for fg in row['fee_groups']},
            {'fg-adm', 'fg-other'},
        )

    def test_updating_an_item_amount_succeeds(self):
        response = self.client.patch(
            '/api/grades/grade-1/',
            {
                'fee_groups': [
                    {
                        'id': 'fg-adm',
                        'name': 'ADMISSION FEE',
                        'items': [
                            {'id': 'item-term', 'label': 'Tuition', 'amount': 1350000}
                        ],
                    }
                ]
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.term.refresh_from_db()
        self.assertEqual(float(self.term.amount), 1350000)

    def test_new_fee_group_without_an_id_gets_one(self):
        response = self.client.patch(
            '/api/grades/grade-1/',
            {
                'fee_groups': [
                    {
                        'id': 'fg-adm',
                        'name': 'ADMISSION FEE',
                        'items': [
                            {'id': 'item-term', 'label': 'Tuition', 'amount': 1200000}
                        ],
                    },
                    {
                        'name': 'TRANSPORT FEE',
                        'items': [{'label': 'Bus', 'amount': 600000}],
                    },
                ]
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.grade.refresh_from_db()
        self.assertEqual(self.grade.fee_groups.count(), 2)
        self.assertIn('TRANSPORT FEE', self.grade.fee_groups.values_list('name', flat=True))


class PublicPaymentFlowTests(TestCase):
    """Parents are not logged in, so public money submissions must be claims."""

    def setUp(self):
        grade = Grade.objects.create(id='grade-pub', name='Grade 1')
        self.student = Student.objects.create(
            id='stu-pub',
            application_no='APP-PUB-1',
            first_name='Asha',
            last_name='Juma',
            gender='Female',
            dob='2015-01-01',
            address='Zanzibar',
            parent_name='Fatma',
            parent_phone='0777123456',
            parent_address='Zanzibar',
            parent_relationship='Mother',
            grade=grade,
            academic_year='2026',
        )
        self.client = APIClient()

    def test_anonymous_can_submit_a_payment_claim(self):
        response = self.client.post(
            '/api/claims/',
            {
                'student': self.student.id,
                'amount': 150000,
                'transaction_id': 'MP260101001',
                'payment_date': '2026-01-01',
                'payer_phone': '0777123456',
                'method': 'Lipa Namba',
                'notes': 'Online Admission Fee Payment',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        claim = PaymentClaim.objects.get(id=response.data['id'])
        self.assertEqual(claim.status, 'PENDING')
        self.assertEqual(claim.payer_phone, '0777123456')
        self.assertEqual(float(claim.amount), 150000)

    def test_anonymous_cannot_create_a_payment_directly(self):
        response = self.client.post(
            '/api/payments/',
            {
                'student': self.student.id,
                'amount': 150000,
                'method': 'Lipa Namba',
                'date': '2026-01-01',
                'reference': 'MP260101002',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 401)
