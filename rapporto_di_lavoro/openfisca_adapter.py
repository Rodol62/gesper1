from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from .models import DetrazioneLavoroDipendente, ParametroContributi
from .utils_calcoli import calcola_detrazioni, calcola_irpef_lorda

DEFAULT_INAIL_ALIQUOTA = Decimal('0.0120')
DEFAULT_INPS_ALIQUOTA_DIP = Decimal('0.0936')
DEFAULT_INPS_ALIQUOTA_AZIENDA = Decimal('0.3000')
DEFAULT_ADDIZIONALE_ALIQUOTA = Decimal('0.00')


class OpenFiscaAdapter:
    """Adapter per i calcoli statali basati su OpenFisca e fallback DB.

    Questa classe espone metodi per calcolare contributi INPS, IRPEF e premio INAIL.
    Se OpenFisca non è disponibile o fallisce, il calcolo ricade sui modelli
    parametrici del database.
    """

    def __init__(self, data_riferimento: Optional[date] = None):
        self.data_riferimento = data_riferimento or date.today()
        self.anno = self.data_riferimento.year
        self._openfisca = self._load_openfisca()

    def calcola_contributi_inps(
        self,
        imponibile: Decimal,
        categoria_lavoratore: str,
        data_riferimento: Optional[date] = None,
    ) -> Dict[str, Decimal]:
        """Calcola i contributi INPS per dipendente e azienda.

        Args:
            imponibile: imponibile mensile o annuo su cui applicare le aliquote.
            categoria_lavoratore: categoria CCNL / dimensione azienda da usare per il lookup.
            data_riferimento: data di validità per selezionare il parametro.

        Returns:
            dict con chiavi `inps_dipendente`, `inps_azienda`, `aliquota_dipendente`,
            `aliquota_azienda`.
        """
        if self._openfisca_available:
            result = self._calcola_contributi_inps_openfisca(
                imponibile, categoria_lavoratore, data_riferimento
            )
            if result is not None:
                return result

        return self._calcola_contributi_inps_fallback(imponibile, categoria_lavoratore)

    def calcola_irpef(
        self,
        reddito_annuo: Decimal,
        detrazioni_lavoro: Decimal,
        detrazioni_carichi: Decimal,
        addizionale_reg: Decimal,
        addizionale_com: Decimal,
        giorni: int,
    ) -> Dict[str, Decimal]:
        """Calcola IRPEF netta annuale e mensile.

        Args:
            reddito_annuo: reddito annuo lordo imponibile.
            detrazioni_lavoro: detrazione annua per lavoro dipendente.
            detrazioni_carichi: detrazione annua per carichi di famiglia.
            addizionale_reg: aliquota addizionale regionale (% o frazione).
            addizionale_com: aliquota addizionale comunale (% o frazione).
            giorni: numero di giorni lavorativi / periodo di riferimento (non usato nel fallback).

        Returns:
            dict con `irpef_lorda_annua`, `detrazioni_annua`,
            `addizionale_reg_annua`, `addizionale_com_annua`,
            `irpef_netta_annua`, `irpef_netta_mensile`.
        """
        if self._openfisca_available:
            result = self._calcola_irpef_openfisca(
                reddito_annuo,
                detrazioni_lavoro,
                detrazioni_carichi,
                addizionale_reg,
                addizionale_com,
                giorni,
            )
            if result is not None:
                return result

        return self._calcola_irpef_fallback(
            reddito_annuo,
            detrazioni_lavoro,
            detrazioni_carichi,
            addizionale_reg,
            addizionale_com,
        )

    def calcola_premio_inail(self, settore: str, imponibile: Decimal) -> Decimal:
        """Calcola il premio INAIL annuale.

        Args:
            settore: settore o categoria utile al lookup del parametro INAIL.
            imponibile: imponibile su cui applicare l'aliquota.

        Returns:
            premio INAIL annuale come Decimal.
        """
        if self._openfisca_available:
            result = self._calcola_premio_inail_openfisca(settore, imponibile)
            if result is not None:
                return result

        return self._calcola_premio_inail_fallback(settore, imponibile)

    # ------------------------
    # OpenFisca helper
    # ------------------------

    def _load_openfisca(self) -> Optional[Dict[str, object]]:
        try:
            openfisca_italy = importlib.import_module('openfisca_italy')
            openfisca_core = importlib.import_module('openfisca_core')
            return {
                'openfisca_italy': openfisca_italy,
                'openfisca_core': openfisca_core,
            }
        except Exception:
            return None

    @property
    def _openfisca_available(self) -> bool:
        return self._openfisca is not None

    def _simulate_openfisca(self, variable: str, values: dict) -> Optional[Decimal]:
        try:
            simulation_module = importlib.import_module('openfisca_core.simulations')
            Simulation = getattr(simulation_module, 'Simulation', None)
            country_class = getattr(
                self._openfisca['openfisca_italy'],
                'CountryTaxBenefitSystem',
                None,
            )
            if Simulation is None or country_class is None:
                return None

            country = country_class()
            sim = Simulation(country, period=str(self.anno))
            output = sim.calculate(variable, values)

            if output is None:
                return None

            return Decimal(str(output)).quantize(Decimal('0.01'))
        except Exception:
            return None

    def _calcola_contributi_inps_openfisca(
        self,
        imponibile: Decimal,
        categoria: str,
        data_riferimento: Optional[date],
    ) -> Optional[Dict[str, Decimal]]:
        """Tentativo di calcolo INPS con OpenFisca se disponibile."""
        return None

    def _calcola_irpef_openfisca(
        self,
        reddito_annuo: Decimal,
        detrazioni_lavoro: Decimal,
        detrazioni_carichi: Decimal,
        addizionale_reg: Decimal,
        addizionale_com: Decimal,
        giorni: int,
    ) -> Optional[Dict[str, Decimal]]:
        """Tentativo di calcolo IRPEF con OpenFisca se disponibile."""
        return None

    def _calcola_premio_inail_openfisca(
        self,
        settore: str,
        imponibile: Decimal,
    ) -> Optional[Decimal]:
        """Tentativo di calcolo INAIL con OpenFisca se disponibile."""
        return None

    # ------------------------
    # Fallback manuale
    # ------------------------

    def _calcola_contributi_inps_fallback(
        self,
        imponibile: Decimal,
        categoria_lavoratore: str,
    ) -> Dict[str, Decimal]:
        qs = ParametroContributi.objects.filter(
            tipo_contributo='inps',
            anno=self.anno,
            attivo=True,
        )
        param = qs.filter(categoria__iexact=categoria_lavoratore).order_by(
            '-data_validita_da'
        ).first()
        if not param:
            param = qs.filter(categoria__icontains=categoria_lavoratore).order_by(
                '-data_validita_da'
            ).first()
        if not param:
            param = qs.order_by('-data_validita_da').first()

        if not param:
            aliquota_dipendente = DEFAULT_INPS_ALIQUOTA_DIP
            aliquota_azienda = DEFAULT_INPS_ALIQUOTA_AZIENDA
        else:
            aliquota_dipendente = Decimal(str(param.aliquota_dipendente)) / Decimal('100')
            aliquota_azienda = Decimal(str(param.aliquota_azienda)) / Decimal('100')

        inps_dipendente = (Decimal(str(imponibile)) * aliquota_dipendente).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        inps_azienda = (Decimal(str(imponibile)) * aliquota_azienda).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        return {
            'inps_dipendente': inps_dipendente,
            'inps_azienda': inps_azienda,
            'aliquota_dipendente': aliquota_dipendente,
            'aliquota_azienda': aliquota_azienda,
        }

    def _calcola_irpef_fallback(
        self,
        reddito_annuo: Decimal,
        detrazioni_lavoro: Decimal,
        detrazioni_carichi: Decimal,
        addizionale_reg: Decimal,
        addizionale_com: Decimal,
    ) -> Dict[str, Decimal]:
        reddito = Decimal(str(reddito_annuo))
        irpef_lorda_annua = Decimal(
            str(calcola_irpef_lorda(float(reddito), anno=self.anno))
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        detrazioni_annua = Decimal(str(detrazioni_lavoro or 0)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        detrazioni_annua += Decimal(str(detrazioni_carichi or 0)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        addiz_reg_rate = self._normalize_rate(addizionale_reg)
        addiz_com_rate = self._normalize_rate(addizionale_com)

        addizionale_reg_annua = (reddito * addiz_reg_rate).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        addizionale_com_annua = (reddito * addiz_com_rate).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        irpef_netta_annua = (
            irpef_lorda_annua
            + addizionale_reg_annua
            + addizionale_com_annua
            - detrazioni_annua
        )
        if irpef_netta_annua < Decimal('0'):
            irpef_netta_annua = Decimal('0.00')

        irpef_netta_mensile = (irpef_netta_annua / Decimal('12')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        return {
            'irpef_lorda_annua': irpef_lorda_annua,
            'detrazioni_annua': detrazioni_annua,
            'addizionale_reg_annua': addizionale_reg_annua,
            'addizionale_com_annua': addizionale_com_annua,
            'irpef_netta_annua': irpef_netta_annua,
            'irpef_netta_mensile': irpef_netta_mensile,
        }

    def _calcola_premio_inail_fallback(self, settore: str, imponibile: Decimal) -> Decimal:
        qs = ParametroContributi.objects.filter(
            tipo_contributo='inail',
            anno=self.anno,
            attivo=True,
        )
        param = qs.filter(categoria__iexact=settore).order_by('-data_validita_da').first()
        if not param:
            param = qs.filter(categoria__icontains=settore).order_by(
                '-data_validita_da'
            ).first()
        if not param:
            param = qs.order_by('-data_validita_da').first()

        if not param:
            aliquota = DEFAULT_INAIL_ALIQUOTA
        else:
            aliquota = Decimal(str(param.aliquota_azienda)) / Decimal('100')

        return (Decimal(str(imponibile)) * aliquota).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def _normalize_rate(value: Decimal) -> Decimal:
        if value is None:
            return DEFAULT_ADDIZIONALE_ALIQUOTA
        value_dec = Decimal(str(value))
        if value_dec > Decimal('1'):
            return (value_dec / Decimal('100')).quantize(Decimal('0.0001'))
        return value_dec.quantize(Decimal('0.0001'))
