"""Tag template per controlli di paginazione coerenti (GET preservato)."""

from django import template

from accounts.pagination import pagination_window

register = template.Library()


@register.inclusion_tag('includes/gesper_pagination.html', takes_context=True)
def gesper_pagination(context, page_obj, aria_label='Paginazione elenco', page_param='page'):
    """
    Navigazione pagine con numeri (finestra), prima/ultima, indietro/avanti.
    Ricopia tutti i parametri GET della richiesta corrente aggiornando solo `page_param`.
    """
    request = context.get('request')
    if request is None or page_obj is None:
        return {'show': False}

    paginator = getattr(page_obj, 'paginator', None)
    if paginator is None or paginator.num_pages <= 1:
        return {'show': False}

    def url_for(num):
        q = request.GET.copy()
        q[page_param] = str(int(num))
        return '?' + q.urlencode()

    pages = []
    for n in pagination_window(page_obj):
        if n is None:
            pages.append({'ellipsis': True})
        else:
            pages.append(
                {
                    'n': n,
                    'url': url_for(n),
                    'current': n == page_obj.number,
                }
            )

    return {
        'show': True,
        'page_obj': page_obj,
        'aria_label': aria_label,
        'url_first': url_for(1),
        'url_last': url_for(paginator.num_pages),
        'url_prev': url_for(page_obj.previous_page_number()) if page_obj.has_previous() else None,
        'url_next': url_for(page_obj.next_page_number()) if page_obj.has_next() else None,
        'pages': pages,
    }
