"""
Integrazione del modulo costo_lavoro con Django (rapporto_di_lavoro)
Questo file mostra come usare il calcolatore nei view di Django.
"""

from costo_lavoro import CostoLavoroAzienda, RuleEngine, DatiContrattuali
from costo_lavoro.logger import logger


class ServizioCalcoloCosto:
    """
    Wrapper di servizio per integrare costo_lavoro nelle view Django.
    """

    def __init__(self):
        self.engine = RuleEngine()

    def calcola_costo_proposta(self, proposta_assunzione):
        """
        Calcola il costo del lavoro per una proposta di assunzione.
        
        Args:
            proposta_assunzione: istanza del modello PropostaAssunzione di Django
        
        Returns:
            dict con il costo del lavoro dettagliato
        """
        try:
            livello_obj = getattr(proposta_assunzione, "livello_ccnl", None)
            if isinstance(livello_obj, str):
                livello = livello_obj
            else:
                livello = getattr(livello_obj, "nome", "standard") if livello_obj else "standard"

            ccnl_obj = getattr(proposta_assunzione, "ccnl", None)
            if isinstance(ccnl_obj, str):
                ccnl_codice = ccnl_obj
            else:
                ccnl_codice = getattr(ccnl_obj, "codice", "commercio") if ccnl_obj else "commercio"

            azienda = getattr(proposta_assunzione, "azienda", None)
            dimensione_azienda = getattr(azienda, "numero_dipendenti", 1) or 1

            # Estrai i dati dalla proposta
            dati_contrattuali = DatiContrattuali(
                retribuzione_lorda_mensile=proposta_assunzione.stipendio_lordo_mensile,
                giorni_lavorativi_mese=getattr(proposta_assunzione, "giorni_lavorativi_mese", 26),
                giorni_lavorati=getattr(proposta_assunzione, "giorni_lavorativi_mese", 26),  # Assumiamo giorni interi nel calcolo
                mensilita=getattr(proposta_assunzione, "numero_mensilita", 13),
                ore_settimanali=getattr(proposta_assunzione, "ore_settimanali", 40),
                livello=livello,
                ccnl=ccnl_codice,
            )

            # Carica regole dal motore
            regole_inps = self.engine.get(
                "inps",
                ccnl=dati_contrattuali.ccnl,
                dimensione=dimensione_azienda,
            )
            
            regole_inail = self.engine.get("inail", ccnl=dati_contrattuali.ccnl)
            regole_ratei = self.engine.get("ratei")
            
            # Verifica decontribuzioni (es. under 36)
            regole_decontrib = None
            if proposta_assunzione.eta and proposta_assunzione.eta < 36:
                regole_decontrib = self.engine.get("decontribuzioni")
            
            # Unisci regole
            regole_complete = {
                **regole_inps,
                **regole_inail,
                **regole_ratei
            }

            # Istanzia calcolatore
            calcolatore = CostoLavoroAzienda(
                contrattuali=dati_contrattuali,
                regole_inps=regole_complete,
                regole_decontrib=regole_decontrib or {},
                costi_eventuali=None
            )

            # Calcola
            risultato = calcolatore.calcola()
            
            logger.info(f"Costo lavoro calcolato per proposta {proposta_assunzione.id}")
            return risultato

        except Exception as e:
            logger.error(f"Errore nel calcolo del costo lavoro: {str(e)}")
            raise

    def calcola_per_contratto(self, contratto):
        """
        Calcola il costo per un contratto attivo.
        
        Args:
            contratto: istanza di Contratto Django
        
        Returns:
            dict con il costo del lavoro
        """
        # Adatta il contratto al formato atteso da calcola_costo_proposta.
        class _PropostaAdapter:
            pass

        adapter = _PropostaAdapter()
        adapter.id = getattr(contratto, "id", None)
        adapter.stipendio_lordo_mensile = getattr(contratto, "stipendio_lordo_mensile", 0)
        adapter.giorni_lavorativi_mese = getattr(contratto, "giorni_lavorativi_mese", 26)
        adapter.numero_mensilita = getattr(contratto, "numero_mensilita", 13)
        adapter.ore_settimanali = getattr(contratto, "ore_settimanali", 40)
        adapter.livello_ccnl = getattr(contratto, "livello_ccnl", None)
        adapter.ccnl = getattr(contratto, "ccnl", None)
        adapter.azienda = getattr(contratto, "azienda", None)
        adapter.eta = getattr(contratto, "eta", None)

        return self.calcola_costo_proposta(adapter)

    def aggiorna_regole(self, categoria, dati_json):
        """
        Aggiorna il file JSON delle regole.
        Utile per recepire nuove normative INPS, INAIL, ecc.
        
        Args:
            categoria: "inps", "inail", "decontribuzioni", ecc.
            dati_json: dict con le nuove regole
        """
        import json
        import os
        
        path = os.path.join(
            os.path.dirname(__file__),
            f"rules/{categoria}.json"
        )
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dati_json, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Regole {categoria} aggiornate")
