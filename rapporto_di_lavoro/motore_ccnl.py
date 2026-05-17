from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from .models import (
    CCNL,
    EventoContrattuale,
    ParametroContributi,
    ParametroMaggiorazione,
    ParametroOrario,
    ParametroRatei,
    ParametroScattiAnnuali,
    RapportoDiLavoro,
    Transizione,
)

logger = logging.getLogger(__name__)

DEFAULT_ORE_MENSILI = Decimal('173.33')
DEFAULT_TFR_ALIQUOTA = Decimal('0.0691')
DEFAULT_FERIE_RATEO = Decimal('0.1154')
DEFAULT_13A_RATEO = Decimal('0.083333')
DEFAULT_14A_RATEO = Decimal('0')


class MotoreCCNL:
    """Motore dei calcoli contrattuali legati al CCNL."""

    def __init__(self, rapporto: RapportoDiLavoro, data_riferimento: Optional[date] = None):
        self.rapporto = rapporto
        self.data_riferimento = data_riferimento or date.today()
        self.ccnl_obj = self._carica_ccnl()
        self.parametri_orario = self._carica_parametri_orario()
        self.parametri_maggiorazione = self._carica_parametri_maggiorazione()
        self.parametri_scatti = self._carica_parametri_scatti()
        self.parametri_ratei = self._carica_parametri_ratei()
        self.parametri_contributi = self._carica_parametri_contributi()
        self.transizioni = self._carica_transizioni()

    # ------------------------
    # Metodi pubblici
    # ------------------------

    def calcola_retribuzione_lorda(self, riepilogo) -> Dict[str, Decimal]:
        """Calcola le componenti lorde del cedolino mensile basato sul CCNL."""
        paga_base = self._calcola_paga_base()
        scatti = self._calcola_scatti_anzianita()
        paga_oraria = self._calcola_paga_oraria(paga_base)

        straordinari = self._calcola_straordinari(riepilogo, paga_oraria)
        maggiorazioni = self._calcola_maggiorazioni(riepilogo, paga_oraria)

        lordo_totale = (paga_base + scatti + straordinari + maggiorazioni).quantize(Decimal('0.01'))

        logger.debug(
            "Calcolo retribuzione lorda: paga_base=%s scatti=%s straordinari=%s maggiorazioni=%s lordo_totale=%s",
            paga_base, scatti, straordinari, maggiorazioni, lordo_totale,
        )

        return {
            'paga_base': paga_base,
            'scatti': scatti,
            'straordinari': straordinari,
            'maggiorazioni': maggiorazioni,
            'lordo_totale': lordo_totale,
        }

    def calcola_ratei(self, imponibile: Decimal, tipo: str) -> Decimal:
        """Calcola il rateo mensile in base al tipo di competenza."""
        tipo = tipo.lower().strip()
        coefficiente = None

        if self.ccnl_obj and self.parametri_ratei:
            pr = self.parametri_ratei.filter(tipo_rateo=tipo).order_by('-data_validita_da').first()
            if pr:
                coefficiente = pr.coefficiente
                logger.debug("Rateo trovato in ParametroRatei: %s -> %s", tipo, coefficiente)

        if coefficiente is None and tipo == 'tfr':
            coefficiente = self._fallback_tfr_coefficiente()
            logger.debug("Fallback TFR coefficiente: %s", coefficiente)

        if coefficiente is None:
            if tipo == 'tredicesima':
                amount = imponibile * DEFAULT_13A_RATEO
            elif tipo == 'quattordicesima':
                amount = imponibile * DEFAULT_14A_RATEO
            elif tipo == 'ferie' or tipo == 'permessi':
                amount = imponibile * DEFAULT_FERIE_RATEO
            elif tipo == 'tfr':
                amount = imponibile * DEFAULT_TFR_ALIQUOTA
            else:
                amount = Decimal('0')
            logger.debug("Rateo tipo %s senza coefficiente DB: %s", tipo, amount)
            return amount.quantize(Decimal('0.01'))

        coeff_dec = Decimal(str(coefficiente))
        if tipo in ('tredicesima', 'quattordicesima'):
            rateo = (imponibile * (coeff_dec / Decimal('12'))).quantize(Decimal('0.01'))
        elif tipo in ('ferie', 'permessi'):
            rateo = (imponibile * (coeff_dec / Decimal('100'))).quantize(Decimal('0.01'))
        elif tipo == 'tfr':
            rateo = (imponibile * (coeff_dec / Decimal('100'))).quantize(Decimal('0.01'))
        else:
            rateo = Decimal('0')

        logger.debug("Rateo calcolato %s: %s", tipo, rateo)
        return rateo

    def gestisci_evento(self, evento: EventoContrattuale) -> Dict[str, Decimal]:
        """Gestisce gli eventi contrattuali e calcola le voci di fine rapporto o promozione."""
        logger.debug("Gestione evento contrattuale %s per rapporto %s", evento, self.rapporto)
        if evento.tipo in ('licenziamento', 'dimissioni'):
            return self._gestisci_fine_rapporto(evento)
        if evento.tipo == 'promozione':
            return self._gestisci_promozione(evento)
        if evento.tipo == 'aspettativa':
            return self._gestisci_aspettativa(evento)

        logger.warning("Tipo evento contrattuale non gestito: %s", evento.tipo)
        return {'tipo': evento.tipo, 'valore': Decimal('0.00')}

    # ------------------------
    # Helper privati
    # ------------------------

    def _carica_ccnl(self) -> Optional[CCNL]:
        if not self.rapporto.tipo_contratto or not self.rapporto.tipo_contratto.ccnl:
            return None
        sigla = self.rapporto.tipo_contratto.ccnl.strip()
        ccnl = CCNL.objects.filter(sigla__iexact=sigla, attivo=True).first()
        if ccnl:
            return ccnl
        return CCNL.objects.filter(nome__icontains=sigla, attivo=True).first()

    def _carica_parametri_orario(self):
        if not self.ccnl_obj:
            return ParametroOrario.objects.none()
        tipo_contratto = self.rapporto.tipo_contratto.tipo if self.rapporto.tipo_contratto else ''
        return ParametroOrario.objects.filter(
            ccnl=self.ccnl_obj,
            tipo_categoria='mensile',
            tipo_contratto__iexact=tipo_contratto,
            attivo=True,
            data_validita_da__lte=self.data_riferimento,
        ).order_by('-data_validita_da')

    def _carica_parametri_maggiorazione(self):
        if not self.ccnl_obj:
            return ParametroMaggiorazione.objects.none()
        return ParametroMaggiorazione.objects.filter(
            ccnl=self.ccnl_obj,
            attivo=True,
            data_validita_da__lte=self.data_riferimento,
        ).order_by('-data_validita_da')

    def _carica_parametri_scatti(self):
        if not self.ccnl_obj:
            return ParametroScattiAnnuali.objects.none()
        return ParametroScattiAnnuali.objects.filter(
            ccnl=self.ccnl_obj,
            attivo=True,
            data_validita_da__lte=self.data_riferimento,
        ).order_by('-anni_anzianita', '-data_validita_da')

    def _carica_parametri_ratei(self):
        if not self.ccnl_obj:
            return ParametroRatei.objects.none()
        return ParametroRatei.objects.filter(
            ccnl=self.ccnl_obj,
            attivo=True,
            data_validita_da__lte=self.data_riferimento,
        ).order_by('-data_validita_da')

    def _carica_parametri_contributi(self):
        if not self.ccnl_obj:
            return ParametroContributi.objects.none()
        return ParametroContributi.objects.filter(
            ccnl=self.ccnl_obj,
            attivo=True,
            data_validita_da__lte=self.data_riferimento,
        ).order_by('-data_validita_da')

    def _carica_transizioni(self):
        return Transizione.objects.filter(
            rapporto=self.rapporto,
            data_inizio__gte=self.rapporto.data_inizio_rapporto,
        ).order_by('data_inizio')

    def _calcola_paga_base(self) -> Decimal:
        if self.ccnl_obj:
            minimo = self.ccnl_obj.get_minimo_tabellare(self.rapporto.livello_ccnl, self.data_riferimento)
            if minimo is not None:
                return Decimal(str(minimo)).quantize(Decimal('0.01'))

        logger.warning(
            "Nessun minimo tabellare trovato per rapporto %s, uso stipendio lordo mensile di contratto",
            self.rapporto,
        )
        return Decimal(str(self.rapporto.stipendio_lordo_mensile)).quantize(Decimal('0.01'))

    def _calcola_scatti_anzianita(self) -> Decimal:
        if not self.ccnl_obj:
            return Decimal('0.00')
        data_inizio = self.rapporto.data_inizio_rapporto
        anni = self._anni_servizio(data_inizio, self.data_riferimento)
        totale = Decimal('0.00')
        for scatto in self.parametri_scatti:
            if scatto.livello.lower() == self.rapporto.livello_ccnl.lower() and scatto.anni_anzianita <= anni:
                totale += Decimal(str(scatto.importo_scatto))
        return totale.quantize(Decimal('0.01'))

    def _calcola_paga_oraria(self, paga_base: Decimal) -> Decimal:
        ore_mensili = DEFAULT_ORE_MENSILI
        if self.parametri_orario.exists():
            ore_mensili = Decimal(str(self.parametri_orario.first().valore_massimo))
        if ore_mensili <= 0:
            ore_mensili = DEFAULT_ORE_MENSILI
        return (paga_base / ore_mensili).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

    def _maggiorazione(self, tipo: str, default_percentuale: Decimal) -> Decimal:
        if not self.ccnl_obj:
            return default_percentuale
        entry = self.parametri_maggiorazione.filter(tipo_maggiorazione=tipo).first()
        if entry:
            return (Decimal(str(entry.percentuale)) / Decimal('100')).quantize(Decimal('0.0001'))
        return default_percentuale

    def _calcola_straordinari(self, riepilogo, paga_oraria: Decimal) -> Decimal:
        magg_diurno = self._maggiorazione('straordinario_feriale', Decimal('0.15'))
        magg_notturno = self._maggiorazione('straordinario_notturno', Decimal('0.30'))
        magg_festivo = self._maggiorazione('straordinario_festivo', Decimal('0.30'))
        magg_nott_fest = self._maggiorazione('straordinario_notturno_festivo', magg_notturno + magg_festivo)
        magg_domenicale = self._maggiorazione('straordinario_domenicale', magg_festivo)

        imp_sd = (Decimal(str(riepilogo.ore_straord_diurno)) * paga_oraria * (Decimal('1') + magg_diurno)).quantize(Decimal('0.01'))
        imp_sn = (Decimal(str(riepilogo.ore_straord_notturno)) * paga_oraria * (Decimal('1') + magg_notturno)).quantize(Decimal('0.01'))
        imp_sf = (Decimal(str(riepilogo.ore_straord_festivo)) * paga_oraria * (Decimal('1') + magg_festivo)).quantize(Decimal('0.01'))
        imp_snf = (Decimal(str(riepilogo.ore_straord_nott_fest)) * paga_oraria * (Decimal('1') + magg_nott_fest)).quantize(Decimal('0.01'))
        imp_sdom = (Decimal(str(riepilogo.ore_straord_domenica)) * paga_oraria * (Decimal('1') + magg_domenicale)).quantize(Decimal('0.01'))

        totale = (imp_sd + imp_sn + imp_sf + imp_snf + imp_sdom).quantize(Decimal('0.01'))
        logger.debug("Totale straordinari: %s", totale)
        return totale

    def _calcola_maggiorazioni(self, riepilogo, paga_oraria: Decimal) -> Decimal:
        magg_dom = self._maggiorazione('lavoro_domenicale', Decimal('0.15'))
        magg_fest = self._maggiorazione('lavoro_festivo', Decimal('0.20'))

        imp_dom = (Decimal(str(riepilogo.ore_domenicali)) * paga_oraria * magg_dom).quantize(Decimal('0.01'))
        imp_fest = (Decimal(str(riepilogo.ore_festivi)) * paga_oraria * magg_fest).quantize(Decimal('0.01'))

        totale = (imp_dom + imp_fest).quantize(Decimal('0.01'))
        logger.debug("Totale maggiorazioni: %s", totale)
        return totale

    def _gestisci_fine_rapporto(self, evento: EventoContrattuale) -> Dict[str, Decimal]:
        paga_base = self._calcola_paga_base()
        pag_giornaliera = (paga_base / Decimal('26')).quantize(Decimal('0.01'))
        tfr = self.calcola_ratei(paga_base, 'tfr')
        ferie = (Decimal(str(evento.giorni_ferie_non_godute)) * pag_giornaliera).quantize(Decimal('0.01'))
        preavviso = (Decimal(str(evento.giorni_preavviso or 0)) * pag_giornaliera).quantize(Decimal('0.01'))
        totale = (tfr + ferie + preavviso).quantize(Decimal('0.01'))
        logger.debug("Competenze fine rapporto: tfr=%s ferie=%s preavviso=%s totale=%s", tfr, ferie, preavviso, totale)
        return {
            'tipo_evento': evento.tipo,
            'tfr': tfr,
            'ferie_non_godute': ferie,
            'preavviso': preavviso,
            'totale': totale,
        }

    def _gestisci_promozione(self, evento: EventoContrattuale) -> Dict[str, Decimal]:
        nuovo_livello = evento.nuovo_livello or self.rapporto.livello_ccnl
        if not evento.nuovo_livello and self.transizioni.exists():
            transizione = self.transizioni.filter(nuovo_livello__isnull=False).first()
            if transizione:
                nuovo_livello = transizione.nuovo_livello

        nuovo_minimo = None
        if self.ccnl_obj:
            nuovo_minimo = self.ccnl_obj.get_minimo_tabellare(nuovo_livello, evento.data_evento)
        nuovo_minimo = Decimal(str(nuovo_minimo or evento.nuovo_stipendio_lordo_mensile or self.rapporto.stipendio_lordo_mensile)).quantize(Decimal('0.01'))
        logger.debug("Promozione: nuovo livello %s nuovo minimo %s", nuovo_livello, nuovo_minimo)
        return {
            'tipo_evento': evento.tipo,
            'nuovo_livello': nuovo_livello,
            'nuova_retribuzione_lorda': nuovo_minimo,
        }

    def _gestisci_aspettativa(self, evento: EventoContrattuale) -> Dict[str, Decimal]:
        logger.debug("Aspettativa: riduzione proporzionale evento=%s", evento)
        return {
            'tipo_evento': evento.tipo,
            'riduzione': Decimal('0.00'),
        }

    def _anni_servizio(self, inizio: date, riferimento: date) -> int:
        if not inizio:
            return 0
        anni = riferimento.year - inizio.year
        if (riferimento.month, riferimento.day) < (inizio.month, inizio.day):
            anni -= 1
        return max(0, anni)

    def _fallback_tfr_coefficiente(self) -> Decimal:
        if self.rapporto.aliquota_tfr is not None:
            return Decimal(str(self.rapporto.aliquota_tfr))
        return DEFAULT_TFR_ALIQUOTA
