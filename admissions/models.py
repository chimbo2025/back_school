from django.db import models
from django.core.validators import MinValueValidator

from .normalization import normalize_phone


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
    # Canonical 0XXXXXXXXX form used by matching. ``parent_phone`` keeps the
    # number exactly as the parent typed it so printed letters are unchanged.
    parent_phone_normalized = models.CharField(max_length=20, blank=True, default='', db_index=True)
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

    def save(self, *args, **kwargs):
        self.parent_phone = (self.parent_phone or '').strip()
        self.parent_phone_normalized = normalize_phone(self.parent_phone)
        super().save(*args, **kwargs)

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

    def get_available_credit(self):
        return self.credits.aggregate(total=models.Sum('remaining_amount'))['total'] or 0


class Payment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ('M-Pesa', 'M-Pesa'),
        ('Mixx by Yas', 'Mixx by Yas'),
        ('Airtel Money', 'Airtel Money'),
        ('Bank', 'Bank'),
        ('Lipa Namba', 'Lipa Namba'),
    ]
    SOURCE_CHOICES = [
        ('MANUAL', 'Manual'),
        ('CLAIM', 'Parent claim'),
        ('PROVIDER', 'Payment provider'),
    ]

    id = models.CharField(max_length=50, primary_key=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    date = models.DateField()
    reference = models.CharField(max_length=100, unique=True)
    notes = models.TextField(blank=True, null=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='MANUAL')
    statement_row = models.ForeignKey(
        'StatementRow', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments'
    )
    # Provider-ready fields: a future callback can create a Payment with
    # source=PROVIDER, external_id for idempotency and the raw payload for
    # later reconciliation. Allocation logic only ever sees student + amount.
    provider = models.CharField(max_length=50, blank=True, default='')
    external_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.student.application_no} - {self.amount} via {self.method}"


class PaymentClaim(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('NEEDS_REVIEW', 'Needs review'),
        ('CONFIRMED', 'Confirmed'),
        ('REJECTED', 'Rejected'),
        ('NOT_FOUND', 'Not found'),
    ]

    id = models.CharField(max_length=50, primary_key=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='claims')
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    transaction_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    payment_date = models.DateField()
    payer_phone = models.CharField(max_length=20)
    payer_phone_normalized = models.CharField(max_length=20, blank=True, default='', db_index=True)
    method = models.CharField(max_length=20, blank=True, default='')
    screenshot = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    # The parent's optional request: [{fee_line_id, fee_group_name,
    # fee_item_label, amount}]. It is a request only; the server applies it
    # after the statement row is confirmed, capped at the statement amount.
    requested_allocations = models.JSONField(default=list, blank=True)
    # Filled in on confirmation. The statement amount always wins; variance is
    # statement_amount - amount and is shown to the accountant.
    statement_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    variance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    match_reason = models.TextField(blank=True, default='')
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=100, blank=True, default='')
    rejection_reason = models.TextField(blank=True, default='')
    matched_payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='claims'
    )

    class Meta:
        db_table = 'payment_claims'
        ordering = ['-submitted_at']
        constraints = [
            # A rejected claim may be corrected and resubmitted. Every other
            # status must have a unique non-empty transaction ID.
            models.UniqueConstraint(
                fields=['transaction_id'],
                condition=~models.Q(status='REJECTED') & ~models.Q(transaction_id=''),
                name='unique_active_claim_txn',
            )
        ]

    def save(self, *args, **kwargs):
        self.transaction_id = (self.transaction_id or '').strip().upper()
        self.payer_phone = (self.payer_phone or '').strip()
        self.payer_phone_normalized = normalize_phone(self.payer_phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Claim {self.id} - {self.amount} ({self.status})"


class StatementImport(models.Model):
    STATUS_CHOICES = [
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    id = models.CharField(max_length=50, primary_key=True)
    file_name = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, db_index=True)
    mapping = models.JSONField(default=dict, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.CharField(max_length=100, blank=True, default='')
    total_rows = models.IntegerField(default=0)
    imported_rows = models.IntegerField(default=0)
    skipped_rows = models.IntegerField(default=0)
    matched_claims = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='COMPLETED')

    class Meta:
        db_table = 'statement_imports'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.file_name} ({self.imported_rows}/{self.total_rows})"


