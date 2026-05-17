"""
Test automatici per scenari Fase C (monti / riepilogo) — vedi docs/PRESENZE_MOTORE_FASE_C.md.
"""
from datetime import date, time
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from anagrafiche.models import Azienda, Dipendente
from presenze.models import MovimentoMonte, Presenza
from presenze.monte_ledger import applica_movimenti_da_riepilogo, chiave_idempotenza_riepilogo
from presenze.utils import aggrega_presenze_per_motore, ore_std_giornaliere_contratto

User = get_user_model()


def _uniq_piva():
    return f'TS{uuid.uuid4().hex[:10]}'.upper()[:20]


def _uniq_cf():
    return f'TSTTST{uuid.uuid4().hex[:10]}'.upper()[:16]


class MonteFaseCAggregazioneTestCase(TestCase):
    """C2, C3, C4: output di aggrega_presenze_per_motore (ore_std = 8 h da azienda 40h/sett)."""

    def setUp(self):
        self.azienda = Azienda.objects.create(
            nome='Azienda Monte Test',
            partita_iva=_uniq_piva(),
            indirizzo='Via Test 1',
            email='test@monte.local',
            ore_settimanali_standard=Decimal('40'),
            ore_giornaliere_standard=Decimal('8'),
        )
        self.user = User.objects.create_user(
            username=f'u_{uuid.uuid4().hex[:12]}',
            password='x',
            azienda=self.azienda,
        )
        self.dip = Dipendente.objects.create(
            azienda=self.azienda,
            nome='Test',
            cognome='Monte',
            codice_fiscale=_uniq_cf(),
            ruolo='Impiegato',
            stato='attivo',
            data_assunzione=date(2020, 1, 1),
        )
        self.anno = 2026
        self.mese = 3
        self.ore_std = ore_std_giornaliere_contratto(self.dip, self.azienda, self.anno, self.mese)
        self.assertEqual(self.ore_std, Decimal('8'))

    def test_c2_due_giornate_ferie_intere_senza_orari(self):
        # Mar 2026: 2 e 3 marzo = lun/mar (non festivi)
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 3, 2),
            causale='F',
            registrata_da=self.user,
        )
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 3, 3),
            causale='F',
            registrata_da=self.user,
        )
        rie = aggrega_presenze_per_motore(self.dip, self.azienda, self.anno, self.mese, utente=self.user)
        self.assertEqual(rie.giorni_ferie_godute, Decimal('2.00'))
        self.assertEqual(rie.ore_permessi_goduti, Decimal('0.00'))

    def test_c3_mezza_giornata_ferie_quattro_ore(self):
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 3, 4),
            causale='F',
            ora_entrata=time(9, 0),
            ora_uscita=time(13, 0),
            registrata_da=self.user,
        )
        rie = aggrega_presenze_per_motore(self.dip, self.azienda, self.anno, self.mese, utente=self.user)
        self.assertEqual(rie.giorni_ferie_godute, Decimal('0.50'))

    def test_c4_permessi_rol_somma_ore(self):
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 3, 10),
            causale='PE',
            ora_entrata=time(9, 0),
            ora_uscita=time(11, 0),
            registrata_da=self.user,
        )
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 3, 11),
            causale='PE',
            ora_entrata=time(9, 0),
            ora_uscita=time(12, 0),
            registrata_da=self.user,
        )
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 3, 12),
            causale='PE',
            ora_entrata=time(9, 0),
            ora_uscita=time(10, 0),
            registrata_da=self.user,
        )
        rie = aggrega_presenze_per_motore(self.dip, self.azienda, self.anno, self.mese, utente=self.user)
        self.assertEqual(rie.ore_permessi_goduti, Decimal('6.00'))
        self.assertEqual(rie.giorni_ferie_godute, Decimal('0.00'))


