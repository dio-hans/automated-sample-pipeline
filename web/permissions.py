
from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect


def user_has_any_role(user, allowed_roles):
    """Central role inheritance and authorization."""
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    allowed = set(allowed_roles)

    # Production Manager inherits all Store Manager permissions.
    if "MANAGER" in allowed:
        allowed.add("PRODUCTION")

    return user.role in allowed


class RoleRequiredMixin:
    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")

        if user_has_any_role(request.user, self.allowed_roles):
            return super().dispatch(request, *args, **kwargs)

        messages.error(
            request,
            "You do not have permission to access this area."
        )
        return redirect("dashboard")


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")

            if user_has_any_role(request.user, roles):
                return view_func(request, *args, **kwargs)

            messages.error(
                request,
                "You do not have permission to access this area."
            )
            return redirect("dashboard")

        return wrapped
    return decorator


def admin_required(view_func):
    return role_required("ADMIN")(view_func)
