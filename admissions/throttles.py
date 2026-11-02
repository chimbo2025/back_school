"""Rate limits for public, unauthenticated endpoints.

Parents must be able to check a student's status without a password in this
version, so throttling is the main protection against enumerating student
numbers and phone combinations. Limits are configured in
``settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``.
"""

from rest_framework.throttling import SimpleRateThrottle


class ScopedThrottle(SimpleRateThrottle):
    """Throttle authenticated users by id and anonymous users by IP."""

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f'user:{request.user.pk}'
        else:
            ident = f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class PublicLookupThrottle(ScopedThrottle):
    scope = 'public_lookup'


class ClaimSubmitThrottle(ScopedThrottle):
    scope = 'claim_submit'
