from django.test import SimpleTestCase

from accounts.formatting import normalize_anno_calendario


class NormalizeAnnoCalendarioTests(SimpleTestCase):
    def test_plain_year(self):
        self.assertEqual(normalize_anno_calendario('2026'), '2026')
        self.assertEqual(normalize_anno_calendario(2026), '2026')

    def test_italian_thousands_separator(self):
        self.assertEqual(normalize_anno_calendario('2.026'), '2026')

    def test_empty(self):
        self.assertEqual(normalize_anno_calendario(''), '')
        self.assertEqual(normalize_anno_calendario(None), '')
