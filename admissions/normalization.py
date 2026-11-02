"""Small, dependency-free normalisers shared by models, services and commands.

Keeping these in one module means the same rules are used when a parent
registers, when an accountant imports a till statement and when the matcher
compares the two. The frontend has a mirror copy in ``src/lib/matching.ts`` for
instant preview, but the backend is authoritative.
"""

import re

_PHONE_RE = re.compile(r'^0\d{9}$')


def normalize_phone(phone):
    """Return a Tanzanian phone number as ``0XXXXXXXXX`` or ``''``.

    Accepts ``0777123456``, ``+255777123456``, ``255777123456``,
    ``777123456`` and the same values with spaces, dashes or dots.
    Invalid values return an empty string instead of raising, so callers can
    decide whether that is a validation error or simply "unknown payer".
    """
    if not phone:
        return ''
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return ''
    if digits.startswith('255'):
        digits = '0' + digits[3:]
    elif len(digits) == 9:
        digits = '0' + digits
    return digits if _PHONE_RE.match(digits) else ''


def is_valid_phone(phone):
    return normalize_phone(phone) != ''
