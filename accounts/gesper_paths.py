# -*- coding: utf-8 -*-
"""Path pubblici coerenti con ``FORCE_SCRIPT_NAME`` (web + API)."""
from __future__ import annotations

from django.urls import get_script_prefix, reverse


def portal_web_base_path(request=None) -> str:
    """Path base del portale web (``/`` o ``/gesper/``)."""
    sn = ''
    if request is not None:
        sn = (request.META.get('SCRIPT_NAME') or '').strip()
    if not sn:
        sn = (get_script_prefix() or '').strip()
    if not sn or sn == '/':
        return '/'
    return sn if sn.endswith('/') else sn + '/'


def api_base_path() -> str:
    """Path della root API REST (es. ``/api/`` o ``/gesper/api/``)."""
    me = reverse('api_me').rstrip('/')
    if me.endswith('/me'):
        root = me[:-3]
        return (root + '/') if root else '/'
    return me + '/' if not me.endswith('/') else me


def pwa_app_path(request=None) -> str:
    """URL path della PWA dipendenti (login app), es. ``/gesper-app/`` o ``/gesper/gesper-app/``."""
    base = portal_web_base_path(request)
    if base == '/':
        return '/gesper-app/'
    return f'{base.rstrip("/")}/gesper-app/'


def logout_landing_path(request) -> str:
    """
    Destinazione dopo logout dal portale web.

    - Portale HR / sito: login classico ``/accounts/login/``.
    - Uscita dalla PWA (form con ``dest=pwa`` o referer ``/gesper-app/``): login PWA.
    """
    if request is None:
        return reverse('login')

    dest = (request.GET.get('dest') or request.POST.get('dest') or '').strip().lower()
    if dest == 'pwa':
        return pwa_app_path(request)

    referer = (request.META.get('HTTP_REFERER') or '')
    if '/gesper-app' in referer:
        return pwa_app_path(request)

    return reverse('login')
