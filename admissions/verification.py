from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Student, Payment, PaymentClaim, StatementImport, StatementRow,
    Allocation, AuditLog, AppSetting,
)
from .serializers import (
    PaymentSerializer, PaymentClaimSerializer, StatementRowSerializer,
    StatementImportSerializer, AllocationSerializer, AuditLogSerializer,
    AppSettingSerializer,
)
from .utils import new_id

DEFAULT_SETTINGS = {
    'allocation_order': [
        {'feeGroupName': 'ADMISSION FEE', 'priority': 1},
        {'feeGroupName': 'SCHOOL FEE', 'priority': 2},
        {'feeGroupName': 'TRANSPORT FEE', 'priority': 3},
        {'feeGroupName': 'OTHER FEE', 'priority': 4},
    ],
    'not_found_days': 3,
    'csv_mapping': None,
}

ACTIVE_ROW_STATUSES = ['UNCLAIMED', 'MATCHED', 'ASSIGNED']


def get_setting(key):
    try:
        return AppSetting.objects.get(key=key).value
    except AppSetting.DoesNotExist:
        return DEFAULT_SETTINGS.get(key)


def write_audit(student=None, payment=None, action='MANUAL_ADJUST', performed_by='system',
                before=None, after=None, reason=''):
    return AuditLog.objects.create(
        id=new_id('audit'),
        student=student,
        payment=payment,
        action=action,
        performed_by=performed_by,
        before=before or {},
        after=after or {},
        reason=reason or '',
    )


