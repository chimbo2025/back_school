from django.db import models
from django.core.validators import MinValueValidator


class FeeItem(models.Model):
    id = models.CharField(max_length=50, primary_key=True)
    label = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        db_table = 'fee_items'

    def __str__(self):
        return f"{self.label} - {self.amount}"


class FeeGroup(models.Model):
    id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=100)
    items = models.ManyToManyField(FeeItem, related_name='fee_groups', blank=True)

    class Meta:
        db_table = 'fee_groups'

    def __str__(self):
        return self.name


class Grade(models.Model):
    id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    age_range = models.CharField(max_length=50, blank=True, null=True)
    fee_groups = models.ManyToManyField(FeeGroup, related_name='grades', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'grades'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_total_fee(self):
        total = 0
        for group in self.fee_groups.all():
            for item in group.items.all():
                total += item.amount
        return total


class Student(models.Model):
    GENDER_CHOICES = [
        ('Male', 'Male'),
        ('Female', 'Female'),
    ]
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Paid', 'Paid'),
        ('Admitted', 'Admitted'),
    ]
    RELATIONSHIP_CHOICES = [
        ('Father', 'Father'),
        ('Mother', 'Mother'),
        ('Guardian', 'Guardian'),
        ('Grandparent', 'Grandparent'),
        ('Sibling', 'Sibling'),
        ('Other', 'Other'),
    ]

    id = models.CharField(max_length=50, primary_key=True)
    application_no = models.CharField(max_length=50, unique=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    dob = models.DateField()
    address = models.TextField()
    parent_name = models.CharField(max_length=200)
    parent_phone = models.CharField(max_length=20)
    parent_address = models.TextField()
    parent_relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES, default='Father')
    parent_national_id = models.CharField(max_length=50, blank=True, default='')
    emergency_contact_name = models.CharField(max_length=200, blank=True, default='')
    emergency_contact_phone = models.CharField(max_length=20, blank=True, default='')
    emergency_contact_relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES, default='Guardian')
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name='students')
    academic_year = models.CharField(max_length=20)
    previous_school = models.CharField(max_length=200, blank=True, null=True)
    additional_info = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'students'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.application_no} - {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        parts = [self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        parts.append(self.last_name)
        return ' '.join(parts)

    def get_expected_fees(self):
        return self.grade.get_total_fee()

    def get_total_paid(self):
        return self.payments.aggregate(total=models.Sum('amount'))['total'] or 0

    def get_outstanding(self):
        return self.get_expected_fees() - self.get_total_paid()


class Payment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ('M-Pesa', 'M-Pesa'),
        ('Mixx by Yas', 'Mixx by Yas'),
        ('Airtel Money', 'Airtel Money'),
        ('Bank', 'Bank'),
    ]

    id = models.CharField(max_length=50, primary_key=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    date = models.DateField()
    reference = models.CharField(max_length=100)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.student.application_no} - {self.amount} via {self.method}"