"""Parsing importi proforma/parcella da testo estratto PDF."""
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from accounts.consulente_registro_studio import (
    parse_testo_proforma_parcella,
    ricalcola_totali_documenti_da_testo_estratto,
)
from accounts.models import MovimentoRegistroStudioConsulente
from anagrafiche.models import Azienda


class ParseTotaleParcellaProformaTests(SimpleTestCase):
    def test_totale_parcella_etichetta(self):
        txt = """
        PARCELLA PROFESSIONALE
        Totale parcella 1.234,56
        """
        r = parse_testo_proforma_parcella(txt, "fattura.pdf")
        self.assertEqual(r.totale_da_pagare, Decimal("1234.56"))
        self.assertEqual(r.tipo_documento, "parcella")

    def test_totale_proforma_etichetta(self):
        txt = "PROFORMA\nTotale proforma: € 500,00\n"
        r = parse_testo_proforma_parcella(txt, "x.pdf")
        self.assertEqual(r.totale_da_pagare, Decimal("500.00"))

    def test_numero_proforma_stessa_riga(self):
        txt = "PROFORMA N. 2025/P/042\nTotale proforma 100,00\n"
        r = parse_testo_proforma_parcella(txt, "pf.pdf")
        self.assertEqual(r.numero_documento, "2025/P/042")
        self.assertEqual(r.tipo_documento, "proforma")

    def test_numero_proforma_riga_dopo_titolo(self):
        txt = "PROFORMA FATTURA\nNumero: 2024/PROT-99\nTotale proforma 250,00\n"
        r = parse_testo_proforma_parcella(txt, "a.pdf")
        self.assertEqual(r.numero_documento, "2024/PROT-99")

    def test_numero_numero_proforma_etichetta_estesa(self):
        txt = "Numero proforma  PF-2026-0012\nPROFORMA\nTotale proforma 10,00\n"
        r = parse_testo_proforma_parcella(txt, "b.pdf")
        self.assertEqual(r.numero_documento, "PF-2026-0012")

    def test_umero_ocr_non_usato_prende_protocollo(self):
        """OCR «n. umero» non deve finire in numero_documento; si usa es. Prot."""
        txt = (
            "PROFORMA\nn. umero\nProt. 09/2022-PF\nTotale proforma 174,11\n"
        )
        r = parse_testo_proforma_parcella(txt, "pf.pdf")
        self.assertEqual(r.numero_documento, "09/2022-PF")

    def test_normalizza_n_umero_in_numero(self):
        txt = "PROFORMA\nn. umero: 55/REV-1\nTotale proforma 1,00\n"
        r = parse_testo_proforma_parcella(txt, "a.pdf")
        self.assertEqual(r.numero_documento, "55/REV-1")

    def test_totale_parcella_riga_successiva(self):
        txt = "TOTALE PARCELLA\n1.000,50\n"
        r = parse_testo_proforma_parcella(txt, "p.pdf")
        self.assertEqual(r.totale_da_pagare, Decimal("1000.50"))

    def test_nbsp_normalizzato(self):
        txt = f"TOTALE PARCELLA\u00a0:\u00a01.000,00"
        r = parse_testo_proforma_parcella(txt, "p.pdf")
        self.assertEqual(r.totale_da_pagare, Decimal("1000.00"))


class RileggiTotaliDaTestoEstrattoTests(TestCase):
    def test_aggiorna_totale_e_dare_da_testo_salvato(self):
        az = Azienda.objects.create(
            nome="Az Test Libro",
            partita_iva="IT99988877701",
            indirizzo="Via Test 1",
            email="libro@test.it",
        )
        txt = "PARCELLA\nTotale parcella 250,50\n"
        m = MovimentoRegistroStudioConsulente.objects.create(
            azienda=az,
            tipo_riga="documento",
            tipo_documento="sconosciuto",
            nome_file="parcella_x.pdf",
            testo_estratto=txt,
            totale_da_pagare=None,
            dare=Decimal("0"),
        )
        res = ricalcola_totali_documenti_da_testo_estratto(az.id)
        self.assertEqual(res["n_aggiornati"], 1)
        self.assertEqual(res["n_invariati"], 0)
        m.refresh_from_db()
        self.assertEqual(m.totale_da_pagare, Decimal("250.50"))
        self.assertEqual(m.dare, Decimal("250.50"))
        self.assertEqual(m.tipo_documento, "parcella")

    def test_salta_senza_testo(self):
        az = Azienda.objects.create(
            nome="Az Test 2",
            partita_iva="IT99988877702",
            indirizzo="Via Y 2",
            email="libro2@test.it",
        )
        MovimentoRegistroStudioConsulente.objects.create(
            azienda=az,
            tipo_riga="documento",
            nome_file="vuoto.pdf",
            testo_estratto="",
            totale_da_pagare=None,
            dare=Decimal("0"),
        )
        res = ricalcola_totali_documenti_da_testo_estratto(az.id)
        self.assertEqual(res["n_senza_testo"], 1)
        self.assertEqual(res["n_aggiornati"], 0)
