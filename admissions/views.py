from rest_framework import viewsets, status, filters, serializers
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Sum, Count
from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.auth import get_user_model
from .models import Grade, FeeGroup, FeeItem, Student, Payment
from .permissions import IsAccountantOrAdmin, IsAdmin
from .serializers import (
    GradeSerializer, GradeListSerializer, GradeWriteSerializer, FeeGroupSerializer,
    FeeItemSerializer, StudentListSerializer, StudentDetailSerializer,
    StudentCreateSerializer, PaymentSerializer, FinanceSummarySerializer
)
from .services.allocation import (
    AllocationError, DuplicateTransactionError, confirm_payment,
)
from .utils import parse_date

User = get_user_model()


def _record_manual_payment(request, student, data):
    """Create an admin-only manual payment through the allocation service.

    Lipa Namba money must be backed by a till statement row, so it cannot be
    typed in by hand. Bank/cash payments are allowed for an admin, but require
    a reason and are fully allocated/receipted/audited like any other payment.
    """
    method = (data.get('method') or '').strip()
    if method == 'Lipa Namba':
        return None, Response(
            {
                'error': 'statement_required',
                'detail': (
                    'Lipa Namba payments must be confirmed from the till '
                    'statement or a parent claim, not entered manually.'
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    reason = (data.get('notes') or data.get('reason') or '').strip()
    if not reason:
        return None, Response(
            {
                'error': 'reason_required',
                'detail': 'A reason is required for a manual payment.',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    reference = (data.get('reference') or '').strip()
    if not reference:
        return None, Response(
            {'error': 'reference_required', 'detail': 'A transaction ID is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = confirm_payment(
            student=student,
            amount=data.get('amount'),
            reference=reference,
            payment_date=parse_date(data.get('date')),
            method=method or 'Bank',
            notes=reason,
            source='MANUAL',
            performed_by=_actor_from_request(request),
        )
    except DuplicateTransactionError as exc:
        return None, Response(
            {'error': 'duplicate_transaction', 'detail': str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except AllocationError as exc:
        return None, Response(
            {'error': 'allocation_error', 'detail': str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return result.payment, None


def _actor_from_request(request, fallback='admin'):
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user.username or fallback
    return fallback



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
    permission_classes = [AllowAny]


class FeeGroupViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FeeGroup.objects.prefetch_related('items').all()
    serializer_class = FeeGroupSerializer
    permission_classes = [AllowAny]


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
        # Fee structures are public (the parent portal needs them). Editing
        # fees is an administrator action.
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAdmin()]



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
        # Public student registration stays open. Staff can browse students;
        # only an administrator can change status or add money by hand.
        if self.action == 'create':
            return [AllowAny()]
        if self.action in ['list', 'retrieve', 'stats']:
            return [IsAccountantOrAdmin()]
        return [IsAdmin()]

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
        payment, error = _record_manual_payment(request, student, request.data)
        if error is not None:
            return error
        student.refresh_from_db()
        return Response(
            StudentDetailSerializer(student).data,
            status=status.HTTP_201_CREATED,
        )

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

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAccountantOrAdmin()]
        return [IsAdmin()]

    def create(self, request, *args, **kwargs):
        student = Student.objects.filter(id=request.data.get('student')).first()
        if student is None:
            return Response(
                {'error': 'Student not found.'}, status=status.HTTP_400_BAD_REQUEST
            )
        payment, error = _record_manual_payment(request, student, request.data)
        if error is not None:
            return error
        return Response(
            PaymentSerializer(payment).data, status=status.HTTP_201_CREATED
        )


class FinanceViewSet(viewsets.ViewSet):
    permission_classes = [IsAccountantOrAdmin]

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