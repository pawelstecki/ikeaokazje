"""Testy jednostkowe dla ikea_okazje.py - bez polaczenia z IKEA/Telegramem.

Uruchomienie: python3 -m unittest tests/test_ikea_okazje.py
"""

import os
import sys
import tempfile
import unittest

# Izolowany, tymczasowy HOME - zeby import modulu nie czytal/nie tworzyl
# prawdziwych plikow stanu ani nie wymagal prawdziwego .env uzytkownika.
_TEST_HOME = tempfile.mkdtemp(prefix="ikea_okazje_test_home_")
os.environ["HOME"] = _TEST_HOME
os.makedirs(os.path.join(_TEST_HOME, ".config"), exist_ok=True)
with open(os.path.join(_TEST_HOME, ".config", "ikea-okazje.env"), "w", encoding="utf-8") as _f:
    # SMTP_MODE=exim nie wymaga zadnych sekretow (SMTP_USER/PASS).
    _f.write("SMTP_MODE=exim\nSTORE_IDS=294\n")
os.chmod(os.path.join(_TEST_HOME, ".config", "ikea-okazje.env"), 0o600)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ikea_okazje as ik  # noqa: E402


class TestKnownStores(unittest.TestCase):
    def test_294_maps_to_wroclaw_slug(self):
        self.assertEqual(ik.KNOWN_STORES["294"]["slug"], "wrocław")

    def test_188_maps_to_warszawa_janki_slug(self):
        self.assertEqual(ik.KNOWN_STORES["188"]["slug"], "warszawa+janki")


class TestReservationLink(unittest.TestCase):
    def test_plus_left_unencoded(self):
        link = ik.build_offer_reservation_link("1224", "123")
        self.assertIn("bielsko+biala", link)
        self.assertNotIn("%2B", link)

    def test_polish_char_is_encoded(self):
        link = ik.build_offer_reservation_link("294", "864721162")
        self.assertIn("wroc%C5%82aw", link)

    def test_exact_link_for_wroclaw_offer(self):
        link = ik.build_offer_reservation_link("294", "864721162")
        self.assertEqual(
            link,
            "https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroc%C5%82aw/864721162",
        )

    def test_missing_offer_number_returns_none(self):
        self.assertIsNone(ik.build_offer_reservation_link("294", None))

    def test_unknown_store_without_slug_returns_none(self):
        self.assertIsNone(ik.build_offer_reservation_link("999999", "123"))


class TestStoreCommands(unittest.TestCase):
    def setUp(self):
        ik.STORE_IDS = ["294"]
        ik.DYNAMIC_STATE["store_ids"] = ik.STORE_IDS
        self._orig_save = ik.save_dynamic_state
        ik.save_dynamic_state = lambda state: None  # bez zapisu na dysk w testach

    def tearDown(self):
        ik.save_dynamic_state = self._orig_save

    def test_add_known_store(self):
        reply = ik.handle_command("/dodajsklep", "1224")
        self.assertIn("1224", ik.STORE_IDS)
        self.assertIn("Bielsko", reply)

    def test_add_store_no_duplicates(self):
        ik.handle_command("/dodajsklep", "1224")
        ik.handle_command("/dodajsklep", "1224")
        self.assertEqual(ik.STORE_IDS.count("1224"), 1)

    def test_add_unknown_store_rejected(self):
        reply = ik.handle_command("/dodajsklep", "999999")
        self.assertNotIn("999999", ik.STORE_IDS)
        self.assertIn("Nieznany", reply)

    def test_remove_store(self):
        ik.handle_command("/dodajsklep", "1224")
        ik.handle_command("/usunsklep", "1224")
        self.assertNotIn("1224", ik.STORE_IDS)

    def test_remove_last_store_warns(self):
        reply = ik.handle_command("/usunsklep", "294")
        self.assertEqual(ik.STORE_IDS, [])
        self.assertIn("Uwaga", reply)

    def test_english_aliases(self):
        ik.handle_command("/addstore", "1224")
        self.assertIn("1224", ik.STORE_IDS)
        ik.handle_command("/removestore", "1224")
        self.assertNotIn("1224", ik.STORE_IDS)


if __name__ == "__main__":
    unittest.main()
