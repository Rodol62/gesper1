"""
Motore applicativo «Calcolatore ferie e ROL» (identificativo ``MOTORE_CALCOLATORE_FERIE_ROL_ID``).

- Rateo mensile ferie (giorni) e ROL (ore), part-time orizzontale/verticale.
- Assenze non maturative (tabella ``CausaleAssenzaNonMaturativa``).

**Progressione mensile** (ferie in giorni; ROL in ore — stessa struttura)::

    dopo_maturazione = saldo_inizio_mese + maturato_nel_mese
    saldo_fine_mese = dopo_maturazione − goduto_nel_mese

Il saldo fine mese è il saldo inizio del mese successivo (iterato per tutti i mesi).

Il maturato del mese è moltiplicato per la quota di giorni del mese compresi tra
``data_inizio_rapporto`` e ``data_fine_rapporto`` (0 se il mese è fuori rapporto).

Parametri annui da normativa CCNL + rapporto vigente (allineato a proposta/contratto e motore paga).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

MOTORE_CALCOLATORE_FERIE_ROL_ID = 'calcolatore_ferie_rol_v1'


class TipoContrattoCalc(Enum):
    FULL_TIME = "full_time"
    PART_TIME_ORIZZONTALE = "part_time_orizzontale"
    PART_TIME_VERTICALE = "part_time_verticale"


@dataclass
class DatiContratto:
    tipo_contratto: Optional[TipoContrattoCalc]
    ferie_annue: float
    rol_annui: float  # ore annue (ROL / permessi)
    ore_settimanali_pt: Optional[float] = None
    giorni_lavorabili_ft: int = 26
    #: Se True, ``ferie_annue`` / ``rol_annui`` sono già prorati al part-time (come da ``parametri_normativi_contrattuali``): non moltiplicare di nuovo per ore/40 o simili.
    annuali_gia_prorati_ccnl: bool = False
    #: Ore giornaliere contrattuali (es. da CCNL) — solo per equivalenti HH:MM in UI.
    ore_giornaliere_riferimento: Optional[float] = None


@dataclass
class SituazioneMensile:
    giorni_lavorati: Optional[int] = None
    giorni_non_maturativi: int = 0
    ferie_godute_mese: float = 0.0
    rol_goduti_mese: float = 0.0
    ferie_progressive_anno: float = 0.0
    rol_progressivi_anno: float = 0.0
    #: Quota del mese (0–1) in cui il rapporto è attivo (da assunzione a scadenza contratto inclusi).
    coeff_periodo_rapporto: float = 1.0


@dataclass
class RisultatoMaturazione:
    ferie_mese: float
    rol_mese: float
    ferie_progressive: float
    rol_progressivi: float
    ferie_residue: float
    rol_residui: float


class CalcolatoreFerieRol:
    """
    Motore **Calcolatore ferie e ROL** (GESPER).

    ``ferie_progressive_anno`` / ``rol_progressivi_anno`` in input = saldo **inizio** mese
    (prima della maturazione del mese corrente). In output, ``ferie_residue`` / ``rol_residui``
    = saldo **fine** mese dopo maturazione e godimenti.
    """

    MOTORE_ID = MOTORE_CALCOLATORE_FERIE_ROL_ID

    @staticmethod
    def _valida_dati_contratto(dati: DatiContratto) -> None:
        if dati.ferie_annue <= 0:
            raise ValueError("ferie_annue deve essere > 0")

        if dati.rol_annui < 0:
            raise ValueError("rol_annui non può essere negativo")

        if dati.tipo_contratto is None:
            dati.tipo_contratto = TipoContrattoCalc.FULL_TIME

        if dati.tipo_contratto == TipoContrattoCalc.PART_TIME_ORIZZONTALE:
            if not getattr(dati, 'annuali_gia_prorati_ccnl', False):
                if not dati.ore_settimanali_pt or dati.ore_settimanali_pt <= 0:
                    raise ValueError("Per il PT orizzontale servono ore_settimanali_pt > 0")

        if dati.giorni_lavorabili_ft <= 0:
            raise ValueError("giorni_lavorabili_ft deve essere > 0")

    @staticmethod
    def _valida_situazione_mensile(dati: DatiContratto, mese: SituazioneMensile) -> None:
        if mese.giorni_non_maturativi < 0:
            raise ValueError("giorni_non_maturativi non può essere negativo")

        if dati.tipo_contratto == TipoContrattoCalc.PART_TIME_VERTICALE:
            if mese.giorni_lavorati is None or mese.giorni_lavorati < 0:
                raise ValueError("Per PT verticale servono giorni_lavorati >= 0")

    @staticmethod
    def _calcola_ferie_mese(dati: DatiContratto, mese: SituazioneMensile) -> float:
        base_mensile = dati.ferie_annue / 12
        gia_pr = getattr(dati, 'annuali_gia_prorati_ccnl', False)

        if gia_pr:
            if dati.tipo_contratto == TipoContrattoCalc.PART_TIME_VERTICALE:
                giorni_lavorati = mese.giorni_lavorati or 0
                return base_mensile * (giorni_lavorati / dati.giorni_lavorabili_ft)
            return base_mensile

        if dati.tipo_contratto == TipoContrattoCalc.FULL_TIME:
            ferie_mese = base_mensile

        elif dati.tipo_contratto == TipoContrattoCalc.PART_TIME_ORIZZONTALE:
            assert dati.ore_settimanali_pt is not None
            ferie_mese = base_mensile * (dati.ore_settimanali_pt / 40)

        elif dati.tipo_contratto == TipoContrattoCalc.PART_TIME_VERTICALE:
            giorni_lavorati = mese.giorni_lavorati or 0
            ferie_mese = base_mensile * (giorni_lavorati / dati.giorni_lavorabili_ft)

        else:
            ferie_mese = base_mensile

        return ferie_mese

    @staticmethod
    def _calcola_rol_mese(dati: DatiContratto, mese: SituazioneMensile) -> float:
        base_mensile = dati.rol_annui / 12
        gia_pr = getattr(dati, 'annuali_gia_prorati_ccnl', False)

        if gia_pr:
            if dati.tipo_contratto == TipoContrattoCalc.PART_TIME_VERTICALE:
                giorni_lavorati = mese.giorni_lavorati or 0
                return base_mensile * (giorni_lavorati / dati.giorni_lavorabili_ft)
            return base_mensile

        if dati.tipo_contratto == TipoContrattoCalc.FULL_TIME:
            rol_mese = base_mensile

        elif dati.tipo_contratto == TipoContrattoCalc.PART_TIME_ORIZZONTALE:
            assert dati.ore_settimanali_pt is not None
            rol_mese = base_mensile * (dati.ore_settimanali_pt / 40)

        elif dati.tipo_contratto == TipoContrattoCalc.PART_TIME_VERTICALE:
            giorni_lavorati = mese.giorni_lavorati or 0
            rol_mese = base_mensile * (giorni_lavorati / dati.giorni_lavorabili_ft)
        else:
            rol_mese = base_mensile

        return rol_mese

    @staticmethod
    def _applica_assenze_non_maturative(
        ferie_mese: float,
        rol_mese: float,
        dati: DatiContratto,
        mese: SituazioneMensile,
    ) -> tuple[float, float]:
        giorni_totali = dati.giorni_lavorabili_ft
        giorni_maturativi = max(0, giorni_totali - mese.giorni_non_maturativi)

        if giorni_maturativi < giorni_totali:
            coeff = giorni_maturativi / giorni_totali
            ferie_mese *= coeff
            rol_mese *= coeff

        return ferie_mese, rol_mese

    @classmethod
    def calcola(cls, dati: DatiContratto, mese: SituazioneMensile) -> RisultatoMaturazione:
        cls._valida_dati_contratto(dati)
        cls._valida_situazione_mensile(dati, mese)

        ferie_mese = cls._calcola_ferie_mese(dati, mese)
        rol_mese = cls._calcola_rol_mese(dati, mese)

        cper = float(getattr(mese, 'coeff_periodo_rapporto', 1.0) or 0.0)
        cper = max(0.0, min(1.0, cper))
        ferie_mese *= cper
        rol_mese *= cper

        ferie_mese, rol_mese = cls._applica_assenze_non_maturative(
            ferie_mese, rol_mese, dati, mese
        )

        ferie_progressive = mese.ferie_progressive_anno + ferie_mese
        rol_progressivi = mese.rol_progressivi_anno + rol_mese

        ferie_residue = ferie_progressive - mese.ferie_godute_mese
        rol_residui = rol_progressivi - mese.rol_goduti_mese

        return RisultatoMaturazione(
            ferie_mese=round(ferie_mese, 4),
            rol_mese=round(rol_mese, 4),
            ferie_progressive=round(ferie_progressive, 4),
            rol_progressivi=round(rol_progressivi, 4),
            ferie_residue=round(ferie_residue, 4),
            rol_residui=round(rol_residui, 4),
        )


# Retrocompatibilità con import precedenti
CalcolatoreMaturazione = CalcolatoreFerieRol
