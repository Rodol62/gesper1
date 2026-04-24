

from django.contrib.auth.decorators import user_passes_test, login_required
from django.core.exceptions import PermissionDenied

# Funzioni di controllo ruolo
def is_admin(user):
    return user.is_authenticated and (user.is_superuser or user.has_ruolo('admin'))

def is_hr(user):
    return user.is_authenticated and (user.is_superuser or user.has_ruolo('hr'))

def is_dipendente(user):
    return user.is_authenticated and user.has_ruolo('dipendente')

def is_consulente(user):
    return user.is_authenticated and user.has_ruolo('consulente')

# Decoratori granulari
admin_required = user_passes_test(is_admin)
hr_required = user_passes_test(is_hr)
dipendente_required = user_passes_test(is_dipendente)
consulente_required = user_passes_test(is_consulente)

# Decoratore per permessi custom
def permission_required(perm):
    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.has_perm(perm):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return login_required(_wrapped_view)
    return decorator
