"""Test partitario paghe dipendenti (libro dare/avere)."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import MovimentoImportPaghe, PagamentoPartitarioPaghe
from accounts.partitario_paghe_dipendenti import (
    registra_pagamento_partitario,
    righe_libro_partitario,
    scheda_dipendente_partitario,
)
from anagrafiche.models import Azienda, Dipendente


class PartitarioPagheDipendentiTest(TestCase):
    def setUp(self) -> None:
        self.azienda = Azienda.objects.create(nome='Test SPA', partita_iva='12345678901')
        self.dip = Dipendente.objects.create(
            azienda=self.azienda,
            cognome='Rossi',
            nome='Mario',
            codice_fiscale='RSSMRA80A01H501U',
            stato='attivo',
        )
        MovimentoImportPaghe.objects.create(
            azienda=self.azienda,
            dipendente=self.dip,
            tipo='BUSTA',
            anno=2026,
            mese=4,
            importo_netto=Decimal('1500.00'),
            periodo_label='04/2026',
        )

    def test_saldo_dopo_busta_e_pagamento(self) -> None:
        registra_pagamento_partitario(
            azienda=self.azienda,
            dipendente=self.dip,
            data_pagamento=date(2026, 4, 30),
            importo=Decimal('1500.00'),
            descrizione='Bonifico aprile',
            riferimento_bancario='CRO123',
            movimento_busta_id=None,
            utente=None,
        )
        righe = righe_libro_partitario(self.dip)
        self.assertEqual(len(righe), 2)
        self.assertEqual(righe[0].avere, Decimal('1500.00'))
        self.assertEqual(righe[1].dare, Decimal('1500.00'))
        self.assertEqual(righe[1].saldo, Decimal('0.00'))

    def test_scheda_anno_raggruppato(self) -> None:
        scheda = scheda_dipendente_partitario(self.dip, anno=2026)
        self.assertEqual(len(scheda.blocchi_anno), 1)
        self.assertEqual(scheda.blocchi_anno[0].anno, 2026)
        self.assertEqual(scheda.saldo_finale, Decimal('1500.00'))

    def test_pagamento_su_modello(self) -> None:
        pag = PagamentoPartitarioPaghe.objects.create(
            azienda=self.azienda,
            dipendente=self.dip,
            data_pagamento=date(2026, 5, 1),
            importo=Decimal('100.00'),
            descrizione='Acconto',
        )
        self.assertIn('ROSSI', str(pag).upper())
