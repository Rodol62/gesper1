from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from anagrafiche.models import Azienda, Dipendente
from presenze.models import RiepilogoMensilePresenze
from rapporto_di_lavoro.motore_unico import MotoreRetributivo
from rapporto_di_lavoro.models import (
    CCNL,
    EventoContrattuale,
    LivelloCCNL,
    ParametroScattiAnnuali,
    RapportoDiLavoro,
    TipoContratto,
)
from rapporto_di_lavoro.openfisca_adapter import OpenFiscaAdapter

pytestmark = pytest.mark.django_db


def quantize(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'))


def create_base_entities() -> tuple[Azienda, Dipendente, CCNL, LivelloCCNL, TipoContratto, RapportoDiLavoro]:
    azienda = Azienda.objects.create(
        nome='Test SRL',
        partita_iva='12345678901',
        indirizzo='Via Roma 1',
        email='info@test.it',
        telefono='0911234567',
    )
    dipendente = Dipendente.objects.create(
        azienda=azienda,
        nome='Mario',
        cognome='Rossi',
        codice_fiscale='RSSMRA80A01H501U',
    )
    ccnl = CCNL.objects.create(
        nome='FIPE Turismo',
        sigla='FIPE',
        anno_inizio_validita=2024,
        orario_standard_settimanale=Decimal('40.00'),
        mensilita=12,
        giorni_ferie_base=20,
        giorni_rol_base=3,
    )
    livello = LivelloCCNL.objects.create(
        ccnl=ccnl,
        livello='I',
        minimo_tabellare=Decimal('1500.00'),
        data_inizio=date(2024, 1, 1),
        attivo=True,
    )
    tipo = TipoContratto.objects.create(
        nome='Indeterminato full-time',
        ccnl='FIPE',
        tipo='ind_full',
        coefficiente_ore=Decimal('1.00'),
    )
    rapporto = RapportoDiLavoro.objects.create(
        azienda=azienda,
        dipendente=dipendente,
        numero_contratto='C-001',
        tipo_contratto=tipo,
        data_inizio_rapporto=date(2025, 5, 1),
        posizione='Cameriere',
        livello_ccnl='I',
        qualifica='Operaio',
        stipendio_lordo_mensile=Decimal('1500.00'),
        paga_base_mensile=Decimal('1500.00'),
        contingenza_mensile=Decimal('0.00'),
        edr_mensile=Decimal('0.00'),
        superminimo_mensile=Decimal('0.00'),
    )
    return azienda, dipendente, ccnl, livello, tipo, rapporto


def create_riepilogo(dipendente: Dipendente, azienda: Azienda, **kwargs) -> RiepilogoMensilePresenze:
    defaults = {
        'anno': 2026,
        'mese': 5,
        'stato': 'bozza',
    }
    defaults.update(kwargs)
    return RiepilogoMensilePresenze.objects.create(
        dipendente=dipendente,
        azienda=azienda,
        **defaults,
    )


def test_busta_paga_solo_paga_base():
    _, dip, _, _, _, rapporto = create_base_entities()
    riepilogo = create_riepilogo(dip, rapporto.azienda)
    motore = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17))

    cedolino = motore.calcola_busta_completa(riepilogo)

    assert cedolino['lordo']['lordo_totale'] == Decimal('1500.00')
    assert cedolino['netto_in_busta'] == cedolino['netto_in_busta'].quantize(Decimal('0.01'))
    assert cedolino['costo_azienda'] == cedolino['costo_azienda'].quantize(Decimal('0.01'))


def test_busta_paga_con_scatti_anzianita():
    _, dip, ccnl, _, tipo, rapporto = create_base_entities()
    ParametroScattiAnnuali.objects.create(
        ccnl=ccnl,
        livello='I',
        anni_anzianita=1,
        importo_scatto=Decimal('50.00'),
        anno=2026,
        attivo=True,
        data_validita_da=date(2024, 1, 1),
    )
    riepilogo = create_riepilogo(dip, rapporto.azienda)
    motore = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17))

    cedolino = motore.calcola_busta_completa(riepilogo)

    assert cedolino['lordo']['scatti'] == Decimal('50.00')
    assert cedolino['lordo']['lordo_totale'] == Decimal('1550.00')


def test_busta_paga_con_straordinari_diurno():
    _, dip, _, _, _, rapporto = create_base_entities()
    riepilogo = create_riepilogo(
        dip,
        rapporto.azienda,
        ore_straord_diurno=Decimal('10.00'),
    )
    motore = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17))
    result = motore.calcola_busta_completa(riepilogo)

    expected_extra = quantize(
        Decimal('10.00')
        * (Decimal('1500.00') / Decimal('173.33'))
        * Decimal('1.15')
    )
    assert result['lordo']['straordinari'] == expected_extra
    assert result['lordo']['lordo_totale'] == quantize(Decimal('1500.00') + expected_extra)


