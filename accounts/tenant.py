from anagrafiche.models import Azienda


def get_azienda_operativa(user, session):
    """Restituisce l'azienda operativa corrente.

    Priorità:
    1) session['azienda_id'] (se valido)
    2) user.azienda
    """
    azienda_id = session.get('azienda_id')
    if azienda_id:
        azienda = Azienda.objects.filter(id=azienda_id).first()
        if azienda:
            return azienda
    return getattr(user, 'azienda', None)
