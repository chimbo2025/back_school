import uuid
from datetime import date, datetime

from django.utils import timezone


def new_id(prefix):
    return f"{prefix}-{int(timezone.now().timestamp() * 1000)}-{uuid.uuid4().hex[:6]}"


def parse_date(value, default=None):
    """Parse the several date shapes a CSV or browser can send.

    Returns ``default`` (today when omitted) for blank or unparseable values so
    an import never crashes on one malformed row.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return default or timezone.localdate()
    text = str(value).strip()
    if not text:
        return default or timezone.localdate()
    for candidate in (text[:10], text):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y/%m/%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return default or timezone.localdate()
