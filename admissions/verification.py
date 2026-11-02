from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Student, Payment, PaymentClaim, StatementImport, StatementRow,
    Allocation, Credit, AuditLog, AppSetting,
)
from .normalization import normalize_phone
from .permissions import IsAccountantOrAdmin, IsAdmin
from .serializers import (
    PaymentSerializer, PaymentClaimSerializer, StatementRowSerializer,
    StatementImportSerializer, AllocationSerializer, AuditLogSerializer,
    CreditSerializer,
)
from .services.allocation import (
    AllocationError,
    DuplicateTransactionError,
    build_fee_lines,
    confirm_payment,
    money,
    plan_reallocation,
)
from .services.matching import (
    INACTIVE_ROW_STATUSES,
    OPEN_CLAIM_STATUSES,
    find_not_found,
    match_rows,
    phones_for_parent,
)
from .throttles import ClaimSubmitThrottle
from .utils import new_id, parse_date

DEFAULT_SETTINGS = {
    'allocation_order': [
        {'feeGroupName': 'ADMISSION FEE', 'priority': 1},
        {'feeGroupName': 'SCHOOL FEE', 'priority': 2},
        {'feeGroupName': 'TRANSPORT FEE', 'priority': 3},
        {'feeGroupName': 'OTHER FEE', 'priority': 4},
    ],
    'not_found_days': 3,
    'csv_mapping': None,
    # One configurable number for "call us if confirmation takes longer than a
    # day". Stored in international form so WhatsApp links work directly.
    'contact_phone': '255778407818',
    'whatsapp': '255778407818',
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


def _actor(request, fallback='accountant'):
    """The authenticated user is the audit actor; never trust a client name."""
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user.username or fallback
    return fallback


def _mismatch(row, claim, phones):
    """Return (match_type, human reason) for a transaction-ID match that differs."""
    sender = normalize_phone(row.sender_phone)
    amount_ok = money(row.amount) == money(claim.amount)
    phone_ok = bool(sender) and sender in phones

    problems = []
    if not amount_ok:
        problems.append(
            f'Amount differs: statement {money(row.amount):,.2f} Tsh vs claim '
            f'{money(claim.amount):,.2f} Tsh'
        )
    if not phone_ok:
        saved = ', '.join(phones) if phones else 'none'
        problems.append(
            f'Payer phone {row.sender_phone or "(none)"} does not match any phone '
            f'saved for this parent ({saved})'
        )

    if not phone_ok and not amount_ok:
        match_type = 'AMOUNT_PHONE_MISMATCH'
    elif not phone_ok:
        match_type = 'PHONE_MISMATCH'
    else:
        match_type = 'AMOUNT_MISMATCH'
    return match_type, '; '.join(problems)


def _automatch_existing_row(claim, performed_by):
    """A statement row may arrive before the parent submits the claim.

    If the row is still unclaimed, apply the same exact/mismatch rules used by
    the importer so the parent is not left waiting for a manual review.
    """
    if not claim.transaction_id:
        return
    row = StatementRow.objects.filter(
        transaction_id=claim.transaction_id, status='UNCLAIMED'
    ).first()
    if row is None:
        return

    students = list(Student.objects.select_related('grade').all())
    claims = list(PaymentClaim.objects.exclude(status='REJECTED'))
    phones = phones_for_parent(claim.student, students, claims)
    sender = normalize_phone(row.sender_phone)
    exact = money(row.amount) == money(claim.amount) and bool(sender) and sender in phones

    if exact:
        try:
            confirm_payment(
                student=claim.student,
                amount=row.amount,
                reference=row.transaction_id,
                payment_date=row.date,
                method=claim.method or 'Lipa Namba',
                notes=f'Payment claim {claim.id}',
                source='CLAIM',
                statement_row=row,
                claim=claim,
                performed_by=performed_by,
            )
            return
        except (DuplicateTransactionError, AllocationError) as exc:
            claim.status = 'NEEDS_REVIEW'
            claim.match_reason = str(exc)
            claim.save()
            return

    match_type, reason = _mismatch(row, claim, phones)
    claim.status = 'NEEDS_REVIEW'
    claim.statement_amount = row.amount
    claim.variance = money(row.amount - money(claim.amount))
    claim.match_reason = reason
    claim.save()

    row.status = 'MATCHED'
    row.claim = claim
    row.student = claim.student
    row.match_type = match_type
    row.match_reason = reason
    row.save()


class ClaimViewSet(viewsets.ModelViewSet):
    queryset = PaymentClaim.objects.select_related('student__grade').all()
    serializer_class = PaymentClaimSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['student', 'status']
    ordering_fields = ['submitted_at', 'payment_date', 'amount']
    ordering = ['-submitted_at']

    def get_permissions(self):
        # Parents submit claims without an account. Everything else is staff.
        if self.action == 'create':
            return [AllowAny()]
        return [IsAccountantOrAdmin()]

    def get_throttles(self):
        if self.action == 'create':
            return [ClaimSubmitThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        claim = serializer.save(id=serializer.validated_data.get('id') or new_id('claim'))
        if not claim.transaction_id:
            claim.status = 'NEEDS_REVIEW'
            claim.match_reason = 'Claim has no transaction ID. / Hakuna namba ya muamala.'
            claim.save()
        else:
            _automatch_existing_row(claim, performed_by='system')
        write_audit(
            student=claim.student,
            action='CLAIM_SUBMIT',
            performed_by=claim.payer_phone,
            after={'amount': str(claim.amount), 'transaction_id': claim.transaction_id},
            reason=f'Claim submitted by parent ({claim.payer_phone})',
        )

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Confirm a claim using its statement row. The statement amount wins."""
        claim = self.get_object()
        if claim.status == 'CONFIRMED':
            return Response(
                {'error': 'Claim is already confirmed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if claim.status == 'REJECTED':
            return Response(
                {'error': 'Claim was rejected.'}, status=status.HTTP_400_BAD_REQUEST
            )

        row = claim.statement_rows.filter(status__in=ACTIVE_ROW_STATUSES).first()
        if row is None:
            return Response(
                {
                    'error': 'no_statement_row',
                    'detail': (
                        'This claim has no matching statement row. Money that is not '
                        'on the till statement cannot be approved.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get('reason') or claim.match_reason or '').strip()
        try:
            result = confirm_payment(
                student=claim.student,
                amount=row.amount,
                reference=row.transaction_id,
                payment_date=row.date,
                method=claim.method or 'Lipa Namba',
                notes=reason or f'Payment claim {claim.id}',
                source='CLAIM',
                statement_row=row,
                claim=claim,
                performed_by=_actor(request),
                audit_action='CLAIM_APPROVE',
            )
        except DuplicateTransactionError as exc:
            return Response(
                {'error': 'duplicate_transaction', 'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except AllocationError as exc:
            return Response(
                {'error': 'allocation_error', 'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'claim': PaymentClaimSerializer(claim).data,
                'payment': PaymentSerializer(result.payment).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        claim = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response(
                {'error': 'A rejection reason is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        actor = _actor(request)
        previous_status = claim.status
        claim.status = 'REJECTED'
        claim.rejection_reason = reason
        claim.reviewed_at = timezone.now()
        claim.reviewed_by = actor
        claim.save()
        write_audit(
            student=claim.student,
            action='CLAIM_REJECT',
            performed_by=actor,
            before={'status': previous_status},
            after={'status': 'REJECTED'},
            reason=reason,
        )
        return Response(PaymentClaimSerializer(claim).data)

    @action(detail=False, methods=['post'])
    def scan_not_found(self, request):
        """Apply the configurable N-day NOT_FOUND rule and restore late rows."""
        days = int(request.data.get('days') or get_setting('not_found_days') or 3)
        actor = _actor(request)

        claims = list(
            PaymentClaim.objects.select_related('student').filter(
                status__in=['PENDING', 'NEEDS_REVIEW', 'NOT_FOUND']
            )
        )
        rows = list(StatementRow.objects.exclude(status__in=INACTIVE_ROW_STATUSES))
        result = find_not_found(claims, rows, days=days)

        marked, restored = [], []
        for claim in result.not_found:
            previous_status = claim.status
            claim.status = 'NOT_FOUND'
            claim.match_reason = f'No statement row found {days} days after submission.'
            claim.save()
            write_audit(
                student=claim.student,
                action='CLAIM_NOT_FOUND',
                performed_by=actor,
                before={'status': previous_status},
                after={'status': 'NOT_FOUND'},
                reason=f'No statement row within {days} days',
            )
            marked.append(claim.id)

        for claim in result.restored:
            claim.status = 'NEEDS_REVIEW'
            claim.match_reason = 'Statement row received after review. Verify and approve.'
            claim.save()
            write_audit(
                student=claim.student,
                action='CLAIM_NOT_FOUND',
                performed_by=actor,
                before={'status': 'NOT_FOUND'},
                after={'status': 'NEEDS_REVIEW'},
                reason='Statement row found after NOT_FOUND review',
            )
            restored.append(claim.id)

        return Response({'not_found': marked, 'restored': restored, 'days': days})


class StatementViewSet(viewsets.ViewSet):
    """Statement imports, rows, matching and one-click assignment."""

    permission_classes = [IsAccountantOrAdmin]

    def list(self, request):
        if request.query_params.get('resource') == 'rows':
            qs = StatementRow.objects.select_related(
                'claim', 'student', 'import_record'
            ).all()
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
    @transaction.atomic
    def imports(self, request):
        """Import a till statement.

        The server, not the browser, decides every match. An exact match is
        confirmed immediately using the statement amount; a transaction-ID
        match with a different amount or payer goes to review with a reason.
        Re-uploading the same file returns the original import instead of
        creating duplicate payments.
        """
        actor = _actor(request)
        file_name = request.data.get('file_name') or 'statement.csv'
        file_hash = (request.data.get('file_hash') or '').strip()
        mapping = request.data.get('mapping') or {}
        rows_data = request.data.get('rows') or []
        if not isinstance(rows_data, list):
            return Response(
                {'error': 'rows must be a list.'}, status=status.HTTP_400_BAD_REQUEST
            )

        if file_hash:
            existing = StatementImport.objects.filter(file_hash=file_hash).first()
            if existing:
                rows = StatementRow.objects.filter(
                    import_record=existing
                ).select_related('claim', 'student')
                return Response(
                    {
                        'duplicate_file': True,
                        'detail': (
                            f'"{existing.file_name}" was already imported; '
                            'returning the existing import.'
                        ),
                        'import': StatementImportSerializer(existing).data,
                        'rows': StatementRowSerializer(rows, many=True).data,
                        'payments': [],
                    },
                    status=status.HTTP_200_OK,
                )

        parsed = []
        for raw in rows_data:
            if not isinstance(raw, dict):
                continue
            parsed.append(
                {
                    'transaction_id': (raw.get('transaction_id') or '').strip().upper(),
                    'date': parse_date(raw.get('date')),
                    'amount': money(raw.get('amount')),
                    'sender_phone': raw.get('sender_phone') or '',
                    'sender_name': raw.get('sender_name') or '',
                    'receiver_till': raw.get('receiver_till') or '',
                    'direction': (raw.get('direction') or 'IN').strip().upper(),
                }
            )

        open_claims = list(
            PaymentClaim.objects.select_related('student__grade').filter(
                status__in=OPEN_CLAIM_STATUSES
            )
        )
        students = list(Student.objects.select_related('grade').all())
        existing_txns = set(Payment.objects.values_list('reference', flat=True))
        existing_txns |= set(
            StatementRow.objects.filter(status__in=ACTIVE_ROW_STATUSES)
            .values_list('transaction_id', flat=True)
        )
        result = match_rows(
            parsed,
            open_claims,
            students,
            existing_transaction_ids=existing_txns,
        )
        claims_by_id = {claim.id: claim for claim in open_claims}

        import_record = StatementImport.objects.create(
            id=new_id('import'),
            file_name=file_name,
            file_hash=file_hash or new_id('hash'),
            mapping=mapping,
            uploaded_by=actor,
        )
        if mapping:
            AppSetting.objects.update_or_create(
                key='csv_mapping', defaults={'value': mapping}
            )

        created_payments = []
        imported = skipped = matched_claims = 0

        for row_match, row_data in zip(result.rows, parsed):
            row = StatementRow.objects.create(
                id=new_id('srow'),
                import_record=import_record,
                transaction_id=row_data['transaction_id'],
                date=row_data['date'],
                amount=row_data['amount'],
                sender_phone=row_data['sender_phone'],
                sender_name=row_data['sender_name'],
                receiver_till=row_data['receiver_till'],
                direction=(
                    row_data['direction']
                    if row_data['direction'] in ('IN', 'OUT')
                    else 'IN'
                ),
                status=(
                    row_match.status
                    if row_match.status in ('UNCLAIMED', 'MATCHED', 'DUPLICATE', 'SKIPPED')
                    else 'UNCLAIMED'
                ),
                match_reason=row_match.reason,
            )

            if row_match.status in ('SKIPPED', 'DUPLICATE'):
                skipped += 1
                continue

            imported += 1
            if row_match.status != 'MATCHED' or not row_match.claim_id:
                continue

            claim = claims_by_id.get(row_match.claim_id)
            if claim is None:
                continue

            if row_match.match_type == 'EXACT':
                try:
                    confirmation = confirm_payment(
                        student=claim.student,
                        amount=row.amount,
                        reference=row.transaction_id,
                        payment_date=row.date,
                        method=claim.method or 'Lipa Namba',
                        notes=f'Payment claim {claim.id}',
                        source='CLAIM',
                        statement_row=row,
                        claim=claim,
                        performed_by=actor,
                    )
                    created_payments.append(
                        PaymentSerializer(confirmation.payment).data
                    )
                    matched_claims += 1
                except (DuplicateTransactionError, AllocationError) as exc:
                    claim.status = 'NEEDS_REVIEW'
                    claim.match_reason = str(exc)
                    claim.save()
                    row.status = 'UNCLAIMED'
                    row.claim = None
                    row.student = None
                    row.match_reason = str(exc)
                    row.save()
                    imported -= 1
                    skipped += 1
                continue

            row.status = 'MATCHED'
            row.claim = claim
            row.student = claim.student
            row.match_type = row_match.match_type or 'AMOUNT_MISMATCH'
            row.match_reason = row_match.reason
            row.save()

            claim.status = 'NEEDS_REVIEW'
            claim.statement_amount = row.amount
            claim.variance = money(row.amount - money(claim.amount))
            claim.match_reason = row_match.reason
            claim.save()
            matched_claims += 1

        # A claim with no transaction ID can never match automatically.
        for claim in open_claims:
            if not claim.transaction_id and claim.status == 'PENDING':
                claim.status = 'NEEDS_REVIEW'
                claim.match_reason = (
                    'Claim has no transaction ID. / Hakuna namba ya muamala.'
                )
                claim.save()

        import_record.total_rows = len(rows_data)
        import_record.imported_rows = imported
        import_record.skipped_rows = skipped
        import_record.matched_claims = matched_claims
        import_record.save()

        write_audit(
            action='CSV_IMPORT',
            performed_by=actor,
            after={
                'file_name': file_name,
                'total_rows': len(rows_data),
                'imported_rows': imported,
                'skipped_rows': skipped,
                'matched_claims': matched_claims,
            },
            reason=(
                f'Imported statement "{file_name}" '
                f'({imported} rows, {matched_claims} claims matched)'
            ),
        )

        rows = StatementRow.objects.filter(
            import_record=import_record
        ).select_related('claim', 'student')
        return Response(
            {
                'import': StatementImportSerializer(import_record).data,
                'rows': StatementRowSerializer(rows, many=True).data,
                'payments': created_payments,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def assign_row(self, request, pk=None):
        """Assign an unclaimed statement row to a student (one click)."""
        try:
            row = StatementRow.objects.select_related('import_record').get(pk=pk)
        except StatementRow.DoesNotExist:
            return Response(
                {'error': 'Row not found.'}, status=status.HTTP_404_NOT_FOUND
            )

        if row.status in ('DUPLICATE', 'SKIPPED'):
            return Response(
                {'error': f'Row is {row.status.lower()} and cannot be assigned.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if row.status == 'MATCHED' and row.claim_id:
            return Response(
                {
                    'error': 'row_belongs_to_claim',
                    'detail': 'Approve or reject the linked claim instead.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if row.direction == 'OUT':
            return Response(
                {'error': 'Outgoing rows cannot be assigned.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student_id = request.data.get('student_id')
        if not student_id:
            return Response(
                {'error': 'student_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            student = Student.objects.select_related('grade').get(pk=student_id)
        except Student.DoesNotExist:
            return Response(
                {'error': 'Student not found.'}, status=status.HTTP_404_NOT_FOUND
            )

        note = (request.data.get('note') or '').strip()
        try:
            result = confirm_payment(
                student=student,
                amount=row.amount,
                reference=row.transaction_id,
                payment_date=row.date,
                method='Lipa Namba',
                notes=note or f'Assigned from statement row {row.id}',
                source='MANUAL',
                statement_row=row,
                performed_by=_actor(request),
                row_status='ASSIGNED',
                row_match_type='ASSIGNED',
                audit_action='ROW_ASSIGN',
            )
        except DuplicateTransactionError as exc:
            return Response(
                {'error': 'duplicate_transaction', 'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except AllocationError as exc:
            return Response(
                {'error': 'allocation_error', 'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        row.refresh_from_db()
        return Response(
            {
                'row': StatementRowSerializer(row).data,
                'payment': PaymentSerializer(result.payment).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AllocationViewSet(viewsets.ViewSet):
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAccountantOrAdmin()]
        return [IsAdmin()]

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
        """Persist allocations computed by the client allocation engine.

        Used for the demo preview and legacy paths. New work should use
        ``reallocate`` below, which recomputes everything on the server.
        """
        allocations = request.data.get('allocations') or []
        reversed_ids = request.data.get('reversed_ids') or []
        audit_data = request.data.get('audit') or {}
        actor = _actor(request)

        if not isinstance(allocations, list) or not isinstance(reversed_ids, list):
            return Response(
                {'error': 'allocations and reversed_ids must be lists.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            audit_data.get('action') == 'REALLOCATE'
            and not (audit_data.get('reason') or '').strip()
        ):
            return Response(
                {'error': 'A reason is required for reallocation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        with transaction.atomic():
            for item in allocations:
                payment = Payment.objects.filter(id=item.get('payment')).first()
                student = Student.objects.filter(id=item.get('student')).first()
                if not payment or not student:
                    return Response(
                        {'error': 'Unknown payment or student in allocations.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                amount = money(item.get('amount'))
                if amount <= 0:
                    return Response(
                        {'error': 'Allocation amounts must be greater than zero.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                source = item.get('source') or (
                    'REALLOCATION'
                    if audit_data.get('action') == 'REALLOCATE'
                    else 'PRIORITY'
                )
                obj = Allocation.objects.create(
                    id=item.get('id') or new_id('alloc'),
                    payment=payment,
                    student=student,
                    fee_line_id=item.get('fee_line_id', ''),
                    fee_group_name=item.get('fee_group_name', ''),
                    fee_item_label=item.get('fee_item_label', ''),
                    amount=amount,
                    source=source,
                    reason=item.get('reason') or audit_data.get('reason') or '',
                    created_by=item.get('created_by') or actor,
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
                    performed_by=actor,
                    before=audit_data.get('before') or {},
                    after=audit_data.get('after') or {},
                    reason=audit_data.get('reason') or '',
                )

        return Response(
            AllocationSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'])
    def reallocate(self, request):
        """Move money from one confirmed payment to a different fee line.

        The server recomputes the payment's allocations, so the result does not
        depend on the browser's preview. A reason is mandatory and audited.
        """
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response(
                {'error': 'A reason is required for reallocation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = Payment.objects.select_related('student__grade').filter(
            id=request.data.get('payment_id')
        ).first()
        if payment is None:
            return Response(
                {'error': 'Payment not found.'}, status=status.HTTP_404_NOT_FOUND
            )
        target = (request.data.get('target_fee_line_id') or '').strip()
        if not target:
            return Response(
                {'error': 'target_fee_line_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student = payment.student
        existing = list(
            Allocation.objects.filter(student=student, reversed=False)
        )
        try:
            plan = plan_reallocation(
                payment_id=payment.id,
                payment_amount=payment.amount,
                fee_lines=build_fee_lines(student.grade),
                existing_allocations=existing,
                target_fee_line_id=target,
                amount=request.data.get('amount'),
                performed_by=_actor(request),
                reason=reason,
            )
        except AllocationError as exc:
            return Response(
                {'error': 'allocation_error', 'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor = _actor(request)
        with transaction.atomic():
            if plan.reversed_ids:
                Allocation.objects.filter(id__in=plan.reversed_ids).update(reversed=True)
            created = []
            for planned in plan.allocations:
                created.append(
                    Allocation.objects.create(
                        id=new_id('alloc'),
                        payment=payment,
                        student=student,
                        fee_line_id=planned.fee_line_id,
                        fee_group_name=planned.fee_group_name,
                        fee_item_label=planned.fee_item_label,
                        amount=planned.amount,
                        source='REALLOCATION',
                        reason=reason,
                        created_by=actor,
                    )
                )

            credit = Credit.objects.filter(payment=payment).first()
            if plan.credit > 0:
                if credit is None:
                    Credit.objects.create(
                        id=new_id('credit'),
                        student=student,
                        payment=payment,
                        amount=plan.credit,
                        remaining_amount=plan.credit,
                        reason=reason,
                        created_by=actor,
                    )
                else:
                    credit.remaining_amount = plan.credit
                    credit.save()
            elif credit is not None:
                credit.remaining_amount = money(0)
                credit.save()

            write_audit(
                student=student,
                payment=payment,
                action='REALLOCATE',
                performed_by=actor,
                before={
                    'allocations': [
                        {'fee_line_id': a.fee_line_id, 'amount': str(a.amount)}
                        for a in existing
                        if a.payment_id == payment.id
                    ]
                },
                after={
                    'target_fee_line_id': target,
                    'allocations': [
                        {'fee_line_id': a.fee_line_id, 'amount': str(a.amount)}
                        for a in plan.allocations
                    ],
                    'credit': str(plan.credit),
                },
                reason=reason,
            )

        return Response(
            AllocationSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class CreditViewSet(viewsets.ViewSet):
    """List and apply overpayment credits. Applying money is admin-only."""

    def get_permissions(self):
        if self.action == 'list':
            return [IsAccountantOrAdmin()]
        return [IsAdmin()]

    def list(self, request):
        qs = Credit.objects.select_related('student', 'payment').all()
        student_id = request.query_params.get('student')
        if student_id:
            qs = qs.filter(student=student_id)
        return Response(CreditSerializer(qs[:1000], many=True).data)

    @action(detail=True, methods=['post'])
    def apply(self, request, pk=None):
        credit = Credit.objects.select_related('student__grade', 'payment').filter(
            pk=pk
        ).first()
        if credit is None:
            return Response(
                {'error': 'Credit not found.'}, status=status.HTTP_404_NOT_FOUND
            )
        if credit.payment_id is None:
            return Response(
                {'error': 'This credit has no source payment to allocate.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response(
                {'error': 'A reason is required to apply a credit.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = money(request.data.get('amount') or credit.remaining_amount)
        if amount <= 0 or amount > money(credit.remaining_amount):
            return Response(
                {
                    'error': (
                        f'Amount must be between 0.01 and '
                        f'{money(credit.remaining_amount):,.2f} Tsh.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_id = (request.data.get('target_fee_line_id') or '').strip()
        lines = build_fee_lines(credit.student.grade)
        line_map = {line.id: line for line in lines}
        target = line_map.get(target_id)
        if target is None:
            return Response(
                {'error': 'Target fee line not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        paid = sum(
            (
                money(allocation.amount)
                for allocation in Allocation.objects.filter(
                    student=credit.student,
                    fee_line_id=target_id,
                    reversed=False,
                )
            ),
            money(0),
        )
        due = target.amount - paid
        if amount > due:
            return Response(
                {
                    'error': (
                        f'Target line only has {due:,.2f} Tsh due; cannot apply '
                        f'{amount:,.2f} Tsh.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor = _actor(request)
        remaining_before = credit.remaining_amount
        with transaction.atomic():
            allocation = Allocation.objects.create(
                id=new_id('alloc'),
                payment=credit.payment,
                student=credit.student,
                fee_line_id=target.id,
                fee_group_name=target.fee_group_name,
                fee_item_label=target.fee_item_label,
                amount=amount,
                source='CREDIT',
                reason=reason,
                created_by=actor,
            )
            credit.remaining_amount = money(remaining_before) - amount
            credit.save()
            write_audit(
                student=credit.student,
                payment=credit.payment,
                action='CREDIT_APPLY',
                performed_by=actor,
                before={'remaining_amount': str(remaining_before)},
                after={
                    'remaining_amount': str(credit.remaining_amount),
                    'target_fee_line_id': target.id,
                    'amount': str(amount),
                },
                reason=reason,
            )

        return Response(
            {
                'credit': CreditSerializer(credit).data,
                'allocation': AllocationSerializer(allocation).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AuditViewSet(viewsets.ViewSet):
    def get_permissions(self):
        if self.action == 'list':
            return [IsAccountantOrAdmin()]
        return [IsAdmin()]

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
            return Response(
                {'error': 'action is required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        log = write_audit(
            student=student,
            payment=payment,
            action=request.data['action'],
            performed_by=_actor(request, fallback='system'),
            before=request.data.get('before') or {},
            after=request.data.get('after') or {},
            reason=request.data.get('reason') or '',
        )
        return Response(AuditLogSerializer(log).data, status=status.HTTP_201_CREATED)


class SettingsViewSet(viewsets.ViewSet):
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAccountantOrAdmin()]
        return [IsAdmin()]

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
        if not isinstance(data, dict):
            return Response(
                {'error': 'Settings must be a key/value object.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = {}
        saved = {}
        for k, v in data.items():
            if k not in DEFAULT_SETTINGS:
                return Response(
                    {'error': f'Unknown setting "{k}".'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            existing = AppSetting.objects.filter(key=k).first()
            before[k] = existing.value if existing else DEFAULT_SETTINGS.get(k)
            AppSetting.objects.update_or_create(key=k, defaults={'value': v})
            saved[k] = v

        if saved:
            write_audit(
                action='SETTINGS_CHANGE',
                performed_by=_actor(request, fallback='admin'),
                before=before,
                after=saved,
                reason='Settings updated',
            )
        return Response([{'key': k, 'value': v} for k, v in saved.items()])
