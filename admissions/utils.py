import uuid

from django.utils import timezone


def new_id(prefix):
    return f"{prefix}-{int(timezone.now().timestamp() * 1000)}-{uuid.uuid4().hex[:6]}"
