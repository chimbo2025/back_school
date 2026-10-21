import time
from rest_framework import serializers
from .models import (
    Grade, FeeGroup, FeeItem, Student, Payment, PaymentClaim,
    StatementImport, StatementRow, Allocation, AuditLog, AppSetting,
)
from .utils import new_id


class FeeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeItem
        fields = ['id', 'label', 'amount']


class FeeItemWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeItem
        fields = ['id', 'label', 'amount']


class FeeGroupSerializer(serializers.ModelSerializer):
    items = FeeItemSerializer(many=True, read_only=True)

    class Meta:
        model = FeeGroup
        fields = ['id', 'name', 'items']


class FeeGroupWriteSerializer(serializers.ModelSerializer):
    items = FeeItemWriteSerializer(many=True)

    class Meta:
        model = FeeGroup
        fields = ['id', 'name', 'items']

    def to_representation(self, instance):
        return FeeGroupSerializer(instance, context=self.context).data

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        fee_group = FeeGroup.objects.create(**validated_data)
        for item_data in items_data:
            item_id = item_data.pop('id', None)
            if item_id:
                item, _ = FeeItem.objects.update_or_create(id=item_id, defaults=item_data)
            else:
                item = FeeItem.objects.create(**item_data)
            fee_group.items.add(item)
        return fee_group

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])
        instance.name = validated_data.get('name', instance.name)
        instance.save()

        # Update or create items
        existing_item_ids = set(instance.items.values_list('id', flat=True))
        new_item_ids = set()

        for item_data in items_data:
            item_id = item_data.get('id')
            if item_id:
                item, _ = FeeItem.objects.update_or_create(id=item_id, defaults=item_data)
                new_item_ids.add(item.id)
            else:
                item = FeeItem.objects.create(**item_data)
                new_item_ids.add(item.id)
            instance.items.add(item)

        # Remove items not in the update
        to_remove = existing_item_ids - new_item_ids
        if to_remove:
            instance.items.remove(*to_remove)

        return instance


class GradeSerializer(serializers.ModelSerializer):
    fee_groups = FeeGroupSerializer(many=True, read_only=True)
    total_fee = serializers.SerializerMethodField()

    class Meta:
        model = Grade
        fields = ['id', 'name', 'description', 'age_range', 'fee_groups', 'total_fee']

    def get_total_fee(self, obj):
        return float(obj.get_total_fee())


class GradeListSerializer(serializers.ModelSerializer):
    total_fee = serializers.SerializerMethodField()

    class Meta:
        model = Grade
        fields = ['id', 'name', 'description', 'age_range', 'total_fee']

    def get_total_fee(self, obj):
        return float(obj.get_total_fee())


class PaymentSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False)

    class Meta:
        model = Payment
        fields = ['id', 'student', 'amount', 'method', 'date', 'reference', 'notes', 'source', 'created_at']
        read_only_fields = ['created_at', 'source']

    def validate_reference(self, value):
        qs = Payment.objects.filter(reference=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(f'Transaction ID "{value}" already exists.')
        return value

    def create(self, validated_data):
        if not validated_data.get('id'):
            validated_data['id'] = new_id('pay')
        return super().create(validated_data)


class StudentListSerializer(serializers.ModelSerializer):
    grade_name = serializers.CharField(source='grade.name', read_only=True)
    expected_fees = serializers.SerializerMethodField()
    total_paid = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            'id', 'application_no', 'first_name', 'middle_name', 'last_name',
            'gender', 'dob', 'address', 'parent_name', 'parent_phone',
            'parent_relationship', 'parent_national_id',
            'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship',
            'grade', 'grade_name', 'academic_year', 'status',
            'expected_fees', 'total_paid', 'outstanding', 'created_at'
        ]

    def get_expected_fees(self, obj):
        return float(obj.get_expected_fees())

    def get_total_paid(self, obj):
        return float(obj.get_total_paid())

    def get_outstanding(self, obj):
        return float(obj.get_outstanding())


