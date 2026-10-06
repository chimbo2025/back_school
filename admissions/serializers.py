from rest_framework import serializers
from .models import Grade, FeeGroup, FeeItem, Student, Payment


class FeeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeItem
        fields = ['id', 'label', 'amount']


class FeeGroupSerializer(serializers.ModelSerializer):
    items = FeeItemSerializer(many=True, read_only=True)

    class Meta:
        model = FeeGroup
        fields = ['id', 'name', 'items']


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
    class Meta:
        model = Payment
        fields = ['id', 'student', 'amount', 'method', 'date', 'reference', 'notes', 'created_at']
        read_only_fields = ['created_at']


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

    class Meta:
        model = Student
        fields = [
            'first_name', 'middle_name', 'last_name', 'gender', 'dob',
            'address', 'parent_name', 'parent_phone', 'parent_address',
            'parent_relationship', 'parent_national_id',
            'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship',
            'grade_id', 'academic_year', 'previous_school', 'additional_info'
        ]

    def create(self, validated_data):
        grade_id = validated_data.pop('grade_id')
        grade = Grade.objects.get(id=grade_id)
        student = Student.objects.create(grade=grade, **validated_data)
        return student


class FinanceSummarySerializer(serializers.Serializer):
    total_expected = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_collected = serializers.DecimalField(max_digits=15, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=15, decimal_places=2)
    collection_rate = serializers.DecimalField(max_digits=5, decimal_places=2)