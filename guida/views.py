from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .models import VoceGuida
from .registry import elenco_legacy, elenco_moduli, get_modulo, legacy_per_modulo


@login_required
def indice_guida(request):
    """Elenco moduli (con classificazione evolutiva) + mappa aree legacy."""
    return render(
        request,
        'guida/indice.html',
        {
            'moduli': elenco_moduli(),
            'legacy_aree': elenco_legacy(),
        },
    )


@login_required
def guida_modulo(request, codice_modulo: str):
    """Tutte le voci attive per un modulo + ancore per campo."""
    info = get_modulo(codice_modulo)
    if not info:
        raise Http404('Modulo non registrato')
    voci = list(
        VoceGuida.objects.filter(codice_modulo=codice_modulo, attiva=True).order_by('ordine', 'codice_campo', 'titolo'),
    )
    return render(
        request,
        'guida/modulo.html',
        {
            'modulo': info,
            'voci': voci,
            'legacy_collegate': legacy_per_modulo(codice_modulo),
        },
    )
