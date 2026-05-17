from costo_lavoro.logger import logger
from costo_lavoro.models.contributivi import DatiContributivi
from costo_lavoro.models.risultato import RisultatoCostoLavoro


class CostoLavoroAzienda:

    def __init__(self, contrattuali, regole_inps, regole_decontrib=None, costi_eventuali=None):
        self.contr = contrattuali
        self.contrib = DatiContributivi(**regole_inps)
        self.decontrib = regole_decontrib or {}
        self.extra = costi_eventuali

    # 1. Retribuzione proporzionata ai giorni lavorati
    def _calcola_retribuzione_proporzionata(self):
        base = self.contr.retribuzione_lorda_mensile
        ratio = self.contr.giorni_lavorati / self.contr.giorni_lavorativi_mese
        return base * ratio

    # 2. Contributi INPS a carico azienda (base)
    def _calcola_inps(self, retribuzione):
        aliquota = self.contrib.get("aliquota_inps_azienda", 0)
        inps = retribuzione * aliquota

        # contributi aggiuntivi
        inps += retribuzione * self.contrib.get("contributo_fis", 0)
        inps += retribuzione * self.contrib.get("contributo_naspi", 0)
        inps += retribuzione * self.contrib.get("contributo_td", 0)

        # applicazione decontribuzioni (se presenti)
        if self.decontrib:
            tipo = self.decontrib.get("tipo")
            valore = self.decontrib.get("valore", 0)
            massimale = self.decontrib.get("massimale_mensile", 0)

            if tipo == "percentuale":
                inps -= inps * valore
            elif tipo == "esonero_totale":
                inps = 0
            elif tipo == "esonero_parziale":
                inps -= inps * valore
            elif tipo == "massimale":
                inps = max(0, inps - massimale)

        return inps

    # 3. INAIL
    def _calcola_inail(self, retribuzione):
        aliquota_inail = self.contrib.get("aliquota_inail", 0)
        return retribuzione * aliquota_inail

    # 4. Ratei (ferie, permessi, 13a, 14a)
    def _calcola_ratei(self, retribuzione):
        rateo_ferie = retribuzione * self.contrib.get("rateo_ferie", 1/12)
        rateo_permessi = retribuzione * self.contrib.get("rateo_permessi", 1/18)
        rateo_tredicesima = retribuzione * self.contrib.get("rateo_tredicesima", 1/12)
        rateo_quattordicesima = retribuzione * self.contrib.get("rateo_quattordicesima", 0)

        totale = (
            rateo_ferie +
            rateo_permessi +
            rateo_tredicesima +
            rateo_quattordicesima
        )

        return {
            "rateo_ferie": rateo_ferie,
            "rateo_permessi": rateo_permessi,
            "rateo_tredicesima": rateo_tredicesima,
            "rateo_quattordicesima": rateo_quattordicesima,
            "ratei_totali": totale
        }

    # 5. TFR
    def _calcola_tfr(self, retribuzione):
        aliquota_tfr = self.contrib.get("aliquota_tfr", 1/13.5)
        return retribuzione * aliquota_tfr

    # 6. Costi extra
    def _calcola_extra(self):
        if not self.extra:
            return 0
        return self.extra.totale

    def calcola(self):
        logger.info("Calcolo costo del lavoro avviato")

        retribuzione = self._calcola_retribuzione_proporzionata()
        inps = self._calcola_inps(retribuzione)
        inail = self._calcola_inail(retribuzione)
        ratei = self._calcola_ratei(retribuzione)
        tfr = self._calcola_tfr(retribuzione)
        extra = self._calcola_extra()

        costo_totale = (
            retribuzione +
            inps +
            inail +
            ratei["ratei_totali"] +
            tfr +
            extra
        )

        risultato = RisultatoCostoLavoro(
            retribuzione_proporzionata=retribuzione,
            contributi_inps=inps,
            premio_inail=inail,
            **ratei,
            tfr=tfr,
            costi_extra=extra,
            costo_totale=costo_totale
        )

        logger.info("Calcolo costo del lavoro completato")
        return risultato.to_dict()