class StatementRow(models.Model):
    DIRECTION_CHOICES = [
        ('IN', 'Incoming'),
        ('OUT', 'Outgoing'),
    ]
    STATUS_CHOICES = [
        ('UNCLAIMED', 'Unclaimed'),
        ('MATCHED', 'Matched'),
        ('ASSIGNED', 'Assigned'),
        ('DUPLICATE', 'Duplicate'),
        ('SKIPPED', 'Skipped'),
    ]

    id = models.CharField(max_length=50, primary_key=True)
    import_record = models.ForeignKey(
        StatementImport, on_delete=models.CASCADE, related_name='rows'
    )
    transaction_id = models.CharField(max_length=100, db_index=True)
    date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    sender_phone = models.CharField(max_length=30, blank=True, default='')
    sender_phone_normalized = models.CharField(max_length=20, blank=True, default='', db_index=True)
    sender_name = models.CharField(max_length=200, blank=True, default='')
    receiver_till = models.CharField(max_length=50, blank=True, default='')
    direction = models.CharField(max_length=5, choices=DIRECTION_CHOICES, default='IN')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='UNCLAIMED')
    claim = models.ForeignKey(
        PaymentClaim, on_delete=models.SET_NULL, null=True, blank=True, related_name='statement_rows'
    )
    student = models.ForeignKey(
        Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='statement_rows'
    )
    match_type = models.CharField(max_length=20, blank=True, default='')
    match_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'statement_rows'
        ordering = ['-date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['transaction_id'],
                condition=models.Q(status__in=['UNCLAIMED', 'MATCHED', 'ASSIGNED']),
                name='unique_active_statement_txn',
            )
        ]

    def save(self, *args, **kwargs):
        self.transaction_id = (self.transaction_id or '').strip().upper()
        self.sender_phone = (self.sender_phone or '').strip()
        self.sender_phone_normalized = normalize_phone(self.sender_phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_id} - {self.amount} ({self.status})"


class Allocation(models.Model):
    SOURCE_CHOICES = [
        ('PRIORITY', 'Admin priority order'),
        ('SPLIT', 'Parent requested split'),
        ('REALLOCATION', 'Admin reallocation'),
        ('CREDIT', 'Credit applied'),
        ('MANUAL', 'Manual'),
    ]

    id = models.CharField(max_length=60, primary_key=True)
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='allocations')
    fee_line_id = models.CharField(max_length=120)
    fee_group_name = models.CharField(max_length=100, blank=True, default='')
    fee_item_label = models.CharField(max_length=200, blank=True, default='')
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='PRIORITY')
    reason = models.TextField(blank=True, default='')
    reversed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=100, blank=True, default='system')

    class Meta:
        db_table = 'allocations'
        ordering = ['created_at']

    def __str__(self):
        return f"{self.fee_item_label} - {self.amount}"


class Credit(models.Model):
    """Money received above what the student owed.

    A credit is created when a confirmed statement amount is larger than the
    outstanding balance. It is persisted so it can be shown on the parent
    statement and applied later by an admin with a reason.
    """

    id = models.CharField(max_length=60, primary_key=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='credits')
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='credits'
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    remaining_amount = models.DecimalField(
        max_digits=15, decimal_places=2, validators=[MinValueValidator(0)]
    )
    reason = models.TextField(blank=True, default='')
    created_by = models.CharField(max_length=100, blank=True, default='system')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'credits'
        ordering = ['-created_at']

    def __str__(self):
        return f"Credit {self.student.application_no} - {self.remaining_amount}/{self.amount}"


class Receipt(models.Model):
    """Snapshot of a confirmed payment for the parent's status page.

    The snapshot keeps the receipt readable even if a grade's fees change
    later. It is created at confirmation time by the allocation service.
    """

    id = models.CharField(max_length=60, primary_key=True)
    receipt_no = models.CharField(max_length=60, unique=True)
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name='receipt')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='receipts')
    snapshot = models.JSONField(default=dict, blank=True)
    issued_by = models.CharField(max_length=100, blank=True, default='system')
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'receipts'
        ordering = ['-issued_at']

    def __str__(self):
        return f"{self.receipt_no} - {self.student.application_no}"


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('ALLOCATE', 'Allocate'),
        ('REALLOCATE', 'Reallocate'),
        ('PAYMENT_CREATE', 'Create payment'),
        ('PAYMENT_DELETE', 'Delete payment'),
        ('CLAIM_SUBMIT', 'Submit claim'),
        ('CLAIM_APPROVE', 'Confirm claim'),
        ('CLAIM_REJECT', 'Reject claim'),
        ('CLAIM_NOT_FOUND', 'Claim not found'),
        ('PAYMENT_CONFIRM', 'Confirm payment'),
        ('CREDIT_CREATE', 'Create credit'),
        ('CREDIT_APPLY', 'Apply credit'),
        ('RECEIPT_ISSUE', 'Issue receipt'),
        ('CSV_IMPORT', 'Import statement'),
        ('ROW_ASSIGN', 'Assign statement row'),
        ('MANUAL_ADJUST', 'Manual adjustment'),
        ('SETTINGS_CHANGE', 'Change settings'),
    ]

    id = models.CharField(max_length=60, primary_key=True)
    student = models.ForeignKey(
        Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs'
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs'
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    performed_by = models.CharField(max_length=100)
    performed_at = models.DateTimeField(auto_now_add=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-performed_at']

    def __str__(self):
        return f"{self.action} by {self.performed_by} at {self.performed_at}"


class AppSetting(models.Model):
    key = models.CharField(max_length=50, primary_key=True)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'app_settings'

    def __str__(self):
        return self.key