def test_busta_paga_con_maggiorazioni_notturne():
    _, dip, _, _, _, rapporto = create_base_entities()
    riepilogo = create_riepilogo(
        dip,
        rapporto.azienda,
        ore_straord_notturno=Decimal('20.00'),
    )
    motore = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17))
    result = motore.calcola_busta_completa(riepilogo)

    expected_extra = quantize(
        Decimal('20.00')
        * (Decimal('1500.00') / Decimal('173.33'))
        * Decimal('1.30')
    )
    assert result['lordo']['straordinari'] == expected_extra
    assert result['lordo']['lordo_totale'] == quantize(Decimal('1500.00') + expected_extra)


def test_busta_paga_scenario_completo():
    _, dip, _, _, _, rapporto = create_base_entities()
    riepilogo = create_riepilogo(
        dip,
        rapporto.azienda,
        ore_straord_diurno=Decimal('5.00'),
        ore_straord_notturno=Decimal('3.00'),
        ore_domenicali=Decimal('2.00'),
    )
    motore = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17))
    result = motore.calcola_busta_completa(riepilogo)

    base = Decimal('1500.00')
    oraria = (base / Decimal('173.33')).quantize(Decimal('0.0001'))
    expected_straord = quantize(
        Decimal('5.00') * oraria * Decimal('1.15')
        + Decimal('3.00') * oraria * Decimal('1.30')
    )
    expected_magg = quantize(Decimal('2.00') * oraria * Decimal('0.15'))

    assert result['lordo']['straordinari'] == expected_straord
    assert result['lordo']['maggiorazioni'] == expected_magg
    assert result['lordo']['lordo_totale'] == quantize(base + expected_straord + expected_magg)


def test_openfisca_adapter_inps_massimale_2026():
    adapter = OpenFiscaAdapter(data_riferimento=date(2026, 5, 17))
    result = adapter.calcola_contributi_inps(Decimal('150000.00'), 'I')

    expected_base = Decimal('122295.00')
    expected_extra = quantize((Decimal('150000.00') - Decimal('56224.00')) * Decimal('0.01'))
    assert result['inps_dipendente'] == quantize(expected_base * Decimal('0.0936') + expected_extra)
    assert result['inps_azienda'] == quantize(expected_base * Decimal('0.3000') + expected_extra)


@pytest.mark.parametrize(
    'reddito_annuo, expected_netto_annuo',
    [
        (Decimal('10000.00'), Decimal('0.00')),
        (Decimal('20000.00'), Decimal('757.69')),
        (Decimal('60000.00'), Decimal('18000.00')),
    ],
)
def test_openfisca_adapter_irpef_netta_2026(reddito_annuo, expected_netto_annuo):
    adapter = OpenFiscaAdapter(data_riferimento=date(2026, 5, 17))
    result = adapter.calcola_irpef(
        reddito_annuo,
        Decimal('0.00'),
        Decimal('0.00'),
        Decimal('0.00'),
        Decimal('0.00'),
        0,
    )
    assert result['irpef_netta_annua'] == expected_netto_annuo


def test_costo_azienda_completo():
    _, dip, _, _, _, rapporto = create_base_entities()
    riepilogo = create_riepilogo(dip, rapporto.azienda)
    result = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17)).calcola_busta_completa(riepilogo)

    assert result['costo_azienda'] >= result['lordo']['lordo_totale']
    assert result['costo_azienda'] >= result['lordo']['lordo_totale'] + result['contributi']['inps_azienda']
    assert isinstance(result['costo_azienda'], Decimal)


def test_calcola_evento_fine_rapporto_licenziamento():
    _, dip, _, _, _, rapporto = create_base_entities()
    evento = EventoContrattuale.objects.create(
        rapporto=rapporto,
        tipo='licenziamento',
        data_evento=date(2026, 5, 17),
        giorni_ferie_non_godute=Decimal('5.00'),
        giorni_preavviso=3,
    )
    result = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17)).calcola_evento(evento)

    assert result['tipo_evento'] == 'licenziamento'
    assert result['tfr'] == quantize(Decimal('1500.00') * Decimal('0.065'))
    assert result['ferie_non_godute'] == quantize(Decimal('5.00') * quantize(Decimal('1500.00') / Decimal('26')))
    assert result['preavviso'] == quantize(Decimal('3') * quantize(Decimal('1500.00') / Decimal('26')))


def test_calcola_evento_promozione_nuova_retribuzione():
    _, dip, _, _, _, rapporto = create_base_entities()
    evento = EventoContrattuale.objects.create(
        rapporto=rapporto,
        tipo='promozione',
        data_evento=date(2026, 5, 17),
        nuovo_stipendio_lordo_mensile=Decimal('1700.00'),
    )
    result = MotoreRetributivo(rapporto, data_riferimento=date(2026, 5, 17)).calcola_evento(evento)

    assert result['tipo_evento'] == 'promozione'
    assert result['nuova_retribuzione_lorda'] == Decimal('1700.00')
