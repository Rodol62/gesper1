"""Servizi di sincronizzazione anagrafica tra User, ProfiloCandidato e Dipendente."""

from __future__ import annotations

from typing import Optional


def diagnostica_anagrafica_candidato(candidato, profilo):
    """Rileva anomalie pratiche su User/ProfiloCandidato/Dipendente con istruzioni operative."""
    anomalie = []
    if not profilo:
        anomalie.append({
            "livello": "danger",
            "titolo": "Profilo candidato mancante",
            "istruzioni": "Apri 'Modifica profilo' e completa i dati minimi per creare il profilo.",
        })
        return anomalie

    dip = getattr(profilo, "dipendente", None)
    if not dip:
        anomalie.append({
            "livello": "warning",
            "titolo": "Dipendente non collegato al profilo",
            "istruzioni": "Usa 'Riallinea anagrafica' per creare/agganciare il record dipendente.",
        })
        return anomalie

    if not dip.utente_id:
        anomalie.append({
            "livello": "warning",
            "titolo": "Dipendente senza utente collegato",
            "istruzioni": "Usa 'Riallinea anagrafica' per collegare il candidato al dipendente.",
        })
    elif dip.utente_id != candidato.id:
        anomalie.append({
            "livello": "danger",
            "titolo": "Dipendente collegato a un altro utente",
            "istruzioni": "Controlla assegnazioni profilo/proposte e riallinea solo dopo verifica manuale.",
        })

    cf_profilo = (profilo.codice_fiscale or "").strip().upper()
    cf_dip = (dip.codice_fiscale or "").strip().upper()
    if cf_profilo and cf_dip and cf_profilo != cf_dip:
        anomalie.append({
            "livello": "warning",
            "titolo": "Codice fiscale disallineato (profilo vs dipendente)",
            "istruzioni": "Aggiorna il profilo e usa 'Riallinea anagrafica' per propagare i dati su dipendente.",
        })

    if (candidato.email or "").strip().lower() != (dip.email or "").strip().lower():
        anomalie.append({
            "livello": "warning",
            "titolo": "Email disallineata (utente vs dipendente)",
            "istruzioni": "Verifica l'email corretta su utente e poi usa 'Riallinea anagrafica'.",
        })

    if (candidato.first_name or "").strip().upper() != (dip.nome or "").strip().upper() or (
        (candidato.last_name or "").strip().upper() != (dip.cognome or "").strip().upper()
    ):
        anomalie.append({
            "livello": "info",
            "titolo": "Nome/cognome non coerenti tra utente e dipendente",
            "istruzioni": "Allinea nome/cognome utente (fonte primaria) e poi esegui 'Riallinea anagrafica'.",
        })

    return anomalie


def sincronizza_dipendente_da_profilo(user, profilo, create_if_missing=True):
    """
    Allinea (o crea) il Dipendente collegato a un ProfiloCandidato.

    Regole:
    - Fonte anagrafica primaria per candidato: User + ProfiloCandidato.
    - Dipendente viene mantenuto coerente con tali dati.
    - Nessun aggancio forzato a record già usati da altri profili.
    - Se create_if_missing=False non crea nuovi Dipendente.
    """
    from anagrafiche.models import Azienda, Dipendente
    from accounts.models import ProfiloCandidato as _PC

    azienda = profilo.azienda_interesse
    if not azienda:
        azienda = Azienda.objects.first()

    dip_data = {
        "nome": user.first_name or user.username,
        "cognome": user.last_name or "",
        "email": user.email,
        "codice_fiscale": profilo.codice_fiscale or None,
        "data_nascita": profilo.data_nascita,
        "indirizzo": profilo.indirizzo,
        "telefono": profilo.telefono,
        "stato": "candidato",
        "ruolo": profilo.livello_aspirato or "Candidato",
        "livello": profilo.livello_aspirato or "",
    }

    if profilo.dipendente_id:
        dip = profilo.dipendente
        for field, val in dip_data.items():
            setattr(dip, field, val)
        if azienda and not dip.azienda_id:
            dip.azienda = azienda
        if not dip.utente_id:
            dip.utente = user
        dip.save()
        return dip

    # Ricerca preventiva per CF (evita duplicati).
    cf = dip_data.get("codice_fiscale")
    dip_cf: Optional[Dipendente] = Dipendente.objects.filter(codice_fiscale=cf).first() if cf else None
    if dip_cf:
        occupato_da_altro = _PC.objects.filter(dipendente=dip_cf).exclude(pk=profilo.pk).exists()
        if not occupato_da_altro:
            for field, val in dip_data.items():
                setattr(dip_cf, field, val)
            if azienda and not dip_cf.azienda_id:
                dip_cf.azienda = azienda
            if not dip_cf.utente_id:
                dip_cf.utente = user
            dip_cf.save()
            profilo.dipendente = dip_cf
            return dip_cf
        return None

    if not create_if_missing:
        return None

    if not azienda:
        return None

    dip = Dipendente.objects.create(azienda=azienda, utente=user, **dip_data)
    profilo.dipendente = dip
    return dip

