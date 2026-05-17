from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from .models import (
    AddizionaleComunale,
    AddizionaleRegionale,
    DetrazioneLavoroDipendente,
    InailParametro,
    ParametroContributi,
)

DEFAULT_INAIL_ALIQUOTA = Decimal('0.0120')
DEFAULT_INPS_ALIQUOTA_DIP = Decimal('0.0936')
DEFAULT_INPS_ALIQUOTA_AZIENDA = Decimal('0.3000')
DEFAULT_ADDIZIONALE_ALIQUOTA = Decimal('0.0000')
DEFAULT_ADDIZIONALE_REGIONALE = Decimal('0.0090')
DEFAULT_ADDIZIONALE_COMUNALE = Decimal('0.0010')
DEFAULT_INPS_MASSIMALE_ANNUO = Decimal('122295')
DEFAULT_INPS_SOGLIA_ECCEDENZA = Decimal('56224')
DEFAULT_INPS_ECCEDENZA_RATE = Decimal('0.01')
DEFAULT_INPS_MESE_THRESHOLD = Decimal('12000')
DEFAULT_INAIL_GIORNI_LAVORATIVI = Decimal('260')
DEFAULT_TRATTAMENTO_INTEGRATIVO = Decimal('1200.00')
DEFAULT_DETRAZIONI_LAVORO_15000_REDDITO = Decimal('15000')
DEFAULT_DETRAZIONI_LAVORO_15000 = Decimal('1880.00')
DEFAULT_DETRAZIONI_LAVORO_28000_BASE = Decimal('1910.00')
DEFAULT_DETRAZIONI_LAVORO_28000_SLOPE = Decimal('1190.00')
DEFAULT_DETRAZIONI_LAVORO_50000_BASE = Decimal('1910.00')
DEFAULT_BT_28000 = Decimal('28000')
DEFAULT_BT_50000 = Decimal('50000')


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
        param = self._carica_parametro_contributi('inps', categoria_lavoratore)
        if not param:
            aliquota_dipendente = DEFAULT_INPS_ALIQUOTA_DIP
            aliquota_azienda = DEFAULT_INPS_ALIQUOTA_AZIENDA
        else:
            aliquota_dipendente = Decimal(str(param.aliquota_dipendente)) / Decimal('100')
            aliquota_azienda = Decimal(str(param.aliquota_azienda)) / Decimal('100')

        imponibile_annuo = self._normalizza_imponibile_annuo(imponibile)
        imponibile_annuo = min(imponibile_annuo, DEFAULT_INPS_MASSIMALE_ANNUO)
        extra_annuo = max(
            Decimal('0.00'), self._normalizza_imponibile_annuo(imponibile) - DEFAULT_INPS_SOGLIA_ECCEDENZA
        )
        extra_rate = self._get_inps_extra_rate(param)
        extra_contributo = (extra_annuo * extra_rate).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        inps_dipendente = (
            imponibile_annuo * aliquota_dipendente + extra_contributo
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        inps_azienda = (
            imponibile_annuo * aliquota_azienda + extra_contributo
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if param is not None:
            minimale_giornaliero = getattr(param, 'minimale_giornaliero', None)
            if minimale_giornaliero is not None:
                giornaliero = (imponibile_annuo / DEFAULT_INAIL_GIORNI_LAVORATIVI).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                if giornaliero < Decimal(str(minimale_giornaliero)):
                    base_contributivo = (
                        Decimal(str(minimale_giornaliero)) * DEFAULT_INAIL_GIORNI_LAVORATIVI
                    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    inps_dipendente = (base_contributivo * aliquota_dipendente).quantize(
                        Decimal('0.01'), rounding=ROUND_HALF_UP
                    )
                    inps_azienda = (base_contributivo * aliquota_azienda).quantize(
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
        irpef_lorda_annua = self._calcola_irpef_lorda_2026(reddito)

        if detrazioni_lavoro is None or detrazioni_lavoro == Decimal('0'):
            detrazioni_lavoro = self._calcola_detrazioni_lavoro_2026(reddito)

        detrazioni_annua = (
            Decimal(str(detrazioni_lavoro or Decimal('0.00')))
            + Decimal(str(detrazioni_carichi or Decimal('0.00')))
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        addiz_reg_rate = self._normalize_rate(addizionale_reg)
        if addiz_reg_rate == Decimal('0.00'):
            addiz_reg_rate = self._get_addizionale_regionale_rate()

        addiz_com_rate = self._normalize_rate(addizionale_com)
        if addiz_com_rate == Decimal('0.00'):
            addiz_com_rate = self._get_addizionale_comunale_rate()

        addizionale_reg_annua = (reddito * addiz_reg_rate).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        addizionale_com_annua = (reddito * addiz_com_rate).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        bonus_integrativo = (
            DEFAULT_TRATTAMENTO_INTEGRATIVO
            if reddito < DEFAULT_BT_28000
            else Decimal('0.00')
        )

        irpef_netta_annua = (
            irpef_lorda_annua
            + addizionale_reg_annua
            + addizionale_com_annua
            - detrazioni_annua
            - bonus_integrativo
        )
        if irpef_netta_annua < Decimal('0.00'):
            irpef_netta_annua = Decimal('0.00')

        irpef_netta_mensile = (irpef_netta_annua / Decimal('12')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        return {
            'irpef_lorda_annua': irpef_lorda_annua,
            'detrazioni_annua': detrazioni_annua,
            'addizionale_reg_annua': addizionale_reg_annua,
            'addizionale_com_annua': addizionale_com_annua,
            'bonus_integrativo_annua': bonus_integrativo,
            'irpef_netta_annua': irpef_netta_annua,
            'irpef_netta_mensile': irpef_netta_mensile,
        }

    def _calcola_premio_inail_fallback(self, settore: str, imponibile: Decimal) -> Decimal:
        param = self._carica_parametro_contributi('inail', settore)
        if not param:
            aliquota = DEFAULT_INAIL_ALIQUOTA
        else:
            aliquota = Decimal(str(param.aliquota_azienda)) / Decimal('100')

        imponibile_annuo = Decimal(str(imponibile))
        inail_param = self._carica_inail_parametro()
        if inail_param is not None:
            if inail_param.retribuzione_annua_massima is not None:
                imponibile_annuo = min(
                    imponibile_annuo,
                    Decimal(str(inail_param.retribuzione_annua_massima)),
                )
            if inail_param.retribuzione_convenzionale_giornaliera is not None:
                imponibile_annuo = (
                    Decimal(str(inail_param.retribuzione_convenzionale_giornaliera))
                    * DEFAULT_INAIL_GIORNI_LAVORATIVI
                ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            elif inail_param.retribuzione_giornaliera_minima is not None:
                giornaliero = (imponibile_annuo / DEFAULT_INAIL_GIORNI_LAVORATIVI).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                if giornaliero < Decimal(str(inail_param.retribuzione_giornaliera_minima)):
                    imponibile_annuo = (
                        Decimal(str(inail_param.retribuzione_giornaliera_minima))
                        * DEFAULT_INAIL_GIORNI_LAVORATIVI
                    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        return (imponibile_annuo * aliquota).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    def _carica_parametro_contributi(self, tipo_contributo: str, categoria: str):
        qs = ParametroContributi.objects.filter(
            tipo_contributo=tipo_contributo,
            anno=self.anno,
            attivo=True,
        )
        param = qs.filter(categoria__iexact=categoria).order_by(
            '-data_validita_da'
        ).first()
        if not param:
            param = qs.filter(categoria__icontains=categoria).order_by(
                '-data_validita_da'
            ).first()
        if not param:
            param = qs.order_by('-data_validita_da').first()
        return param

    def _normalizza_imponibile_annuo(self, imponibile: Decimal) -> Decimal:
        value = Decimal(str(imponibile))
        if value <= DEFAULT_INPS_MESE_THRESHOLD:
            return (value * Decimal('12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _get_inps_extra_rate(self, param):
        if param is None:
            return DEFAULT_INPS_ECCEDENZA_RATE
        extra = getattr(param, 'aliquota_aggiuntiva', None)
        if extra is not None:
            extra_dec = Decimal(str(extra))
            return extra_dec / Decimal('100') if extra_dec > Decimal('1') else extra_dec
        return DEFAULT_INPS_ECCEDENZA_RATE

    def _calcola_irpef_lorda_2026(self, reddito: Decimal) -> Decimal:
        aliquote = [
            (DEFAULT_BT_28000, Decimal('0.23')),
            (DEFAULT_BT_50000, Decimal('0.33')),
            (None, Decimal('0.43')),
        ]
        imponibile = Decimal(str(reddito))
        risultato = Decimal('0.00')
        limite_precedente = Decimal('0.00')
        for limite, aliquota in aliquote:
            if limite is None or imponibile <= limite:
                base = imponibile - limite_precedente
                if base > 0:
                    risultato += base * aliquota
                break
            risultato += (limite - limite_precedente) * aliquota
            limite_precedente = limite
        return risultato.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _calcola_detrazioni_lavoro_2026(self, reddito: Decimal) -> Decimal:
        detrazione = self._carica_detrazione_lavoro_da_db(reddito)
        if detrazione is not None:
            return detrazione

        if reddito <= DEFAULT_DETRAZIONI_LAVORO_15000_REDDITO:
            return DEFAULT_DETRAZIONI_LAVORO_15000
        if reddito <= DEFAULT_BT_28000:
            return (
                DEFAULT_DETRAZIONI_LAVORO_28000_BASE
                + DEFAULT_DETRAZIONI_LAVORO_28000_SLOPE
                * (DEFAULT_BT_28000 - reddito)
                / (DEFAULT_BT_28000 - DEFAULT_DETRAZIONI_LAVORO_15000_REDDITO)
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if reddito <= DEFAULT_BT_50000:
            return (
                DEFAULT_DETRAZIONI_LAVORO_50000_BASE
                * (DEFAULT_BT_50000 - reddito)
                / (DEFAULT_BT_50000 - DEFAULT_BT_28000)
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return Decimal('0.00')

    def _carica_detrazione_lavoro_da_db(self, reddito: Decimal) -> Optional[Decimal]:
        try:
            fasce = DetrazioneLavoroDipendente.objects.filter(
                anno=self.anno,
                attivo=True,
            ).order_by('reddito_da')
            for f in fasce:
                da = Decimal(str(f.reddito_da or 0))
                a = Decimal(str(f.reddito_a)) if f.reddito_a is not None else None
                if reddito < da:
                    continue
                if a is not None and reddito > a:
                    continue
                base = Decimal(str(f.importo_base_annuo or 0))
                coeff = Decimal(str(f.coefficiente_variabile_annuo)) if f.coefficiente_variabile_annuo is not None else None
                rif = Decimal(str(f.reddito_riferimento)) if f.reddito_riferimento is not None else None
                div = Decimal(str(f.divisore_fascia)) if f.divisore_fascia is not None else None
                if coeff is not None and rif is not None and div not in (None, Decimal('0')):
                    val = base + coeff * (rif - reddito) / div
                else:
                    val = base
                return max(val, Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except Exception:
            return None
        return None

    def _get_addizionale_regionale_rate(self) -> Decimal:
        param = AddizionaleRegionale.objects.filter(
            anno=self.anno,
            attivo=True,
        ).order_by('-data_validita_da').first()
        if param is not None and param.aliquota is not None:
            return Decimal(str(param.aliquota)) / Decimal('100')
        return DEFAULT_ADDIZIONALE_REGIONALE

    def _get_addizionale_comunale_rate(self) -> Decimal:
        param = AddizionaleComunale.objects.filter(
            anno=self.anno,
            attivo=True,
        ).order_by('-data_validita_da').first()
        if param is not None and param.aliquota is not None:
            return Decimal(str(param.aliquota)) / Decimal('100')
        return DEFAULT_ADDIZIONALE_COMUNALE

    def _carica_inail_parametro(self) -> Optional[InailParametro]:
        return (
            InailParametro.objects.filter(
                anno=self.anno,
                attivo=True,
            )
            .order_by('-data_validita_da')
            .first()
        )

    @staticmethod
    def _normalize_rate(value: Decimal) -> Decimal:
        if value is None:
            return DEFAULT_ADDIZIONALE_ALIQUOTA
        value_dec = Decimal(str(value))
        if value_dec > Decimal('1'):
            return (value_dec / Decimal('100')).quantize(Decimal('0.0001'))
        return value_dec.quantize(Decimal('0.0001'))
