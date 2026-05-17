from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from .motore_ccnl import MotoreCCNL
from .openfisca_adapter import OpenFiscaAdapter
from .models import EventoContrattuale, RapportoDiLavoro
from presenze.models import RiepilogoMensilePresenze

logger = logging.getLogger(__name__)


class MotoreRetributivo:
    """Orchestratore dei calcoli retributivi e fiscali per un rapporto di lavoro."""

    def __init__(
        self,
        rapporto: RapportoDiLavoro,
        data_riferimento: Optional[date] = None,
    ):
        self.rapporto = rapporto
        self.data_riferimento = data_riferimento or date.today()
        self.motore_ccnl = MotoreCCNL(rapporto, data_riferimento=self.data_riferimento)
        self.openfisca_adapter = OpenFiscaAdapter(data_riferimento=self.data_riferimento)

    def calcola_busta_completa(self, riepilogo: RiepilogoMensilePresenze) -> dict:
        """Calcola la busta paga completa combinando CCNL e regole statali/fiscali."""
        lordo = self.motore_ccnl.calcola_retribuzione_lorda(riepilogo)
        lordo_totale = cast_decimal(lordo.get('lordo_totale', Decimal('0.00')))

        categoria = self._get_categoria_lavoratore()
        contributi = self.openfisca_adapter.calcola_contributi_inps(
            lordo_totale,
            categoria,
            data_riferimento=self.data_riferimento,
        )

        imponibile_irpef_mensile = (lordo_totale - contributi.get('inps_dipendente', Decimal('0.00'))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        reddito_annuo_stimato = (imponibile_irpef_mensile * Decimal('12')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        giorni = self._estrai_giorni_da_riepilogo(riepilogo)
        irpef = self.openfisca_adapter.calcola_irpef(
            reddito_annuo_stimato,
            Decimal('0.00'),
            Decimal('0.00'),
            Decimal('0.00'),
            Decimal('0.00'),
            giorni,
        )

        netto_in_busta = (lordo_totale - contributi.get('inps_dipendente', Decimal('0.00')) - irpef.get('irpef_netta_annua', Decimal('0.00')) / Decimal('12')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        premio_inail = self.openfisca_adapter.calcola_premio_inail(
            self._get_settore_inail(),
            lordo_totale,
        )

        ratei = self._calcola_ratei_per_costo(lordo_totale)
        cost_ratei = sum(ratei.values(), Decimal('0.00')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        costo_azienda = (
            lordo_totale
            + contributi.get('inps_azienda', Decimal('0.00'))
            + premio_inail
            + cost_ratei
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        logger.debug(
            "Busta completa calcolata per rapporto %s: lordo=%s contributi=%s irpef=%s netto=%s costo_azienda=%s",
            self.rapporto,
            lordo_totale,
            contributi,
            irpef,
            netto_in_busta,
            costo_azienda,
        )

        return {
            'lordo': lordo,
            'contributi': contributi,
            'irpef': irpef,
            'netto_in_busta': netto_in_busta,
            'costo_azienda': costo_azienda,
        }

    def calcola_evento(self, evento: EventoContrattuale) -> dict:
        """Delega la gestione dell'evento contrattuale al motore CCNL."""
        logger.debug("Calcolo evento contrattuale %s per rapporto %s", evento, self.rapporto)
        risultato = self.motore_ccnl.gestisci_evento(evento)
        return {**{'evento': evento.tipo}, **risultato}

    def _get_categoria_lavoratore(self) -> str:
        if self.rapporto.livello_ccnl:
            return str(self.rapporto.livello_ccnl)
        if self.rapporto.tipo_contratto:
            return str(self.rapporto.tipo_contratto.nome)
        return ''

    def _get_settore_inail(self) -> str:
        if self.rapporto.tipo_contratto:
            return str(self.rapporto.tipo_contratto.nome)
        if self.rapporto.posizione:
            return str(self.rapporto.posizione)
        return ''

    def _estrai_giorni_da_riepilogo(self, riepilogo: RiepilogoMensilePresenze) -> int:
        giorni = getattr(riepilogo, 'giorni_lavorati', None)
        if giorni is None:
            giorni = getattr(riepilogo, 'giorni', None)
        if giorni is None:
            giorni = getattr(riepilogo, 'giorni_riepilogo', None)
        try:
            return int(giorni) if giorni is not None else 0
        except (TypeError, ValueError):
            return 0

    def _calcola_ratei_per_costo(self, imponibile: Decimal) -> Dict[str, Decimal]:
        ratei = {
            'tredicesima': self.motore_ccnl.calcola_ratei(imponibile, 'tredicesima'),
            'quattordicesima': self.motore_ccnl.calcola_ratei(imponibile, 'quattordicesima'),
            'ferie': self.motore_ccnl.calcola_ratei(imponibile, 'ferie'),
            'tfr': self.motore_ccnl.calcola_ratei(imponibile, 'tfr'),
        }
        return {k: cast_decimal(v) for k, v in ratei.items()}


def cast_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal('0.00')
