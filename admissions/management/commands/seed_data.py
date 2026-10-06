from django.core.management.base import BaseCommand
from admissions.models import Grade, FeeGroup, FeeItem, Student, Payment
from decimal import Decimal
from datetime import date


class Command(BaseCommand):
    help = 'Seed the database with initial data'

    def handle(self, *args, **options):
        self.stdout.write('Seeding data...')

        # Create Fee Items
        fee_items_data = {
            'item-1-1': {'label': 'Tuition & Learning Materials', 'amount': Decimal('1175000')},
            'item-1-2': {'label': 'School Bus Service (Annual)', 'amount': Decimal('600000')},
            'item-1-3': {'label': 'Registration & Student Kit', 'amount': Decimal('150000')},
            'item-2-1': {'label': 'Tuition & Digital Learning Lab', 'amount': Decimal('1250000')},
            'item-2-2': {'label': 'School Bus Service (Annual)', 'amount': Decimal('600000')},
            'item-2-3': {'label': 'Registration & Medical Care', 'amount': Decimal('150000')},
            'item-3-1': {'label': 'Tuition & Standard Academic Package', 'amount': Decimal('1350000')},
            'item-3-2': {'label': 'Science & Robotics Lab Practical', 'amount': Decimal('50000')},
            'item-3-3': {'label': 'School Bus Service (Annual)', 'amount': Decimal('650000')},
            'item-3-4': {'label': 'Registration & Uniform Set', 'amount': Decimal('150000')},
        }

        fee_items = {}
        for item_id, data in fee_items_data.items():
            item, created = FeeItem.objects.get_or_create(id=item_id, defaults=data)
            fee_items[item_id] = item
            if created:
                self.stdout.write(f'Created FeeItem: {item.label}')

        # Create Fee Groups
        fee_groups_data = {
            'fg-1': {'name': 'SCHOOL FEE', 'items': ['item-1-1']},
            'fg-2': {'name': 'TRANSPORT FEE', 'items': ['item-1-2']},
            'fg-3': {'name': 'ADMISSION FEE', 'items': ['item-1-3']},
            'fg-2-1': {'name': 'SCHOOL FEE', 'items': ['item-2-1']},
            'fg-2-2': {'name': 'TRANSPORT FEE', 'items': ['item-2-2']},
            'fg-2-3': {'name': 'ADMISSION FEE', 'items': ['item-2-3']},
            'fg-3-1': {'name': 'SCHOOL FEE', 'items': ['item-3-1', 'item-3-2']},
            'fg-3-2': {'name': 'TRANSPORT FEE', 'items': ['item-3-3']},
            'fg-3-3': {'name': 'ADMISSION FEE', 'items': ['item-3-4']},
        }

        fee_groups = {}
        for group_id, data in fee_groups_data.items():
            group, created = FeeGroup.objects.get_or_create(id=group_id, defaults={'name': data['name']})
            group.items.set([fee_items[item_id] for item_id in data['items']])
            fee_groups[group_id] = group
            if created:
                self.stdout.write(f'Created FeeGroup: {group.name}')

        # Create Grades
        grades_data = [
            {
                'id': 'grade-1',
                'name': 'Grade 1',
                'description': 'Lower Primary education focusing on literacy, numeracy, science, and creative arts.',
                'age_range': '6 - 7 years',
                'fee_groups': ['fg-1', 'fg-2', 'fg-3'],
            },
            {
                'id': 'grade-2',
                'name': 'Grade 2',
                'description': 'Primary foundation building with expanded English, Kiswahili, and ICT basics.',
                'age_range': '7 - 8 years',
                'fee_groups': ['fg-2-1', 'fg-2-2', 'fg-2-3'],
            },
            {
                'id': 'grade-3',
                'name': 'Grade 3',
                'description': 'Upper Primary preparatory stream with STEM robotics, environmental science, and sports.',
                'age_range': '8 - 9 years',
                'fee_groups': ['fg-3-1', 'fg-3-2', 'fg-3-3'],
            },
        ]

        for grade_data in grades_data:
            fee_group_ids = grade_data.pop('fee_groups')
            grade, created = Grade.objects.get_or_create(id=grade_data['id'], defaults=grade_data)
            grade.fee_groups.set([fee_groups[fg_id] for fg_id in fee_group_ids])
            if created:
                self.stdout.write(f'Created Grade: {grade.name}')

        # Create Students
        students_data = [
            {
                'id': 'stu-1',
                'application_no': 'BFS-2026-1001',
                'first_name': 'Amina',
                'middle_name': 'Hassan',
                'last_name': 'Juma',
                'gender': 'Female',
                'dob': '2019-03-14',
                'address': 'Mwanakwerekwe, Zanzibar',
                'parent_name': 'Bi Mwanaisha Ali',
                'parent_phone': '0777123456',
                'parent_address': 'Mwanakwerekwe Plot 42, Zanzibar',
                'parent_relationship': 'Mother',
                'parent_national_id': '1975123456789',
                'emergency_contact_name': 'Hassan Ali',
                'emergency_contact_phone': '0777111222',
                'emergency_contact_relationship': 'Father',
                'grade_id': 'grade-1',
                'academic_year': '2026/2027',
                'previous_school': 'Al-Hikma Nursery',
                'notes': 'Passionate about drawing and reading Kiswahili stories.',
                'status': 'Admitted',
            },
            {
                'id': 'stu-2',
                'application_no': 'BFS-2026-1002',
                'first_name': 'Salim',
                'middle_name': 'Khamis',
                'last_name': 'Ali',
                'gender': 'Male',
                'dob': '2018-07-22',
                'address': 'Chukwani, Zanzibar',
                'parent_name': 'Mzee Haji Omar',
                'parent_phone': '0682987654',
                'parent_address': 'Chukwani Beach Road, Unguja',
                'parent_relationship': 'Father',
                'parent_national_id': '1968051234567',
                'emergency_contact_name': 'Bi Fatuma Omar',
                'emergency_contact_phone': '0777333444',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-2',
                'academic_year': '2026/2027',
                'previous_school': 'Kiembesamaki Primary',
                'notes': 'Requires front-row seating for slight vision focus.',
                'status': 'Admitted',
            },
            {
                'id': 'stu-3',
                'application_no': 'BFS-2026-1003',
                'first_name': 'Zuhura',
                'middle_name': 'Omar',
                'last_name': 'Haji',
                'gender': 'Female',
                'dob': '2017-11-05',
                'address': 'Stone Town (Unguja), Zanzibar',
                'parent_name': 'Omar Haji Mwadini',
                'parent_phone': '0778407818',
                'parent_address': 'Hurumzi Street, Stone Town',
                'parent_relationship': 'Father',
                'parent_national_id': '1972034567890',
                'emergency_contact_name': 'Bi Mariam Mwadini',
                'emergency_contact_phone': '0778555666',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-3',
                'academic_year': '2026/2027',
                'previous_school': 'St. Monica English Medium',
                'notes': 'Skilled in mathematics and school debate.',
                'status': 'Paid',
            },
            {
                'id': 'stu-4',
                'application_no': 'BFS-2026-1004',
                'first_name': 'Faraji',
                'middle_name': 'Mussa',
                'last_name': 'Said',
                'gender': 'Male',
                'dob': '2019-05-18',
                'address': 'Mwanakwerekwe, Zanzibar',
                'parent_name': 'Mussa Said Khatib',
                'parent_phone': '0773554433',
                'parent_address': 'Mwanakwerekwe Darajani',
                'parent_relationship': 'Father',
                'parent_national_id': '1980012345678',
                'emergency_contact_name': 'Bi Aisha Khatib',
                'emergency_contact_phone': '0773777888',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-1',
                'academic_year': '2026/2027',
                'previous_school': 'Kiembesamaki Nursery',
                'notes': 'Applicant passed preliminary entrance interview.',
                'status': 'Paid',
            },
            {
                'id': 'stu-5',
                'application_no': 'BFS-2026-1005',
                'first_name': 'Neema',
                'middle_name': 'Joseph',
                'last_name': 'Mrema',
                'gender': 'Female',
                'dob': '2018-09-10',
                'address': 'Mbweni, Zanzibar',
                'parent_name': 'Joseph Mrema',
                'parent_phone': '0754889900',
                'parent_address': 'Mbweni Ruins Area',
                'parent_relationship': 'Father',
                'parent_national_id': 'ZAN456789012',
                'emergency_contact_name': 'Bi Grace Mrema',
                'emergency_contact_phone': '0754999000',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-2',
                'academic_year': '2026/2027',
                'previous_school': 'Zanzibar Feza International',
                'notes': 'High academic assessment score.',
                'status': 'Admitted',
            },
            {
                'id': 'stu-6',
                'application_no': 'BFS-2026-1006',
                'first_name': 'Rashid',
                'middle_name': 'Nassor',
                'last_name': 'Juma',
                'gender': 'Male',
                'dob': '2017-04-03',
                'address': 'Mazizini, Zanzibar',
                'parent_name': 'Nassor Juma Vuai',
                'parent_phone': '0776223344',
                'parent_address': 'Mazizini Near Airport',
                'parent_relationship': 'Father',
                'parent_national_id': '1970098765432',
                'emergency_contact_name': 'Bi Zainab Vuai',
                'emergency_contact_phone': '0776111222',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-3',
                'academic_year': '2026/2027',
                'previous_school': 'Sos Kindergarten Zanzibar',
                'notes': 'Interested in football and computer coding.',
                'status': 'Pending',
            },
            {
                'id': 'stu-7',
                'application_no': 'BFS-2026-1007',
                'first_name': 'Fatma',
                'middle_name': 'Kassim',
                'last_name': 'Bakari',
                'gender': 'Female',
                'dob': '2019-01-30',
                'address': 'Stone Town (Unguja), Zanzibar',
                'parent_name': 'Bi Mariam Bakari',
                'parent_phone': '0779112233',
                'parent_address': 'Shangani Street, Stone Town',
                'parent_relationship': 'Mother',
                'parent_national_id': 'ZAN789012345',
                'emergency_contact_name': 'Kassim Bakari',
                'emergency_contact_phone': '0779333444',
                'emergency_contact_relationship': 'Father',
                'grade_id': 'grade-1',
                'academic_year': '2026/2027',
                'previous_school': 'Al-Hikma Nursery',
                'notes': 'Interview scheduled for next Monday.',
                'status': 'Pending',
            },
            {
                'id': 'stu-8',
                'application_no': 'BFS-2026-1008',
                'first_name': 'Baraka',
                'middle_name': 'Hamad',
                'last_name': 'Vuai',
                'gender': 'Male',
                'dob': '2018-08-12',
                'address': 'Chukwani, Zanzibar',
                'parent_name': 'Hamad Vuai Machano',
                'parent_phone': '0774667788',
                'parent_address': 'Chukwani Kiembe Samaki',
                'parent_relationship': 'Father',
                'parent_national_id': '1978112233445',
                'emergency_contact_name': 'Bi Khadija Machano',
                'emergency_contact_phone': '0774555666',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-2',
                'academic_year': '2026/2027',
                'previous_school': 'Kiembesamaki Primary',
                'notes': 'Submitted all birth certificate copies.',
                'status': 'Paid',
            },
            {
                'id': 'stu-9',
                'application_no': 'BFS-2026-1009',
                'first_name': 'Khadija',
                'middle_name': 'Said',
                'last_name': 'Khamis',
                'gender': 'Female',
                'dob': '2017-06-25',
                'address': 'Mwanakwerekwe, Zanzibar',
                'parent_name': 'Said Khamis Mzee',
                'parent_phone': '0772334455',
                'parent_address': 'Mwanakwerekwe Njia Panda',
                'parent_relationship': 'Father',
                'parent_national_id': '1965123456789',
                'emergency_contact_name': 'Bi Amina Mzee',
                'emergency_contact_phone': '0772111222',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-3',
                'academic_year': '2026/2027',
                'previous_school': 'Mwembeladu Primary',
                'notes': 'Special interest in science club.',
                'status': 'Admitted',
            },
            {
                'id': 'stu-10',
                'application_no': 'BFS-2026-1010',
                'first_name': 'Yussuf',
                'middle_name': 'Abdalla',
                'last_name': 'Machano',
                'gender': 'Male',
                'dob': '2019-10-14',
                'address': 'Chukwani, Zanzibar',
                'parent_name': 'Bi Asha Machano',
                'parent_phone': '0778998877',
                'parent_address': 'Chukwani Police Line',
                'parent_relationship': 'Mother',
                'parent_national_id': 'ZAN234567890',
                'emergency_contact_name': 'Abdalla Machano',
                'emergency_contact_phone': '0778888999',
                'emergency_contact_relationship': 'Father',
                'grade_id': 'grade-1',
                'academic_year': '2026/2027',
                'previous_school': 'Little Angels Nursery',
                'notes': 'Awaiting parent interview confirmation.',
                'status': 'Pending',
            },
            {
                'id': 'stu-11',
                'application_no': 'BFS-2026-1011',
                'first_name': 'Mariam',
                'middle_name': 'Suleiman',
                'last_name': 'Saleh',
                'gender': 'Female',
                'dob': '2018-02-19',
                'address': 'Stone Town (Unguja), Zanzibar',
                'parent_name': 'Suleiman Saleh',
                'parent_phone': '0771556677',
                'parent_address': 'Vuga Road, Stone Town',
                'parent_relationship': 'Father',
                'parent_national_id': '1973045678901',
                'emergency_contact_name': 'Bi Halima Saleh',
                'emergency_contact_phone': '0771777888',
                'emergency_contact_relationship': 'Mother',
                'grade_id': 'grade-2',
                'academic_year': '2026/2027',
                'previous_school': 'Al-Hikma Nursery',
                'notes': 'Full payment made via bank.',
                'status': 'Admitted',
            },
            {
                'id': 'stu-12',
                'application_no': 'BFS-2026-1012',
                'first_name': 'Daudi',
                'middle_name': 'Ally',
                'last_name': 'Mohamed',
                'gender': 'Male',
                'dob': '2017-12-01',
                'address': 'Mbweni, Zanzibar',
                'parent_name': 'Bi Fatma Mohamed',
                'parent_phone': '0685443322',
                'parent_address': 'Mbweni Sungura Lane',
                'parent_relationship': 'Mother',
                'parent_national_id': '1976078901234',
                'emergency_contact_name': 'Ally Mohamed',
                'emergency_contact_phone': '0685333444',
                'emergency_contact_relationship': 'Father',
                'grade_id': 'grade-3',
                'academic_year': '2026/2027',
                'previous_school': 'Zanzibar Feza International',
                'notes': 'Transfer student from Dar es Salaam branch.',
                'status': 'Admitted',
            },
        ]

        students = {}
        for student_data in students_data:
            grade_id = student_data.pop('grade_id')
            grade = Grade.objects.get(id=grade_id)
            student, created = Student.objects.update_or_create(
                id=student_data['id'],
                defaults={**student_data, 'grade': grade}
            )
            students[student_data['id']] = student
            if created:
                self.stdout.write(f'Created Student: {student.full_name}')

        # Create Payments
        payments_data = [
            {'id': 'pay-1', 'student_id': 'stu-1', 'amount': Decimal('150000'), 'method': 'M-Pesa', 'date': '2026-01-10', 'reference': 'MP260110091', 'notes': 'Admission & Registration Fee'},
            {'id': 'pay-2', 'student_id': 'stu-1', 'amount': Decimal('1175000'), 'method': 'Mixx by Yas', 'date': '2026-01-11', 'reference': 'MX260111452', 'notes': 'Annual School Tuition Fee'},
            {'id': 'pay-3', 'student_id': 'stu-1', 'amount': Decimal('600000'), 'method': 'Bank', 'date': '2026-01-12', 'reference': 'CRDB-994821', 'notes': 'Annual Bus Transport Fee'},
            {'id': 'pay-4', 'student_id': 'stu-2', 'amount': Decimal('150000'), 'method': 'Mixx by Yas', 'date': '2026-01-12', 'reference': 'MX260112831', 'notes': 'Admission Fee'},
            {'id': 'pay-5', 'student_id': 'stu-2', 'amount': Decimal('1250000'), 'method': 'Bank', 'date': '2026-01-14', 'reference': 'NMB-778219', 'notes': 'Tuition Term 1 & 2'},
            {'id': 'pay-6', 'student_id': 'stu-2', 'amount': Decimal('600000'), 'method': 'M-Pesa', 'date': '2026-01-15', 'reference': 'MP260115024', 'notes': 'Transport Fee Full'},
            {'id': 'pay-7', 'student_id': 'stu-3', 'amount': Decimal('150000'), 'method': 'M-Pesa', 'date': '2026-01-15', 'reference': 'MP260115993', 'notes': 'Admission Fee'},
            {'id': 'pay-8', 'student_id': 'stu-3', 'amount': Decimal('1400000'), 'method': 'Airtel Money', 'date': '2026-01-16', 'reference': 'AM260116812', 'notes': 'Tuition and Robotics Fee'},
            {'id': 'pay-9', 'student_id': 'stu-4', 'amount': Decimal('150000'), 'method': 'Mixx by Yas', 'date': '2026-01-18', 'reference': 'MX260118331', 'notes': 'Admission Fee'},
            {'id': 'pay-10', 'student_id': 'stu-4', 'amount': Decimal('1175000'), 'method': 'Bank', 'date': '2026-01-19', 'reference': 'PBZ-551029', 'notes': 'Tuition Paid via People\'s Bank of Zanzibar'},
            {'id': 'pay-11', 'student_id': 'stu-5', 'amount': Decimal('150000'), 'method': 'Bank', 'date': '2026-01-20', 'reference': 'CRDB-104928', 'notes': 'Admission Fee'},
            {'id': 'pay-12', 'student_id': 'stu-5', 'amount': Decimal('1850000'), 'method': 'Bank', 'date': '2026-01-21', 'reference': 'CRDB-104992', 'notes': 'Tuition + Transport Full Settlement'},
            {'id': 'pay-13', 'student_id': 'stu-8', 'amount': Decimal('150000'), 'method': 'M-Pesa', 'date': '2026-01-28', 'reference': 'MP260128919', 'notes': 'Admission Fee'},
            {'id': 'pay-14', 'student_id': 'stu-8', 'amount': Decimal('800000'), 'method': 'Mixx by Yas', 'date': '2026-01-29', 'reference': 'MX260129772', 'notes': 'Tuition First Installment'},
            {'id': 'pay-15', 'student_id': 'stu-9', 'amount': Decimal('150000'), 'method': 'Airtel Money', 'date': '2026-02-01', 'reference': 'AM260201283', 'notes': 'Admission Fee'},
            {'id': 'pay-16', 'student_id': 'stu-9', 'amount': Decimal('1400000'), 'method': 'M-Pesa', 'date': '2026-02-02', 'reference': 'MP260202991', 'notes': 'Tuition & Lab Fee'},
            {'id': 'pay-17', 'student_id': 'stu-9', 'amount': Decimal('650000'), 'method': 'Bank', 'date': '2026-02-03', 'reference': 'NMB-993821', 'notes': 'Transport Fee Full'},
            {'id': 'pay-18', 'student_id': 'stu-11', 'amount': Decimal('2000000'), 'method': 'Bank', 'date': '2026-02-08', 'reference': 'CRDB-556102', 'notes': 'Single Lump Sum Payment for Full Academic Year'},
            {'id': 'pay-19', 'student_id': 'stu-12', 'amount': Decimal('150000'), 'method': 'Mixx by Yas', 'date': '2026-02-10', 'reference': 'MX260210419', 'notes': 'Admission Fee'},
            {'id': 'pay-20', 'student_id': 'stu-12', 'amount': Decimal('2050000'), 'method': 'Bank', 'date': '2026-02-11', 'reference': 'PBZ-889912', 'notes': 'Full Tuition + Transport + Lab Package'},
        ]

        for payment_data in payments_data:
            student_id = payment_data.pop('student_id')
            student = students[student_id]
            payment, created = Payment.objects.get_or_create(
                id=payment_data['id'],
                defaults={**payment_data, 'student': student}
            )
            if created:
                self.stdout.write(f'Created Payment: {payment.reference} for {student.full_name}')

        self.stdout.write(self.style.SUCCESS('Successfully seeded all data!'))