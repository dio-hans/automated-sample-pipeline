from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect


class RoleRequiredMixin:
    """Restrict a class-based view to authenticated users with approved roles."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")

        if request.user.is_superuser or request.user.role in self.allowed_roles:
            return super().dispatch(request, *args, **kwargs)

        messages.error(request, "You do not have permission to access this area.")
        return redirect("dashboard")


def role_required(*roles):
    """Restrict a function-based view to authenticated users with approved roles."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")

            if request.user.is_superuser or request.user.role in roles:
                return view_func(request, *args, **kwargs)

            messages.error(request, "You do not have permission to access this area.")
            return redirect("dashboard")

        return wrapped

    return decorator


def admin_required(view_func):
    return role_required("ADMIN")(view_func)