class MonteFaseCMovimentiTestCase(TestCase):
    """Movimenti monte dopo applica_movimenti_da_riepilogo (C2 / C5-like)."""

    def setUp(self):
        self.azienda = Azienda.objects.create(
            nome='Azienda Monte Mov Test',
            partita_iva=_uniq_piva(),
            indirizzo='Via Test 2',
            email='test2@monte.local',
            ore_settimanali_standard=Decimal('40'),
        )
        self.user = User.objects.create_user(
            username=f'u2_{uuid.uuid4().hex[:12]}',
            password='x',
            azienda=self.azienda,
        )
        self.dip = Dipendente.objects.create(
            azienda=self.azienda,
            nome='Test',
            cognome='Mov',
            codice_fiscale=_uniq_cf(),
            ruolo='Impiegato',
            stato='attivo',
            data_assunzione=date(2020, 1, 1),
        )
        self.anno = 2026
        self.mese = 4

    def test_movimenti_ferie_e_rol_da_riepilogo_approvato(self):
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 4, 6),
            causale='F',
            registrata_da=self.user,
        )
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 4, 7),
            causale='PE',
            ora_entrata=time(9, 0),
            ora_uscita=time(11, 0),
            registrata_da=self.user,
        )
        rie = aggrega_presenze_per_motore(self.dip, self.azienda, self.anno, self.mese, utente=self.user)
        self.assertEqual(rie.giorni_ferie_godute, Decimal('1.00'))
        self.assertEqual(rie.ore_permessi_goduti, Decimal('2.00'))

        rie.stato = 'approvata'
        rie.save(update_fields=['stato'])

        applica_movimenti_da_riepilogo(rie, utente=self.user, solo_se_approvato=True)

        kf = chiave_idempotenza_riepilogo(self.anno, self.mese, 'ferie')
        kr = chiave_idempotenza_riepilogo(self.anno, self.mese, 'rol')

        mf = MovimentoMonte.objects.get(idempotency_key=kf)
        mr = MovimentoMonte.objects.get(idempotency_key=kr)

        self.assertEqual(mf.quantita, Decimal('-1.00'))
        self.assertEqual(mf.unita, 'GG')
        self.assertEqual(mr.quantita, Decimal('-2.00'))
        self.assertEqual(mr.unita, 'ORE')

    def test_idempotenza_seconda_applicazione_non_duplica_righe(self):
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 4, 10),
            causale='F',
            registrata_da=self.user,
        )
        rie = aggrega_presenze_per_motore(self.dip, self.azienda, self.anno, self.mese, utente=self.user)
        rie.stato = 'approvata'
        rie.save(update_fields=['stato'])

        applica_movimenti_da_riepilogo(rie, utente=self.user)
        applica_movimenti_da_riepilogo(rie, utente=self.user)

        kf = chiave_idempotenza_riepilogo(self.anno, self.mese, 'ferie')
        self.assertEqual(MovimentoMonte.objects.filter(idempotency_key=kf).count(), 1)

    def test_aggiornamento_quantita_movimento_se_riepilogo_cambia_senza_aggrega(self):
        """Simula correzione valori sul riepilogo (es. dopo rettifica) con stesso stato approvato."""
        Presenza.objects.create(
            dipendente=self.dip,
            azienda=self.azienda,
            data=date(2026, 4, 15),
            causale='F',
            registrata_da=self.user,
        )
        rie = aggrega_presenze_per_motore(self.dip, self.azienda, self.anno, self.mese, utente=self.user)
        rie.stato = 'approvata'
        rie.save(update_fields=['stato'])
        applica_movimenti_da_riepilogo(rie, utente=self.user)

        kf = chiave_idempotenza_riepilogo(self.anno, self.mese, 'ferie')
        self.assertEqual(MovimentoMonte.objects.get(idempotency_key=kf).quantita, Decimal('-1.00'))

        rie.giorni_ferie_godute = Decimal('2.00')
        rie.save(update_fields=['giorni_ferie_godute'])
        applica_movimenti_da_riepilogo(rie, utente=self.user)

        self.assertEqual(MovimentoMonte.objects.get(idempotency_key=kf).quantita, Decimal('-2.00'))
