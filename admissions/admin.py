from django.contrib import admin
from .models import (
    Grade, FeeGroup, FeeItem, Student, Payment, PaymentClaim,
    StatementRow, Credit, Receipt,
)


class FeeItemInline(admin.TabularInline):
    model = FeeGroup.items.through
    extra = 1


@admin.register(FeeItem)
class FeeItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'label', 'amount']
    search_fields = ['label']


@admin.register(FeeGroup)
class FeeGroupAdmin(admin.ModelAdmin):
    list_display = ['id', 'name']
    search_fields = ['name']
    inlines = [FeeItemInline]


class FeeGroupInline(admin.TabularInline):
    model = Grade.fee_groups.through
    extra = 1


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'description', 'age_range', 'get_total_fee']
    search_fields = ['name']
    inlines = [FeeGroupInline]

    def get_total_fee(self, obj):
        return obj.get_total_fee()
    get_total_fee.short_description = 'Total Fee'


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 1
    readonly_fields = ['created_at']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['application_no', 'full_name', 'grade', 'gender', 'status', 'academic_year', 'get_expected_fees', 'get_total_paid', 'get_outstanding', 'created_at']
    list_filter = ['grade', 'status', 'gender', 'academic_year']
    search_fields = ['application_no', 'first_name', 'last_name', 'parent_name', 'parent_phone']
    readonly_fields = ['application_no', 'created_at', 'updated_at']
    inlines = [PaymentInline]
    ordering = ['-created_at']

    def get_expected_fees(self, obj):
        return obj.get_expected_fees()
    get_expected_fees.short_description = 'Expected Fees'

    def get_total_paid(self, obj):
        return obj.get_total_paid()
    get_total_paid.short_description = 'Total Paid'

    def get_outstanding(self, obj):
        return obj.get_outstanding()
    get_outstanding.short_description = 'Outstanding'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'student', 'amount', 'method', 'date', 'reference']
    list_filter = ['method', 'date']
    search_fields = ['student__application_no', 'student__first_name', 'student__last_name', 'reference']
    ordering = ['-date']


@admin.register(PaymentClaim)
class PaymentClaimAdmin(admin.ModelAdmin):
    list_display = ['id', 'student', 'amount', 'statement_amount', 'variance', 'transaction_id', 'status', 'submitted_at']
    list_filter = ['status', 'method']
    search_fields = ['id', 'student__application_no', 'transaction_id', 'payer_phone']
    ordering = ['-submitted_at']


@admin.register(StatementRow)
class StatementRowAdmin(admin.ModelAdmin):
    list_display = ['transaction_id', 'date', 'amount', 'sender_phone', 'direction', 'status']
    list_filter = ['status', 'direction']
    search_fields = ['transaction_id', 'sender_phone', 'sender_name']


@admin.register(Credit)
class CreditAdmin(admin.ModelAdmin):
    list_display = ['id', 'student', 'amount', 'remaining_amount', 'created_by', 'created_at']
    search_fields = ['student__application_no', 'student__first_name', 'student__last_name']


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ['receipt_no', 'student', 'payment', 'issued_by', 'issued_at']
    search_fields = ['receipt_no', 'student__application_no', 'payment__reference']