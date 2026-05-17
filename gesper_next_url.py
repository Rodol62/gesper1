# -*- coding: utf-8 -*-
"""Parametro `next` sicuro per redirect e viewer (anti open-redirect)."""
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme


def _default_internal_next_path():
	base = (getattr(settings, 'GESPER_ADMIN_SITE_URL', None) or '/moduli/').strip()
	return base if base else '/moduli/'


def sanitize_internal_next(request, next_val, default=None):
	"""
	Accetta solo path relativi sicuri o URL assoluti sullo stesso host della richiesta.
	"""
	if default is None:
		default = _default_internal_next_path()
	if not next_val:
		return default
	raw = (next_val or '').strip()
	if not raw:
		return default
	if raw.startswith('/') and not raw.startswith('//'):
		return raw
	if url_has_allowed_host_and_scheme(
		raw,
		allowed_hosts={request.get_host()},
		require_https=request.is_secure(),
	):
		return raw
	return default
