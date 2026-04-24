"""
Risoluzione del record Dipendente per il portale candidato/dipendente.

Usata da views candidato, documenti e presenze per evitare logiche duplicate
e permessi incoerenti (es. lista documenti vs download).
"""


def get_dipendente_collegato(user):
    """Restituisce il Dipendente collegato all'utente, se presente.

    Ordine:
    1) Dipendente.utente (collegamento HR / anagrafica — fonte di verità per buste e CUD)
    2) ProfiloCandidato.dipendente (percorso candidatura)
    3) Codice fiscale del profilo + azienda utente (se manca il FK utente sul dipendente)
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    from anagrafiche.models import Dipendente

    dip = Dipendente.objects.filter(utente=user).select_related('azienda').first()
    if dip:
        return dip

    profilo = getattr(user, 'profilo_candidato', None)
    if profilo and profilo.dipendente_id:
        return profilo.dipendente

    cf = (getattr(profilo, 'codice_fiscale', None) or '').strip().upper()
    az_id = getattr(user, 'azienda_id', None)
    if cf and len(cf) >= 11 and az_id:
        dip_cf = Dipendente.objects.filter(
            codice_fiscale__iexact=cf,
            azienda_id=az_id,
        ).first()
        if dip_cf:
            return dip_cf

    return None
