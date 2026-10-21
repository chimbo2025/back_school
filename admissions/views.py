from rest_framework import viewsets, status, filters, serializers
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Count
from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.auth import get_user_model
from .models import Grade, FeeGroup, FeeItem, Student, Payment
from .serializers import (
    GradeSerializer, GradeListSerializer, GradeWriteSerializer, FeeGroupSerializer,
    FeeItemSerializer, StudentListSerializer, StudentDetailSerializer,
    StudentCreateSerializer, PaymentSerializer, FinanceSummarySerializer
)

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_staff', 'is_superuser']


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


class FeeItemViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FeeItem.objects.all()
    serializer_class = FeeItemSerializer


class FeeGroupViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FeeGroup.objects.prefetch_related('items').all()
    serializer_class = FeeGroupSerializer


class GradeViewSet(viewsets.ModelViewSet):
    queryset = Grade.objects.prefetch_related('fee_groups__items').all()
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['name']
    ordering = ['name']

    def get_serializer_class(self):
        if self.action == 'list':
            return GradeListSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return GradeWriteSerializer
        return GradeSerializer

    def get_permissions(self):
        # Allow public access for viewing grades (list, retrieve)
        if self.action in ['list', 'retrieve']:
            return []
        return [IsAuthenticated()]


class StudentViewSet(viewsets.ModelViewSet):
    queryset = Student.objects.select_related('grade').prefetch_related('payments').all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['grade', 'status', 'academic_year', 'gender']
    search_fields = ['application_no', 'first_name', 'last_name', 'parent_name', 'parent_phone']
    ordering_fields = ['created_at', 'application_no', 'first_name', 'last_name']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return StudentListSerializer
        elif self.action == 'create':
            return StudentCreateSerializer
        return StudentDetailSerializer

    def get_permissions(self):
        # Allow public access for student registration (create)
        if self.action == 'create':
            return []
        return [IsAuthenticated()]

    @action(detail=False, methods=['get'])
    def stats(self, request):
        total_students = Student.objects.count()
        pending = Student.objects.filter(status='Pending').count()
        paid = Student.objects.filter(status='Paid').count()
        admitted = Student.objects.filter(status='Admitted').count()

        grades = Grade.objects.all()
        grade_stats = []
        for grade in grades:
            grade_students = Student.objects.filter(grade=grade)
            total_expected = sum(float(s.get_expected_fees()) for s in grade_students)
            total_collected = sum(float(s.get_total_paid()) for s in grade_students)
            grade_stats.append({
                'grade_id': grade.id,
                'grade_name': grade.name,
                'student_count': grade_students.count(),
                'total_expected': total_expected,
                'total_collected': total_collected,
                'outstanding': total_expected - total_collected,
            })

        return Response({
            'total_students': total_students,
            'by_status': {'pending': pending, 'paid': paid, 'admitted': admitted},
            'by_grade': grade_stats,
        })

    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        student = self.get_object()
        serializer = PaymentSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(student=student)
            student.refresh_from_db()
            return Response(StudentDetailSerializer(student).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['patch'])
    def update_status(self, request, pk=None):
        student = self.get_object()
        new_status = request.data.get('status')
        if new_status not in dict(Student.STATUS_CHOICES):
            return Response({'error': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
        student.status = new_status
        student.save()
        return Response(StudentDetailSerializer(student).data)


class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.select_related('student').all()
    serializer_class = PaymentSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['student', 'method']
    ordering_fields = ['date', 'amount']
    ordering = ['-date']


class FinanceViewSet(viewsets.ViewSet):
    @action(detail=False, methods=['get'])
    def summary(self, request):
        students = Student.objects.all()
        total_expected = sum(float(s.get_expected_fees()) for s in students)
        total_collected = sum(float(s.get_total_paid()) for s in students)
        outstanding = total_expected - total_collected
        collection_rate = (total_collected / total_expected * 100) if total_expected > 0 else 0

        by_method = Payment.objects.values('method').annotate(
            total=Sum('amount'),
            count=Count('id')
        )

        by_grade = []
        for grade in Grade.objects.all():
            grade_students = Student.objects.filter(grade=grade)
            g_expected = sum(float(s.get_expected_fees()) for s in grade_students)
            g_collected = sum(float(s.get_total_paid()) for s in grade_students)
            by_grade.append({
                'grade': grade.name,
                'expected': g_expected,
                'collected': g_collected,
                'outstanding': g_expected - g_collected,
            })

        serializer = FinanceSummarySerializer({
            'total_expected': total_expected,
            'total_collected': total_collected,
            'outstanding': outstanding,
            'collection_rate': round(collection_rate, 2),
        })
        return Response({
            'summary': serializer.data,
            'by_method': list(by_method),
            'by_grade': by_grade,
        })