class StudentDetailSerializer(serializers.ModelSerializer):
    grade = GradeListSerializer(read_only=True)
    grade_id = serializers.CharField(write_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    expected_fees = serializers.SerializerMethodField()
    total_paid = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            'id', 'application_no', 'first_name', 'middle_name', 'last_name',
            'gender', 'dob', 'address', 'parent_name', 'parent_phone',
            'parent_address', 'parent_relationship', 'parent_national_id',
            'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship',
            'grade', 'grade_id', 'academic_year',
            'previous_school', 'additional_info', 'notes', 'status',
            'expected_fees', 'total_paid', 'outstanding', 'payments',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['application_no', 'created_at', 'updated_at']

    def get_expected_fees(self, obj):
        return float(obj.get_expected_fees())

    def get_total_paid(self, obj):
        return float(obj.get_total_paid())

    def get_outstanding(self, obj):
        return float(obj.get_outstanding())


class StudentCreateSerializer(serializers.ModelSerializer):
    grade_id = serializers.CharField()
    id = serializers.CharField(required=False)

    class Meta:
        model = Student
        fields = [
            'id', 'first_name', 'middle_name', 'last_name', 'gender', 'dob',
            'address', 'parent_name', 'parent_phone', 'parent_address',
            'parent_relationship', 'parent_national_id',
            'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship',
            'grade_id', 'academic_year', 'previous_school', 'additional_info'
        ]

    def to_representation(self, instance):
        return StudentDetailSerializer(instance, context=self.context).data

    def create(self, validated_data):
        if 'id' not in validated_data:
            validated_data['id'] = f"stu-{int(time.time() * 1000)}"
        # Generate unique application number (with retry for race conditions)
        year = time.strftime('%Y')
        for attempt in range(5):
            count = Student.objects.count() + 1 + attempt
            app_no = f"BFS-{year}-{count:04d}"
            if not Student.objects.filter(application_no=app_no).exists():
                validated_data['application_no'] = app_no
                break
        else:
            # Fallback with timestamp
            validated_data['application_no'] = f"BFS-{year}-{int(time.time() * 1000)}"
        grade_id = validated_data.pop('grade_id')
        grade = Grade.objects.get(id=grade_id)
        student = Student.objects.create(grade=grade, **validated_data)
        return student


class FinanceSummarySerializer(serializers.Serializer):
    total_expected = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_collected = serializers.DecimalField(max_digits=15, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=15, decimal_places=2)
    collection_rate = serializers.DecimalField(max_digits=5, decimal_places=2)


class GradeWriteSerializer(serializers.ModelSerializer):
    fee_groups = FeeGroupWriteSerializer(many=True)
    id = serializers.CharField(required=False)

    class Meta:
        model = Grade
        fields = ['id', 'name', 'description', 'age_range', 'fee_groups']

    def to_representation(self, instance):
        # Use the read serializer for response
        return GradeSerializer(instance, context=self.context).data

    def create(self, validated_data):
        if 'id' not in validated_data:
            validated_data['id'] = f"grade-{int(time.time() * 1000)}"
        fee_groups_data = validated_data.pop('fee_groups')
        grade = Grade.objects.create(**validated_data)
        for fg_data in fee_groups_data:
            fg_serializer = FeeGroupWriteSerializer(data=fg_data)
            fg_serializer.is_valid(raise_exception=True)
            fee_group = fg_serializer.save()
            grade.fee_groups.add(fee_group)
        return grade

    def update(self, instance, validated_data):
        fee_groups_data = validated_data.pop('fee_groups', [])
        instance.name = validated_data.get('name', instance.name)
        instance.description = validated_data.get('description', instance.description)
        instance.age_range = validated_data.get('age_range', instance.age_range)
        instance.save()

        # Update or create fee groups
        existing_fg_ids = set(instance.fee_groups.values_list('id', flat=True))
        new_fg_ids = set()

        for fg_data in fee_groups_data:
            fg_id = fg_data.get('id')
            if fg_id:
                fee_group, _ = FeeGroup.objects.update_or_create(id=fg_id, defaults={'name': fg_data.get('name')})
                new_fg_ids.add(fee_group.id)
                # Update items within the fee group
                items_data = fg_data.get('items', [])
                existing_item_ids = set(fee_group.items.values_list('id', flat=True))
                new_item_ids = set()

                for item_data in items_data:
                    item_id = item_data.get('id')
                    if item_id:
                        item, _ = FeeItem.objects.update_or_create(id=item_id, defaults=item_data)
                        new_item_ids.add(item.id)
                    else:
                        item = FeeItem.objects.create(**item_data)
                        new_item_ids.add(item.id)
                    fee_group.items.add(item)

                # Remove items not in the update
                to_remove = existing_item_ids - new_item_ids
                if to_remove:
                    fee_group.items.remove(*to_remove)
            else:
                fee_group = FeeGroup.objects.create(name=fg_data.get('name', 'NEW FEE GROUP'))
                new_fg_ids.add(fee_group.id)
                items_data = fg_data.get('items', [])
                for item_data in items_data:
                    item = FeeItem.objects.create(**item_data)
                    fee_group.items.add(item)
            instance.fee_groups.add(fee_group)

        # Remove fee groups not in the update
        to_remove_fg = existing_fg_ids - new_fg_ids
        if to_remove_fg:
            instance.fee_groups.remove(*to_remove_fg)

        return instance

class PaymentClaimSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    grade_name = serializers.SerializerMethodField()
    id = serializers.CharField(required=False)

    class Meta:
        model = PaymentClaim
        fields = [
            'id', 'student', 'student_name', 'grade_name', 'amount', 'transaction_id',
            'payment_date', 'payer_phone', 'method', 'screenshot', 'notes', 'status',
            'match_reason', 'submitted_at', 'reviewed_at', 'reviewed_by',
            'rejection_reason', 'matched_payment',
        ]
        read_only_fields = ['submitted_at', 'reviewed_at', 'reviewed_by', 'rejection_reason', 'match_reason', 'matched_payment', 'status']

    def get_student_name(self, obj):
        return obj.student.full_name

    def get_grade_name(self, obj):
        return obj.student.grade.name

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Amount must be greater than zero.')
        return value

    def validate_transaction_id(self, value):
        return (value or '').strip().upper()


class StatementRowSerializer(serializers.ModelSerializer):
    claim_status = serializers.CharField(source='claim.status', read_only=True, default=None)
    student_name = serializers.SerializerMethodField()

    class Meta:
        model = StatementRow
        fields = [
            'id', 'import_record', 'transaction_id', 'date', 'amount', 'sender_phone',
            'sender_name', 'receiver_till', 'direction', 'status', 'claim', 'claim_status',
            'student', 'student_name', 'match_type', 'match_reason', 'created_at',
        ]

    def get_student_name(self, obj):
        return obj.student.full_name if obj.student else None


class StatementImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatementImport
        fields = [
            'id', 'file_name', 'file_hash', 'mapping', 'uploaded_at', 'uploaded_by',
            'total_rows', 'imported_rows', 'skipped_rows', 'matched_claims', 'status',
        ]


class AllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Allocation
        fields = [
            'id', 'payment', 'student', 'fee_line_id', 'fee_group_name',
            'fee_item_label', 'amount', 'reversed', 'created_at', 'created_by',
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            'id', 'student', 'student_name', 'payment', 'action', 'performed_by',
            'performed_at', 'before', 'after', 'reason',
        ]

    def get_student_name(self, obj):
        return obj.student.full_name if obj.student else None


class AppSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppSetting
        fields = ['key', 'value', 'updated_at']
        read_only_fields = ['updated_at']
