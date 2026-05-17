from anagrafiche.models import Azienda


def get_azienda_operativa(user, session):
    """Restituisce l'azienda operativa corrente.

    Priorit?:
    1) session['azienda_id'] (se valido); in assenza, legacy session['azienda_operativa_id']
    2) user.azienda
    """
    azienda_id = session.get('azienda_id') or session.get('azienda_operativa_id')
    if azienda_id:
        azienda = Azienda.objects.filter(id=azienda_id).first()
        if azienda:
            return azienda
    return getattr(user, 'azienda', None)
