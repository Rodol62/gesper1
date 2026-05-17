from django import template

register = template.Library()

@register.filter
def has_ruolo(user, ruolo_codice):
    """Restituisce True se l'utente ha il ruolo specificato (es. 'admin', 'hr')."""
    if hasattr(user, 'has_ruolo'):
        return user.has_ruolo(ruolo_codice)
    return False


@register.filter
def get_item(dictionary, key):
    """Restituisce dictionary[key] o None — usato nei template consulente."""
    return dictionary.get(key)
