from django.core.exceptions import PermissionDenied
from functools import wraps

def role_required(allowed_roles=[]):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Check if user is in any of the allowed roles
            if request.user.role in allowed_roles:
                return view_func(request, *args, **kwargs)
            else:
                # Raise 403 Forbidden
                raise PermissionDenied
        return wrapper
    return decorator