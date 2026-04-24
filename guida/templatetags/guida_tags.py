from django import template
from django.urls import reverse

from guida.models import VoceGuida

register = template.Library()


@register.inclusion_tag('guida/_link_aiuto.html')
def guida_link(codice_modulo, codice_campo=''):
    """
    Link contestuale alla guida (modulo o campo).

    Uso: {% load guida_tags %}
         {% guida_link "reg-dipendente" %}
         {% guida_link "reg-dipendente" "email-lavoro" %}
    """
    url_modulo = reverse('guida_modulo', kwargs={'codice_modulo': codice_modulo})
    anchor = codice_campo or 'intro'
    fragment = f'#campo-{anchor}'
    ha_voce = VoceGuida.objects.filter(
        codice_modulo=codice_modulo,
        codice_campo=codice_campo or '',
        attiva=True,
    ).exists()
    return {
        'url': f'{url_modulo}{fragment}',
        'codice_modulo': codice_modulo,
        'codice_campo': codice_campo,
        'ha_voce': ha_voce,
    }
