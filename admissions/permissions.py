"""Role permissions for the school portal.

The requirement's roles map onto Django's built-in flags:

* Parent  -> anonymous, public status/claim endpoints only
* Accountant -> authenticated staff (``is_staff``)
* Admin   -> superuser (``is_superuser``)

The frontend role switcher is a demo affordance; these classes are the real
enforcement.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAccountantOrAdmin(BasePermission):
    message = 'Staff access is required. / Ufikiaji wa wafanyakazi unahitajika.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.is_superuser)
        )


class IsAdmin(BasePermission):
    message = 'Administrator access is required. / Ufikiaji wa msimamizi unahitajika.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)


class IsAdminOrReadOnlyStaff(BasePermission):
    """Staff may read; only an admin may write."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and (user.is_staff or user.is_superuser)):
            return False
        if request.method in SAFE_METHODS:
            return True
        return bool(user.is_superuser)
