from django.contrib import admin
from django.urls import path, include, re_path, reverse
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.http import FileResponse
from django.shortcuts import redirect
from accounts.views import logout_view
from views import home as centro_moduli_view
from views import root_redirect

admin.site.site_url = getattr(settings, "GESPER_ADMIN_SITE_URL", "/moduli/")


def admin_login_redirect_view(request):
    return redirect(f"{reverse('login')}?next={reverse('admin:index')}")

urlpatterns = [
    path('', root_redirect, name='root_login_redirect'),
    path('home/', RedirectView.as_view(pattern_name='profile', permanent=False), name='home'),
    path('moduli/', centro_moduli_view, name='centro_moduli'),
    path('admin/login/', admin_login_redirect_view, name='admin_login_redirect'),
    path('admin/logout/', logout_view),  # override: logout admin → pagina principale
    path('admin/', admin.site.urls),
    path('presenze/', include('presenze.urls')),
    path('anagrafiche/', include('anagrafiche.urls')),
    path('documenti/', include('documenti.urls')),
    path('richieste/', include('richieste.urls')),
    path('rapporti/', include('rapporto_di_lavoro.urls')),
    path('workflow/', include('workflow.urls')),

    path('accounts/', include('accounts.urls')),
    path('candidato/', include('accounts.urls_candidato')),
    path('log/', include('log_attivita.urls')),
    path('storico/', include('storico.urls')),
    path('guida/', include('guida.urls')),
    path('api/', include('api.urls')),
]

# PWA dipendenti: servita anche con DEBUG=False (produzione).
# Cartella: htdocs/gesper-app (sibling) o gesper/gesper-app
_pwa_a = settings.BASE_DIR / 'gesper-app'
_pwa_b = settings.BASE_DIR.parent / 'gesper-app'
PWA_ROOT = _pwa_b if _pwa_b.is_dir() else _pwa_a


def pwa_index(request):
    return FileResponse(open(PWA_ROOT / 'index.html', 'rb'), content_type='text/html')


if PWA_ROOT.is_dir():
    urlpatterns += [
        path('gesper-app', RedirectView.as_view(pattern_name='pwa_index', permanent=False)),
        re_path(
            r'^gesper-app/undefined/?$',
            RedirectView.as_view(pattern_name='pwa_index', permanent=False),
        ),
        path('gesper-app/', pwa_index, name='pwa_index'),
        re_path(r'^gesper-app/(?P<path>.+)$', serve, {'document_root': PWA_ROOT}),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