class ClaimViewSet(viewsets.ModelViewSet):
    queryset = PaymentClaim.objects.select_related('student__grade').all()
    serializer_class = PaymentClaimSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['student', 'status']
    ordering_fields = ['submitted_at', 'payment_date', 'amount']
    ordering = ['-submitted_at']

    def get_permissions(self):
        # Parents are not authenticated in this version: allow public submit/view,
        # keep review actions (approve/reject/scan) behind login.
        if self.action in ['list', 'create', 'retrieve', 'parents']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        claim = serializer.save(id=serializer.validated_data.get('id') or new_id('claim'))
        if not claim.transaction_id:
            claim.status = 'NEEDS_REVIEW'
            claim.match_reason = 'Claim has no transaction ID. / Hakuna namba ya muamala.'
            claim.save()
        write_audit(
            student=claim.student,
            action='CLAIM_SUBMIT',
            performed_by=claim.payer_phone,
            after={'amount': str(claim.amount), 'transaction_id': claim.transaction_id},
            reason=f'Claim submitted by parent ({claim.payer_phone})',
        )

    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def parents(self, request):
        """Mock parent directory for the parent portal (no parent login in this version)."""
        students = Student.objects.select_related('grade').all()
        parents = {}
        for s in students:
            key = s.parent_phone
            entry = parents.setdefault(key, {
                'name': s.parent_name,
                'phone': s.parent_phone,
                'students': [],
            })
            total_fees = float(s.get_expected_fees())
            total_paid = float(s.get_total_paid())
            entry['students'].append({
                'id': s.id,
                'application_no': s.application_no,
                'name': s.full_name,
                'grade': s.grade.name,
                'status': s.status,
                'total_fees': total_fees,
                'total_paid': total_paid,
                'balance': max(0.0, total_fees - total_paid),
                'credit': max(0.0, total_paid - total_fees),
            })
        return Response(list(parents.values()))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a claim using its matched statement row and create the payment."""
        claim = self.get_object()
        if claim.status == 'APPROVED':
            return Response({'error': 'Claim is already approved.'}, status=status.HTTP_400_BAD_REQUEST)
        if claim.status == 'REJECTED':
            return Response({'error': 'Claim was rejected.'}, status=status.HTTP_400_BAD_REQUEST)

        row = claim.statement_rows.filter(status__in=ACTIVE_ROW_STATUSES).first()
        if row is None:
            return Response(
                {
                    'error': 'no_statement_row',
                    'detail': 'This claim has no matching statement row. Money that is not on the till statement cannot be approved.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Payment.objects.filter(reference=row.transaction_id).exists():
            return Response(
                {
                    'error': 'duplicate_transaction',
                    'detail': f'Transaction ID "{row.transaction_id}" has already been used.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        reviewed_by = request.data.get('reviewed_by') or request.user.username
        payment = Payment.objects.create(
            id=new_id('pay'),
            student=claim.student,
            amount=row.amount,
            method=claim.method or 'M-Pesa',
            date=row.date,
            reference=row.transaction_id,
            notes=f'Payment claim {claim.id}',
            source='CLAIM',
            statement_row=row,
        )
        previous_status = claim.status
        claim.status = 'APPROVED'
        claim.matched_payment = payment
        claim.reviewed_at = timezone.now()
        claim.reviewed_by = reviewed_by
        claim.save()

        row.student = claim.student
        row.save()

        write_audit(
            student=claim.student,
            payment=payment,
            action='CLAIM_APPROVE',
            performed_by=reviewed_by,
            before={'claim_status': previous_status},
            after={'claim_status': 'APPROVED', 'payment_id': payment.id, 'amount': str(payment.amount)},
            reason=request.data.get('reason') or claim.match_reason or 'Matched statement row approved',
        )
        return Response(
            {'claim': PaymentClaimSerializer(claim).data, 'payment': PaymentSerializer(payment).data},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        claim = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'error': 'A rejection reason is required.'}, status=status.HTTP_400_BAD_REQUEST)
        reviewed_by = request.data.get('reviewed_by') or request.user.username
        previous_status = claim.status
        claim.status = 'REJECTED'
        claim.rejection_reason = reason
        claim.reviewed_at = timezone.now()
        claim.reviewed_by = reviewed_by
        claim.save()
        write_audit(
            student=claim.student,
            action='CLAIM_REJECT',
            performed_by=reviewed_by,
            before={'status': previous_status},
            after={'status': 'REJECTED'},
            reason=reason,
        )
        return Response(PaymentClaimSerializer(claim).data)

    @action(detail=False, methods=['post'])
    def scan_not_found(self, request):
        """Mark claims with no statement row after N days as NOT_FOUND (and restore any that now have a row)."""
        days = int(request.data.get('days') or get_setting('not_found_days') or 3)
        performed_by = request.data.get('performed_by') or (
            request.user.username if request.user.is_authenticated else 'accountant'
        )
        cutoff = timezone.now() - timedelta(days=days)
        marked, restored = [], []

        for claim in PaymentClaim.objects.filter(status__in=['PENDING', 'NEEDS_REVIEW']):
            if claim.statement_rows.filter(status__in=ACTIVE_ROW_STATUSES).exists():
                continue
            if claim.submitted_at <= cutoff:
                previous_status = claim.status
                claim.status = 'NOT_FOUND'
                claim.match_reason = f'No statement row found {days} days after submission.'
                claim.save()
                write_audit(
                    student=claim.student, action='CLAIM_NOT_FOUND', performed_by=performed_by,
                    before={'status': previous_status}, after={'status': 'NOT_FOUND'},
                    reason=f'No statement row within {days} days',
                )
                marked.append(claim.id)

        for claim in PaymentClaim.objects.filter(status='NOT_FOUND'):
            if claim.statement_rows.filter(status__in=ACTIVE_ROW_STATUSES).exists():
                claim.status = 'NEEDS_REVIEW'
                claim.match_reason = 'Statement row received after review. Verify and approve.'
                claim.save()
                restored.append(claim.id)

        return Response({'not_found': marked, 'restored': restored, 'days': days})


class StatementViewSet(viewsets.ViewSet):
    """Statement imports and rows."""

    def list(self, request):
        if request.query_params.get('resource') == 'rows':
            qs = StatementRow.objects.select_related('claim', 'student', 'import_record').all()
            status_filter = request.query_params.get('status')
            if status_filter:
                qs = qs.filter(status=status_filter)
            return Response(StatementRowSerializer(qs[:2000], many=True).data)
        imports = StatementImport.objects.all()
        return Response(StatementImportSerializer(imports[:200], many=True).data)

    @action(detail=False, methods=['get'])
    def txn_ids(self, request):
        ids = set(Payment.objects.values_list('reference', flat=True))
        ids |= set(
            StatementRow.objects.filter(status__in=ACTIVE_ROW_STATUSES)
            .values_list('transaction_id', flat=True)
        )
        return Response(sorted(ids))

    @action(detail=False, methods=['post'])
    def imports(self, request):
        file_name = request.data.get('file_name', 'statement.csv')
        file_hash = request.data.get('file_hash', '')
        mapping = request.data.get('mapping') or {}
        rows_data = request.data.get('rows') or []
        matches = request.data.get('matches') or []
        uploaded_by = request.data.get('uploaded_by') or (
            request.user.username if request.user.is_authenticated else 'accountant'
        )

        if file_hash and StatementImport.objects.filter(file_hash=file_hash).exists():
            existing = StatementImport.objects.filter(file_hash=file_hash).first()
            return Response(
                {
                    'error': 'duplicate_file',
                    'detail': f'"{existing.file_name}" was already imported on {existing.uploaded_at:%Y-%m-%d %H:%M}.',
                    'import': StatementImportSerializer(existing).data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        import_record = StatementImport.objects.create(
            id=new_id('import'),
            file_name=file_name,
            file_hash=file_hash or new_id('hash'),
            mapping=mapping,
            uploaded_by=uploaded_by,
        )

        match_by_txn = {}
        for m in matches:
            txn = (m.get('transaction_id') or '').strip().upper()
            if txn:
                match_by_txn[txn] = m

        active_txns = set(
            StatementRow.objects.filter(status__in=ACTIVE_ROW_STATUSES)
            .values_list('transaction_id', flat=True)
        )

        created_rows, created_payments = [], []
        imported = skipped = matched_claims = 0
        seen_in_file = set()

        for rd in rows_data:
            txn = (rd.get('transaction_id') or '').strip().upper()
            direction = (rd.get('direction') or 'IN').upper()
            try:
                amount = Decimal(str(rd.get('amount')).replace(',', ''))
            except (InvalidOperation, ValueError):
                amount = Decimal('0')

            match = match_by_txn.get(txn)
            claim = None
            if match and match.get('claim_id'):
                claim = PaymentClaim.objects.filter(id=match['claim_id']).first()

            # --- dedup / skip rules -------------------------------------
            if direction == 'OUT' or amount <= 0 or not txn:
                row_status, row_reason = 'SKIPPED', 'Outgoing or invalid row'
            elif txn in active_txns or txn in seen_in_file:
                row_status, row_reason = 'DUPLICATE', 'Transaction ID already on statement'
            else:
                row_status, row_reason = 'UNCLAIMED', ''

            seen_in_file.add(txn)

            row = StatementRow.objects.create(
                id=new_id('srow'),
                import_record=import_record,
                transaction_id=txn,
                date=rd.get('date') or timezone.now().date(),
                amount=amount,
                sender_phone=rd.get('sender_phone') or '',
                sender_name=rd.get('sender_name') or '',
                receiver_till=rd.get('receiver_till') or '',
                direction=direction if direction in ('IN', 'OUT') else 'IN',
                status=row_status,
                match_reason=row_reason,
            )

            if row_status in ('SKIPPED', 'DUPLICATE'):
                skipped += 1
                continue

            imported += 1

            # --- apply the client-side match ----------------------------
            if claim and claim.status in ('PENDING', 'NEEDS_REVIEW'):
                match_type = (match.get('match_type') or 'EXACT').upper()
                reason = match.get('reason') or ''

                if match_type == 'EXACT':
                    if Payment.objects.filter(reference=txn).exists():
                        claim.status = 'NEEDS_REVIEW'
                        claim.match_reason = f'Transaction ID "{txn}" has already been used.'
                        row.status = 'UNCLAIMED'
                        row.match_reason = claim.match_reason
                        row.save()
                        claim.save()
                        skipped += 1
                        imported -= 1
                        continue

                    payment = Payment.objects.create(
                        id=new_id('pay'),
                        student=claim.student,
                        amount=amount,
                        method=claim.method or 'M-Pesa',
                        date=row.date,
                        reference=txn,
                        notes=f'Payment claim {claim.id}',
                        source='CLAIM',
                        statement_row=row,
                    )
                    claim.status = 'APPROVED'
                    claim.matched_payment = payment
                    claim.reviewed_at = timezone.now()
                    claim.reviewed_by = uploaded_by
                    claim.match_reason = ''
                    claim.save()

                    row.status = 'MATCHED'
                    row.claim = claim
                    row.student = claim.student
                    row.match_type = 'EXACT'
                    row.save()

                    created_payments.append(PaymentSerializer(payment).data)
                    matched_claims += 1
                else:
                    claim.status = 'NEEDS_REVIEW'
                    claim.match_reason = reason or f'Match type {match_type}'
                    claim.save()

                    row.status = 'MATCHED'
                    row.claim = claim
                    row.student = claim.student
                    row.match_type = match_type
                    row.match_reason = reason
                    row.save()
                    matched_claims += 1

            created_rows.append(row)

        import_record.total_rows = len(rows_data)
        import_record.imported_rows = imported
        import_record.skipped_rows = skipped
        import_record.matched_claims = matched_claims
        import_record.save()

        write_audit(
            action='CSV_IMPORT',
            performed_by=uploaded_by,
            after={
                'file_name': file_name,
                'total_rows': len(rows_data),
                'imported_rows': imported,
                'skipped_rows': skipped,
                'matched_claims': matched_claims,
            },
            reason=f'Imported statement "{file_name}" ({imported} rows, {matched_claims} claims matched)',
        )

        return Response(
            {
                'import': StatementImportSerializer(import_record).data,
                'rows': StatementRowSerializer(
                    StatementRow.objects.filter(import_record=import_record)
                    .select_related('claim', 'student'), many=True
                ).data,
                'payments': created_payments,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def assign_row(self, request, pk=None):
        """Assign an unclaimed statement row to a student (creates a payment)."""
        try:
            row = StatementRow.objects.select_related('import_record').get(pk=pk)
        except StatementRow.DoesNotExist:
            return Response({'error': 'Row not found.'}, status=status.HTTP_404_NOT_FOUND)

        if row.status not in ('UNCLAIMED', 'MATCHED'):
            return Response({'error': f'Row already {row.status.lower()}.'}, status=status.HTTP_400_BAD_REQUEST)
        if row.direction == 'OUT':
            return Response({'error': 'Outgoing rows cannot be assigned.'}, status=status.HTTP_400_BAD_REQUEST)
        if Payment.objects.filter(reference=row.transaction_id).exists():
            return Response(
                {'error': 'duplicate_transaction', 'detail': f'Transaction ID "{row.transaction_id}" has already been used.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student_id = request.data.get('student_id')
        if not student_id:
            return Response({'error': 'student_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist:
            return Response({'error': 'Student not found.'}, status=status.HTTP_404_NOT_FOUND)

        performed_by = request.data.get('performed_by') or (
            request.user.username if request.user.is_authenticated else 'accountant'
        )
        note = (request.data.get('note') or '').strip()

        payment = Payment.objects.create(
            id=new_id('pay'),
            student=student,
            amount=row.amount,
            method=request.data.get('method') or 'M-Pesa',
            date=row.date,
            reference=row.transaction_id,
            notes=f'Assigned from statement row {row.id}' + (f': {note}' if note else ''),
            source='CLAIM',
            statement_row=row,
        )
        row.status = 'ASSIGNED'
        row.student = student
        if note:
            row.match_reason = note
        row.save()

        write_audit(
            student=student,
            payment=payment,
            action='ROW_ASSIGN',
            performed_by=performed_by,
            before={'row_status': 'UNCLAIMED'},
            after={'row_status': 'ASSIGNED', 'payment_id': payment.id, 'amount': str(payment.amount)},
            reason=note or f'Assigned unclaimed statement row {row.transaction_id}',
        )
        return Response(
            {'row': StatementRowSerializer(row).data, 'payment': PaymentSerializer(payment).data},
            status=status.HTTP_201_CREATED,
        )


class AllocationViewSet(viewsets.ViewSet):
    def list(self, request):
        qs = Allocation.objects.all()
        student_id = request.query_params.get('student')
        if student_id:
            qs = qs.filter(student=student_id)
        payment_id = request.query_params.get('payment')
        if payment_id:
            qs = qs.filter(payment=payment_id)
        return Response(AllocationSerializer(qs[:5000], many=True).data)

    @action(detail=False, methods=['post'])
    def bulk(self, request):
        """Persist allocations computed by the client allocation engine."""
        allocations = request.data.get('allocations') or []
        reversed_ids = request.data.get('reversed_ids') or []
        audit_data = request.data.get('audit') or {}
        performed_by = request.data.get('performed_by') or (
            request.user.username if request.user.is_authenticated else 'system'
        )

        if not isinstance(allocations, list) or not isinstance(reversed_ids, list):
            return Response({'error': 'allocations and reversed_ids must be lists.'},
                            status=status.HTTP_400_BAD_REQUEST)

        created = []
        for a in allocations:
            payment = Payment.objects.filter(id=a.get('payment')).first()
            student = Student.objects.filter(id=a.get('student')).first()
            if not payment or not student:
                return Response({'error': 'Unknown payment or student in allocations.'},
                                status=status.HTTP_400_BAD_REQUEST)
            amount = Decimal(str(a.get('amount', 0)))
            if amount <= 0:
                return Response({'error': 'Allocation amounts must be greater than zero.'},
                                status=status.HTTP_400_BAD_REQUEST)
            obj = Allocation.objects.create(
                id=a.get('id') or new_id('alloc'),
                payment=payment,
                student=student,
                fee_line_id=a.get('fee_line_id', ''),
                fee_group_name=a.get('fee_group_name', ''),
                fee_item_label=a.get('fee_item_label', ''),
                amount=amount,
                created_by=a.get('created_by') or performed_by,
            )
            created.append(obj)

        for rid in reversed_ids:
            Allocation.objects.filter(id=rid, reversed=False).update(reversed=True)

        if audit_data:
            student = Student.objects.filter(id=audit_data.get('student')).first()
            payment = Payment.objects.filter(id=audit_data.get('payment')).first()
            write_audit(
                student=student,
                payment=payment,
                action=audit_data.get('action') or 'ALLOCATE',
                performed_by=audit_data.get('performed_by') or performed_by,
                before=audit_data.get('before') or {},
                after=audit_data.get('after') or {},
                reason=audit_data.get('reason') or '',
            )

        return Response(AllocationSerializer(created, many=True).data, status=status.HTTP_201_CREATED)


class AuditViewSet(viewsets.ViewSet):
    def list(self, request):
        qs = AuditLog.objects.select_related('student').all()
        student_id = request.query_params.get('student')
        if student_id:
            qs = qs.filter(student=student_id)
        action_filter = request.query_params.get('action')
        if action_filter:
            qs = qs.filter(action=action_filter)
        return Response(AuditLogSerializer(qs[:1000], many=True).data)

    def create(self, request):
        student = Student.objects.filter(id=request.data.get('student')).first()
        payment = Payment.objects.filter(id=request.data.get('payment')).first()
        if not request.data.get('action'):
            return Response({'error': 'action is required.'}, status=status.HTTP_400_BAD_REQUEST)
        log = write_audit(
            student=student,
            payment=payment,
            action=request.data['action'],
            performed_by=request.data.get('performed_by') or (
                request.user.username if request.user.is_authenticated else 'system'
            ),
            before=request.data.get('before') or {},
            after=request.data.get('after') or {},
            reason=request.data.get('reason') or '',
        )
        return Response(AuditLogSerializer(log).data, status=status.HTTP_201_CREATED)


class SettingsViewSet(viewsets.ViewSet):
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def list(self, request):
        values = dict(DEFAULT_SETTINGS)
        for setting in AppSetting.objects.all():
            values[setting.key] = setting.value
        return Response([{'key': k, 'value': v} for k, v in values.items()])

    def create(self, request):
        return self._save(request)

    def update(self, request, pk=None):
        return self._save(request, key=pk)

    def _save(self, request, key=None):
        data = request.data
        if key is not None:
            data = {key: data.get('value')}
        saved = {}
        for k, v in data.items():
            if k not in DEFAULT_SETTINGS:
                return Response({'error': f'Unknown setting "{k}".'}, status=status.HTTP_400_BAD_REQUEST)
            AppSetting.objects.update_or_create(key=k, defaults={'value': v})
            saved[k] = v
        if 'allocation_order' in saved or 'not_found_days' in saved:
            write_audit(
                action='SETTINGS_CHANGE',
                performed_by=request.user.username if request.user.is_authenticated else 'admin',
                after=saved,
                reason='Settings updated',
            )
        return Response([{'key': k, 'value': v} for k, v in saved.items()])
