"""Mark parent claims that never appear on a till statement as NOT_FOUND.

Deployments can schedule this command (for example once a day) and the review
queue also exposes the same scan on demand. Keeping it in a management command
means the repository does not need a cron dependency.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from admissions.models import PaymentClaim
from admissions.verification import ACTIVE_ROW_STATUSES, get_setting, write_audit


class Command(BaseCommand):
    help = 'Mark claims with no statement row after N days as NOT_FOUND (and restore any that now have a row).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Override the configured not_found_days setting.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would change without saving anything.',
        )

    def handle(self, *args, **options):
        days = options['days'] or get_setting('not_found_days') or 3
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=days)
        marked, restored = [], []

        for claim in PaymentClaim.objects.filter(status__in=['PENDING', 'NEEDS_REVIEW']):
            if claim.statement_rows.filter(status__in=ACTIVE_ROW_STATUSES).exists():
                continue
            if claim.submitted_at > cutoff:
                continue
            marked.append(claim.id)
            if dry_run:
                continue
            previous_status = claim.status
            claim.status = 'NOT_FOUND'
            claim.match_reason = f'No statement row found {days} days after submission.'
            claim.save(update_fields=['status', 'match_reason'])
            write_audit(
                student=claim.student,
                action='CLAIM_NOT_FOUND',
                performed_by='scan_not_found',
                before={'status': previous_status},
                after={'status': 'NOT_FOUND'},
                reason=f'No statement row within {days} days',
            )

        for claim in PaymentClaim.objects.filter(status='NOT_FOUND'):
            if not claim.statement_rows.filter(status__in=ACTIVE_ROW_STATUSES).exists():
                continue
            restored.append(claim.id)
            if dry_run:
                continue
            claim.status = 'NEEDS_REVIEW'
            claim.match_reason = 'Statement row received after review. Verify and confirm.'
            claim.save(update_fields=['status', 'match_reason'])
            write_audit(
                student=claim.student,
                action='CLAIM_NOT_FOUND',
                performed_by='scan_not_found',
                before={'status': 'NOT_FOUND'},
                after={'status': 'NEEDS_REVIEW'},
                reason='Statement row received after review',
            )

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(
            f'{prefix}NOT_FOUND scan ({days} days): {len(marked)} marked, {len(restored)} restored.'
        )
        if marked:
            self.stdout.write(f'{prefix}Marked: {", ".join(marked)}')
        if restored:
            self.stdout.write(f'{prefix}Restored: {", ".join(restored)}')
