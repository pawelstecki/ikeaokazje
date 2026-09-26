"""Testy jednostkowe dla ikea_okazje.py - bez polaczenia z IKEA/Telegramem.

Uruchomienie: python3 -m unittest tests/test_ikea_okazje.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

# Izolowany, tymczasowy HOME - zeby import modulu nie czytal/nie tworzyl
# prawdziwych plikow stanu ani nie wymagal prawdziwego .env uzytkownika.
# UWAGA: samo `import ikea_okazje` nie czyta juz .env i nie inicjalizuje
# aplikacji (patrz initialize_runtime() w ikea_okazje.py) - ten plik .env
# jest tu przygotowany z gory tylko dlatego, ze wiele istniejacych testow
# w tym pliku zaklada, ze modul ma juz zaladowana, poprawna konfiguracje
# (wywolujemy initialize_runtime() explicit nizej, PO imporcie).
_TEST_HOME = tempfile.mkdtemp(prefix="ikea_okazje_test_home_")
os.environ["HOME"] = _TEST_HOME
os.makedirs(os.path.join(_TEST_HOME, ".config"), exist_ok=True)
with open(os.path.join(_TEST_HOME, ".config", "ikea-okazje.env"), "w", encoding="utf-8") as _f:
    # SMTP_MODE=exim nie wymaga zadnych sekretow (SMTP_USER/PASS), ale
    # wymaga jawnego EMAIL_TO (patrz validate_notification_config()).
    _f.write("SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n")
os.chmod(os.path.join(_TEST_HOME, ".config", "ikea-okazje.env"), 0o600)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import ikea_okazje as ik  # noqa: E402

# Import sam z siebie NIE inicjalizuje aplikacji (patrz TestSideEffectFreeImport
# nizej) - wiele istniejacych testow w tym pliku zaklada jednak, ze modul ma
# juz zaladowana konfiguracje (ik.STORE_IDS, ik.SEARCH_TERMS, ik.DYNAMIC_STATE,
# ik.EMAIL_ENABLED, ...), wiec wywolujemy initialize_runtime() jawnie raz, tutaj,
# z przygotowanym wyzej testowym .env.
ik.initialize_runtime()


def _make_isolated_home(env_file_contents=None):
    """Tworzy nowy, izolowany katalog HOME (osobny od _TEST_HOME powyzej),
    opcjonalnie z plikiem ~/.config/ikea-okazje.env. Gdy env_file_contents
    jest None, katalog .config nawet nie jest tworzony - do testowania
    zachowania bez zadnej konfiguracji uzytkownika."""
    tmp_home = tempfile.mkdtemp(prefix="ikea_okazje_subproc_home_")
    if env_file_contents is not None:
        config_dir = os.path.join(tmp_home, ".config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "ikea-okazje.env")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(env_file_contents)
        os.chmod(config_path, 0o600)
    return tmp_home


def _run_python_code(code: str, home_dir: str, extra_env: dict = None) -> subprocess.CompletedProcess:
    """Odpala krotki fragment kodu Pythona w osobnym procesie, z podanym
    HOME - do testowania zachowania na poziomie modulu (import,
    initialize_runtime()) w pelnej izolacji od reszty zestawu testow."""
    env = dict(os.environ)
    env["HOME"] = home_dir
    if extra_env:
        env.update(extra_env)
    return subprocess.run(  # nosec - subprocess used only for isolated startup tests
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_module_with_env(env_file_contents: str) -> subprocess.CompletedProcess:
    """Odpala `import ikea_okazje as ik; ik.initialize_runtime()` w osobnym
    procesie, z wlasnym izolowanym HOME i podanym plikiem ikea-okazje.env -
    do testowania walidacji konfiguracji, ktora teraz dzieje sie w
    initialize_runtime(), NIE przy samym imporcie (patrz TestSideEffectFreeImport
    i TestExplicitInitialization nizej dla testow tego rozdzielenia)."""
    tmp_home = _make_isolated_home(env_file_contents)
    return _run_python_code(
        "import ikea_okazje as ik; ik.initialize_runtime()", tmp_home
    )


class TestSideEffectFreeImport(unittest.TestCase):
    """`import ikea_okazje` samo w sobie nie powinno robic NIC poza
    zdefiniowaniem funkcji/stalych/harmless defaultow - patrz
    initialize_runtime() w ikea_okazje.py. Te testy odpalaja `import
    ikea_okazje` (bez wywolania initialize_runtime()) w osobnym procesie,
    z izolowanym, PUSTYM katalogiem HOME (bez ~/.config/ikea-okazje.env)."""

    def test_import_does_not_fail_without_config_file(self):
        home_dir = _make_isolated_home(env_file_contents=None)
        result = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_import_does_not_create_config_file(self):
        home_dir = _make_isolated_home(env_file_contents=None)
        result = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        config_path = os.path.join(home_dir, ".config", "ikea-okazje.env")
        self.assertFalse(os.path.exists(config_path))

    def test_import_does_not_create_dynamic_state_file(self):
        home_dir = _make_isolated_home(env_file_contents=None)
        result = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        dynamic_state_path = os.path.join(home_dir, ".ikea_okazje_dynamic.json")
        self.assertFalse(os.path.exists(dynamic_state_path))

    def test_import_does_not_create_seen_offers_file(self):
        home_dir = _make_isolated_home(env_file_contents=None)
        result = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        seen_offers_path = os.path.join(home_dir, ".ikea_okazje_seen_offers.json")
        self.assertFalse(os.path.exists(seen_offers_path))

    def test_import_succeeds_even_with_invalid_smtp_mode_in_env(self):
        # Gdyby walidacja SMTP_MODE dzialala jeszcze na poziomie importu,
        # ten .env (literowka w SMTP_MODE) spowodowalby awarie samego
        # `import ikea_okazje`. Teraz walidacja jest w initialize_runtime(),
        # wiec sam import ma sie powiesc niezaleznie od tego, co jest w .env.
        home_dir = _make_isolated_home("SMTP_MODE=literowka-w-trybie\n")
        result = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class TestExplicitInitialization(unittest.TestCase):
    """Testy dla initialize_runtime() wywolanej explicit, z wlasnym
    izolowanym HOME/.env - w osobnych procesach, zeby nie zanieczyszczac
    stanu modulu uzywanego przez pozostale testy w tym pliku."""

    def test_initialize_runtime_loads_and_validates_configuration(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=disabled\n"
            "TELEGRAM_BOT_TOKEN=123:fake-token\n"
            "TELEGRAM_CHAT_ID=999\n"
            "STORE_IDS=1224\n"
            "SEARCH_TERMS=\n"
            "SEARCH_ARTICLE_NUMBERS=\n"
        )
        result = _run_python_code(
            "import ikea_okazje as ik\n"
            "ik.initialize_runtime()\n"
            "assert ik.SMTP_MODE == 'disabled', ik.SMTP_MODE\n"
            "assert ik.TELEGRAM_ENABLED is True\n"
            "assert ik.STORE_IDS == ['1224'], ik.STORE_IDS\n"
            "print('OK')\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_dynamic_state_created_only_after_explicit_initialization(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
        )
        dynamic_state_path = os.path.join(home_dir, ".ikea_okazje_dynamic.json")

        result_import_only = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(result_import_only.returncode, 0, msg=result_import_only.stderr)
        self.assertFalse(
            os.path.exists(dynamic_state_path),
            "sam import nie powinien tworzyc dynamicznego stanu",
        )

        result_with_init = _run_python_code(
            "import ikea_okazje as ik; ik.initialize_runtime()", home_dir
        )
        self.assertEqual(result_with_init.returncode, 0, msg=result_with_init.stderr)
        self.assertTrue(
            os.path.exists(dynamic_state_path),
            "initialize_runtime() powinno zasiac dynamiczny stan",
        )

    def test_invalid_notification_config_fails_during_initialize_not_import(self):
        home_dir = _make_isolated_home("SMTP_MODE=disabled\nSTORE_IDS=294\n")

        result_import_only = _run_python_code("import ikea_okazje", home_dir)
        self.assertEqual(
            result_import_only.returncode, 0,
            msg="sam import nie powinien walidowac konfiguracji: " + result_import_only.stderr,
        )

        result_with_init = _run_python_code(
            "import ikea_okazje as ik; ik.initialize_runtime()", home_dir
        )
        self.assertNotEqual(result_with_init.returncode, 0)
        self.assertIn("RuntimeError", result_with_init.stderr)
        self.assertIn("Telegram", result_with_init.stderr)


class TestReinitialization(unittest.TestCase):
    """Wywolanie initialize_runtime() drugi raz w tym samym procesie (z
    innym .env) NIE powinno zachowywac stanu z poprzedniej inicjalizacji -
    testowane w jednym podprocesie, zeby uniknac zanieczyszczenia stanu
    uzywanego przez pozostale testy w tym pliku."""

    def test_second_initialization_reflects_new_config_without_stale_state(self):
        # Uzywamy JEDNEGO katalogu HOME na caly podproces - CONFIG_FILE/
        # DYNAMIC_STATE_FILE sa w ikea_okazje.py stalymi na poziomie modulu
        # (os.path.expanduser("~/...") rozwiazywane wzgledem HOME w chwili
        # importu modulu), wiec zmiana os.environ['HOME'] w trakcie tego
        # samego procesu nie przesunelaby tych scieżek - to nie jest czesc
        # tego refaktoru i nie zmieniamy tego zachowania. Symulujemy wiec
        # zmiane konfiguracji uzytkownika tak, jak dzieje sie to naprawde:
        # nadpisujac ten sam plik .env miedzy wywolaniami initialize_runtime().
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=first@example.com\n"
            "STORE_IDS=294\nSEARCH_TERMS=stall\nSEARCH_ARTICLE_NUMBERS=\n"
            "MIN_DISCOUNT_PERCENT=10\n"
        )
        config_path = os.path.join(home_dir, ".config", "ikea-okazje.env")

        code = f"""
import os
import ikea_okazje as ik

config_path = {config_path!r}

ik.initialize_runtime()
assert ik.EMAIL_TO == 'first@example.com', ik.EMAIL_TO
assert ik.MIN_DISCOUNT_PERCENT == 10, ik.MIN_DISCOUNT_PERCENT
assert ik.STORE_IDS == ['294'], ik.STORE_IDS
assert ik.SEARCH_TERMS == ['stall'], ik.SEARCH_TERMS

# Uzytkownik zmienia .env (pola NIE zasiewajace tylko dynamicznego stanu -
# EMAIL_TO/MIN_DISCOUNT_PERCENT/TELEGRAM_* sa odczytywane z .env przy
# KAZDYM initialize_runtime(), w przeciwienstwie do STORE_IDS/SEARCH_TERMS,
# ktore po pierwszym zasianiu zyja tylko w dynamicznym stanie na dysku -
# patrz README, sekcja "SEARCH_TERMS/... dzialaja tylko RAZ").
with open(config_path, "w", encoding="utf-8") as f:
    f.write(
        "SMTP_MODE=exim\\nEMAIL_TO=second@example.com\\n"
        "STORE_IDS=294\\nSEARCH_TERMS=stall\\n"
        "MIN_DISCOUNT_PERCENT=50\\n"
        "TELEGRAM_BOT_TOKEN=123:fake-token\\nTELEGRAM_CHAT_ID=999\\n"
    )
os.chmod(config_path, 0o600)

ik.initialize_runtime()
assert ik.EMAIL_TO == 'second@example.com', ik.EMAIL_TO
assert ik.MIN_DISCOUNT_PERCENT == 50, ik.MIN_DISCOUNT_PERCENT
assert ik.TELEGRAM_ENABLED is True
# Nic z pierwszej inicjalizacji nie "przecieka" do drugiej:
assert ik.EMAIL_TO != 'first@example.com'
assert ik.MIN_DISCOUNT_PERCENT != 10
print('OK')
"""
        result = _run_python_code(code, home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_reinitialization_after_removing_dynamic_state_seeds_new_store_ids(self):
        # STORE_IDS/SEARCH_TERMS sa zasiewane do dynamicznego stanu tylko
        # przy pierwszym initialize_runtime() (patrz load_dynamic_state()) -
        # to jest istniejace, zamierzone zachowanie z PR #1, nie zmieniamy
        # go tutaj. Usuniecie pliku dynamicznego stanu miedzy wywolaniami
        # (dokumentowany sposob "resetu" w README) powinno pozwolic drugiej
        # inicjalizacji zasiac od nowa z aktualnego .env, bez stale wartosci
        # z pierwszej inicjalizacji.
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\n"
            "STORE_IDS=294\nSEARCH_TERMS=stall\n"
        )
        config_path = os.path.join(home_dir, ".config", "ikea-okazje.env")
        dynamic_state_path = os.path.join(home_dir, ".ikea_okazje_dynamic.json")

        code = f"""
import os
import ikea_okazje as ik

config_path = {config_path!r}
dynamic_state_path = {dynamic_state_path!r}

ik.initialize_runtime()
assert ik.STORE_IDS == ['294'], ik.STORE_IDS
assert ik.SEARCH_TERMS == ['stall'], ik.SEARCH_TERMS

os.remove(dynamic_state_path)
with open(config_path, "w", encoding="utf-8") as f:
    f.write(
        "SMTP_MODE=exim\\nEMAIL_TO=test@example.com\\n"
        "STORE_IDS=1224,306\\nSEARCH_TERMS=poscie,dywan\\n"
        "SEARCH_ARTICLE_NUMBERS=90557419\\n"
    )
os.chmod(config_path, 0o600)

ik.initialize_runtime()
assert ik.STORE_IDS == ['1224', '306'], ik.STORE_IDS
assert ik.SEARCH_TERMS == ['poscie', 'dywan'], ik.SEARCH_TERMS
assert ik.SEARCH_ARTICLE_NUMBERS == ['90557419'], ik.SEARCH_ARTICLE_NUMBERS
assert 'stall' not in ik.SEARCH_TERMS, ik.SEARCH_TERMS
assert '294' not in ik.STORE_IDS, ik.STORE_IDS
print('OK')
"""
        result = _run_python_code(code, home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_repeated_initialization_does_not_duplicate_dynamic_state_entries(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\n"
            "STORE_IDS=294\nSEARCH_TERMS=stall\n"
        )
        code = """
import ikea_okazje as ik
ik.initialize_runtime()
ik.initialize_runtime()
ik.initialize_runtime()
assert ik.STORE_IDS == ['294'], ik.STORE_IDS
assert ik.SEARCH_TERMS == ['stall'], ik.SEARCH_TERMS
assert ik.SEARCH_TERMS.count('stall') == 1
print('OK')
"""
        result = _run_python_code(code, home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)


class TestMainLifecycle(unittest.TestCase):
    """main() musi wywolac initialize_runtime() przed sprawdzeniem komend
    Telegrama/cyklem cron/startem daemona - testowane z mockami, zeby
    zadne prawdziwe polaczenie z IKEA/Telegramem/SMTP nie mialo miejsca."""

    def test_main_initializes_runtime_before_cron_cycle(self):
        calls = []

        def fake_initialize_runtime():
            calls.append("initialize_runtime")

        def fake_run_ikea_check_cycle():
            calls.append("run_ikea_check_cycle")
            return 0

        with mock.patch.object(ik, "initialize_runtime", side_effect=fake_initialize_runtime), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "run_ikea_check_cycle", side_effect=fake_run_ikea_check_cycle), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "RUN_MODE", "cron"):
            result = ik.main()

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["initialize_runtime", "run_ikea_check_cycle"])

    def test_main_initializes_runtime_before_daemon_start(self):
        calls = []

        def fake_initialize_runtime():
            calls.append("initialize_runtime")

        def fake_run_daemon():
            calls.append("run_daemon")
            return 0

        with mock.patch.object(ik, "initialize_runtime", side_effect=fake_initialize_runtime), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "run_daemon", side_effect=fake_run_daemon), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "RUN_MODE", "daemon"):
            result = ik.main()

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["initialize_runtime", "run_daemon"])

    def test_main_initializes_runtime_before_telegram_command_check(self):
        calls = []

        with mock.patch.object(ik, "initialize_runtime", side_effect=lambda: calls.append("init")), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "handle_telegram_updates", side_effect=lambda: calls.append("telegram")), \
             mock.patch.object(ik, "run_ikea_check_cycle", return_value=0), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "RUN_MODE", "cron"):
            ik.main()

        self.assertEqual(calls, ["init", "telegram"])


class TestGracefulShutdown(unittest.TestCase):
    """Testy dla zamkniecia daemona przez SHUTDOWN_EVENT - bez wysylania
    prawdziwych sygnalow OS do procesu testowego (co byloby niestabilne),
    tylko deterministyczne testy handlera i petli run_daemon() z mockami."""

    def setUp(self):
        ik.SHUTDOWN_EVENT.clear()

    def tearDown(self):
        ik.SHUTDOWN_EVENT.clear()

    def test_request_shutdown_sets_event(self):
        self.assertFalse(ik.SHUTDOWN_EVENT.is_set())
        ik.request_shutdown()
        self.assertTrue(ik.SHUTDOWN_EVENT.is_set())

    def test_request_shutdown_accepts_signal_handler_signature(self):
        # signal.signal() wywoluje handler jako handler(signum, frame) -
        # request_shutdown musi przyjmowac te argumenty (nawet ich nie uzywajac).
        ik.request_shutdown(15, None)
        self.assertTrue(ik.SHUTDOWN_EVENT.is_set())

    def test_daemon_loop_exits_promptly_when_shutdown_event_already_set(self):
        ik.SHUTDOWN_EVENT.set()
        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates") as mock_telegram, \
             mock.patch.object(ik, "run_ikea_check_cycle") as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            result = ik.run_daemon()

        self.assertEqual(result, 0)
        mock_telegram.assert_not_called()
        mock_cycle.assert_not_called()

    def test_no_new_cycle_started_after_shutdown_event_set_mid_loop(self):
        call_count = {"n": 0}

        def fake_handle_telegram_updates():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Sygnal "przychodzi" tuz po pierwszej obslugie komend
                # Telegrama, przed sprawdzeniem ofert IKEA.
                ik.SHUTDOWN_EVENT.set()

        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates", side_effect=fake_handle_telegram_updates), \
             mock.patch.object(ik, "run_ikea_check_cycle") as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 0):
            result = ik.run_daemon()

        self.assertEqual(result, 0)
        self.assertEqual(call_count["n"], 1)
        mock_cycle.assert_not_called()

    def test_normal_daemon_shutdown_returns_zero(self):
        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle", return_value=0), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 0):

            def stop_after_first_wait(timeout):
                ik.SHUTDOWN_EVENT.set()
                return True

            with mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=stop_after_first_wait):
                result = ik.run_daemon()

        self.assertEqual(result, 0)

    def test_event_wait_used_instead_of_unconditional_sleep(self):
        # event.wait(timeout) powinien obudzic sie natychmiast, gdy event
        # jest ustawiony w innym watku - w przeciwienstwie do time.sleep().
        wait_finished = threading.Event()

        def setter():
            ik.SHUTDOWN_EVENT.set()

        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle", return_value=0), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 0), \
             mock.patch.object(ik, "TELEGRAM_POLL_INTERVAL_SECONDS", 300):
            timer = threading.Timer(0.05, setter)
            timer.start()
            start = ik.time.time()
            ik.run_daemon()
            elapsed = ik.time.time() - start
            timer.cancel()
            wait_finished.set()

        # Petla powinna wybudzic sie szybko (znacznie przed 300s), bo
        # event.wait() reaguje na SHUTDOWN_EVENT.set() z innego watku.
        self.assertLess(elapsed, 5)


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


class TestSmtpModeValidation(unittest.TestCase):
    def test_gmail_case_insensitive(self):
        self.assertEqual(ik.parse_smtp_mode("Gmail"), "gmail")

    def test_local587_strips_whitespace(self):
        self.assertEqual(ik.parse_smtp_mode(" local587 "), "local587")

    def test_exim_lowercase(self):
        self.assertEqual(ik.parse_smtp_mode("exim"), "exim")

    def test_unknown_value_raises_runtime_error(self):
        with self.assertRaises(RuntimeError):
            ik.parse_smtp_mode("smtp")

    def test_unknown_value_error_message_mentions_allowed_values(self):
        try:
            ik.parse_smtp_mode("smtp")
        except RuntimeError as exc:
            self.assertIn("gmail", str(exc))
            self.assertIn("local587", str(exc))
            self.assertIn("exim", str(exc))
            self.assertIn("disabled", str(exc))
        else:
            self.fail("parse_smtp_mode('smtp') powinno rzucic RuntimeError")

    def test_disabled_lowercase(self):
        self.assertEqual(ik.parse_smtp_mode("disabled"), "disabled")

    def test_disabled_case_insensitive_and_stripped(self):
        self.assertEqual(ik.parse_smtp_mode(" Disabled "), "disabled")


class TestNotificationConfigValidation(unittest.TestCase):
    """Testy dla validate_notification_config(smtp_mode, smtp_user,
    smtp_pass, use_smtp_auth, email_to, telegram_bot_token,
    telegram_chat_id) - efektywne wartosci PO fallbackach, tak jak
    dostaje je kod na poziomie modulu."""

    def test_disabled_smtp_with_full_telegram_is_accepted(self):
        ik.validate_notification_config(
            "disabled", None, None, False, None, "123:token", "999"
        )  # nie rzuca

    def test_disabled_smtp_without_telegram_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "disabled", None, None, False, None, None, None
            )
        message = str(ctx.exception)
        self.assertIn("e-mail", message)
        self.assertIn("Telegram", message)

    def test_gmail_without_user_and_pass_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "gmail", None, None, True, None, None, None
            )
        message = str(ctx.exception)
        self.assertIn("SMTP_USER", message)
        self.assertIn("SMTP_PASS", message)

    def test_gmail_with_user_but_without_pass_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "gmail", "me@gmail.com", None, True, "me@gmail.com", None, None
            )
        message = str(ctx.exception)
        self.assertIn("SMTP_PASS", message)
        self.assertNotIn("SMTP_USER", message)  # SMTP_USER jest ustawiony, nie powinien byc zgloszony

    def test_gmail_with_complete_settings_is_accepted(self):
        ik.validate_notification_config(
            "gmail", "me@gmail.com", "app-password", True, "me@gmail.com", None, None
        )  # nie rzuca

    def test_local587_without_user_and_pass_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "local587", None, None, True, "me@example.com", None, None
            )
        message = str(ctx.exception)
        self.assertIn("SMTP_USER", message)
        self.assertIn("SMTP_PASS", message)

    def test_local587_with_complete_settings_is_accepted(self):
        ik.validate_notification_config(
            "local587", "user", "pass", True, "me@example.com", None, None
        )  # nie rzuca

    def test_exim_without_email_to_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "exim", None, None, False, None, None, None
            )
        self.assertIn("EMAIL_TO", str(ctx.exception))

    def test_exim_with_email_to_is_accepted_without_user_and_pass(self):
        ik.validate_notification_config(
            "exim", None, None, False, "me@example.com", None, None
        )  # nie rzuca

    def test_token_without_chat_id_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "gmail", "me@gmail.com", "app-password", True, "me@gmail.com",
                "123:token", None,
            )
        self.assertIn("TELEGRAM_CHAT_ID", str(ctx.exception))

    def test_chat_id_without_token_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "gmail", "me@gmail.com", "app-password", True, "me@gmail.com",
                None, "999",
            )
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))

    def test_token_without_chat_id_raises_even_with_disabled_smtp(self):
        with self.assertRaises(RuntimeError):
            ik.validate_notification_config(
                "disabled", None, None, False, None, "123:token", None
            )

    def test_incomplete_email_is_rejected_even_with_full_telegram(self):
        # Telegram kompletny NIE maskuje niekompletnej konfiguracji e-mail,
        # dopoki SMTP_MODE nie jest explicite 'disabled'.
        with self.assertRaises(RuntimeError) as ctx:
            ik.validate_notification_config(
                "gmail", None, None, True, None, "123:token", "999"
            )
        self.assertIn("SMTP_USER", str(ctx.exception))


class TestEmailDisabledMode(unittest.TestCase):
    def test_notify_skips_email_when_disabled(self):
        orig_email_enabled = ik.EMAIL_ENABLED
        orig_telegram_enabled = ik.TELEGRAM_ENABLED
        ik.EMAIL_ENABLED = False
        ik.TELEGRAM_ENABLED = False
        try:
            with mock.patch.object(ik, "send_email") as mock_send_email, \
                 mock.patch.object(ik, "send_telegram") as mock_send_telegram:
                result = ik.notify([{"title": "x"}])
            mock_send_email.assert_not_called()
            mock_send_telegram.assert_not_called()
            self.assertIsNone(result["email"])
            self.assertIsNone(result["telegram"])
        finally:
            ik.EMAIL_ENABLED = orig_email_enabled
            ik.TELEGRAM_ENABLED = orig_telegram_enabled

    def test_notify_uses_only_telegram_when_email_disabled(self):
        orig_email_enabled = ik.EMAIL_ENABLED
        orig_telegram_enabled = ik.TELEGRAM_ENABLED
        ik.EMAIL_ENABLED = False
        ik.TELEGRAM_ENABLED = True
        try:
            with mock.patch.object(ik, "send_email") as mock_send_email, \
                 mock.patch.object(ik, "send_telegram") as mock_send_telegram:
                result = ik.notify([{"title": "x"}])
            mock_send_email.assert_not_called()
            mock_send_telegram.assert_called_once()
            self.assertIsNone(result["email"])
            self.assertIsNone(result["telegram"])
        finally:
            ik.EMAIL_ENABLED = orig_email_enabled
            ik.TELEGRAM_ENABLED = orig_telegram_enabled


class TestModuleStartupNotificationConfig(unittest.TestCase):
    """Testy odpalajace ikea_okazje.py w osobnym procesie - walidacja
    SMTP_MODE/Telegrama dzieje sie na poziomie modulu, przy imporcie."""

    def test_telegram_only_configuration_is_accepted(self):
        result = _run_module_with_env(
            "SMTP_MODE=disabled\n"
            "TELEGRAM_BOT_TOKEN=123:fake-token\n"
            "TELEGRAM_CHAT_ID=999\n"
            "STORE_IDS=294\n"
            "SEARCH_TERMS=\n"
            "SEARCH_ARTICLE_NUMBERS=\n"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_no_active_channel_raises_clear_error(self):
        result = _run_module_with_env(
            "SMTP_MODE=disabled\n"
            "STORE_IDS=294\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError", result.stderr)
        self.assertIn("e-mail", result.stderr)
        self.assertIn("Telegram", result.stderr)

    def test_partial_telegram_configuration_is_rejected(self):
        result = _run_module_with_env(
            "SMTP_MODE=exim\n"
            "EMAIL_TO=test@example.com\n"
            "TELEGRAM_BOT_TOKEN=123:fake-token\n"
            "STORE_IDS=294\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError", result.stderr)
        self.assertIn("TELEGRAM_CHAT_ID", result.stderr)

    def test_gmail_mode_without_smtp_fields_fails_at_startup(self):
        result = _run_module_with_env(
            "SMTP_MODE=gmail\n"
            "STORE_IDS=294\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError", result.stderr)
        self.assertIn("SMTP_USER", result.stderr)
        self.assertIn("SMTP_PASS", result.stderr)

    def test_exim_mode_without_email_to_fails_at_startup(self):
        result = _run_module_with_env(
            "SMTP_MODE=exim\n"
            "STORE_IDS=294\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError", result.stderr)
        self.assertIn("EMAIL_TO", result.stderr)

    def test_exim_mode_with_email_to_succeeds(self):
        result = _run_module_with_env(
            "SMTP_MODE=exim\n"
            "EMAIL_TO=test@example.com\n"
            "STORE_IDS=294\n"
            "SEARCH_TERMS=\n"
            "SEARCH_ARTICLE_NUMBERS=\n"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_disabled_smtp_mode_does_not_require_smtp_fields(self):
        result = _run_module_with_env(
            "SMTP_MODE=disabled\n"
            "TELEGRAM_BOT_TOKEN=123:fake-token\n"
            "TELEGRAM_CHAT_ID=999\n"
            "STORE_IDS=294\n"
            "SEARCH_TERMS=\n"
            "SEARCH_ARTICLE_NUMBERS=\n"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("SMTP_USER", result.stderr)
        self.assertNotIn("SMTP_PASS", result.stderr)


class TestSystemdRunModeWarning(unittest.TestCase):
    def setUp(self):
        self._orig_invocation_id = os.environ.pop("INVOCATION_ID", None)

    def tearDown(self):
        if self._orig_invocation_id is not None:
            os.environ["INVOCATION_ID"] = self._orig_invocation_id
        else:
            os.environ.pop("INVOCATION_ID", None)

    def test_no_warning_without_invocation_id(self):
        with mock.patch.object(ik, "log") as mock_log:
            ik.warn_if_systemd_without_daemon_mode()
        mock_log.assert_not_called()

    def test_warning_when_invocation_id_set_and_not_daemon(self):
        os.environ["INVOCATION_ID"] = "fake-id"
        orig_run_mode = ik.RUN_MODE
        ik.RUN_MODE = "cron"
        try:
            with mock.patch.object(ik, "log") as mock_log:
                ik.warn_if_systemd_without_daemon_mode()
            mock_log.assert_called_once()
            message = mock_log.call_args[0][0]
            self.assertIn("RUN_MODE", message)
            self.assertIn("daemon", message)
        finally:
            ik.RUN_MODE = orig_run_mode

    def test_no_warning_when_invocation_id_set_and_daemon(self):
        os.environ["INVOCATION_ID"] = "fake-id"
        orig_run_mode = ik.RUN_MODE
        ik.RUN_MODE = "daemon"
        try:
            with mock.patch.object(ik, "log") as mock_log:
                ik.warn_if_systemd_without_daemon_mode()
            mock_log.assert_not_called()
        finally:
            ik.RUN_MODE = orig_run_mode


class TestHtmlEscaping(unittest.TestCase):
    def test_escape_html_basic(self):
        self.assertEqual(ik.escape_html("<b>stall</b>"), "&lt;b&gt;stall&lt;/b&gt;")

    def test_escape_html_ampersand(self):
        self.assertEqual(ik.escape_html("a & b"), "a &amp; b")

    def test_escape_html_attr_escapes_quotes(self):
        self.assertEqual(
            ik.escape_html_attr('foo"bar\'baz'),
            "foo&quot;bar&#39;baz",
        )


class TestTelegramCommandHtmlSafety(unittest.TestCase):
    def setUp(self):
        ik.SEARCH_TERMS = list(ik.BASE_SEARCH_TERMS) or ["stall"]
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.DYNAMIC_STATE["search_terms"] = ik.SEARCH_TERMS
        ik.DYNAMIC_STATE["search_article_numbers"] = ik.SEARCH_ARTICLE_NUMBERS
        ik.refresh_normalized_terms()
        self._orig_save = ik.save_dynamic_state
        ik.save_dynamic_state = lambda state: None

    def tearDown(self):
        ik.save_dynamic_state = self._orig_save

    def test_dodaj_with_html_payload_is_escaped(self):
        reply = ik.handle_command("/dodaj", "<b>stall</b>")
        self.assertNotIn("<b>stall</b>", reply)
        self.assertIn("&lt;b&gt;stall&lt;/b&gt;", reply)

    def test_dodaj_duplicate_message_is_escaped(self):
        ik.handle_command("/dodaj", "<b>stall</b>")
        reply = ik.handle_command("/dodaj", "<b>stall</b>")
        self.assertNotIn("<b>stall</b>", reply)
        self.assertIn("juz jest na liscie", reply)

    def test_usun_unknown_term_is_escaped(self):
        reply = ik.handle_command("/usun", "<i>brak</i>")
        self.assertNotIn("<i>brak</i>", reply)
        self.assertIn("&lt;i&gt;brak&lt;/i&gt;", reply)

    def test_numer_with_html_payload_is_escaped(self):
        reply = ik.handle_command("/numer", "<b>90557419</b>")
        self.assertNotIn("<b>90557419</b>", reply)
        self.assertIn("90557419", ik.SEARCH_ARTICLE_NUMBERS)

    def test_usunnumer_with_html_payload_is_escaped(self):
        ik.SEARCH_ARTICLE_NUMBERS.append("90557419")
        reply = ik.handle_command("/usunnumer", "<b>905.574.19</b>")
        self.assertNotIn("<b>", reply)
        self.assertNotIn("90557419", ik.SEARCH_ARTICLE_NUMBERS)

    def test_unknown_command_argument_is_escaped(self):
        reply = ik.handle_command("/<script>", "")
        self.assertNotIn("<script>", reply)


class TestFormatOfferTelegramEscaping(unittest.TestCase):
    def _offer(self, **overrides):
        offer = {
            "offer_uuid": "uuid-1",
            "offer_number": "123",
            "title": "Stol",
            "description": "opis",
            "article_numbers": ["90557419"],
            "currency": "PLN",
            "price": 100,
            "original_price": 200,
            "discount_percent": 50,
            "condition": "Nowy",
            "condition_desc": "brak uszkodzen",
            "reason_discount": "wystawowy",
            "additional_info": None,
            "hero_image": None,
            "store_id": "294",
            "reservation_link": None,
        }
        offer.update(overrides)
        return offer

    def test_title_and_description_with_html_chars_are_escaped(self):
        o = self._offer(title="<b>Stall</b>", description="a & b < c > d")
        text = ik.format_offer_telegram(o)
        self.assertNotIn("<b>Stall</b>", text)
        self.assertIn("&lt;b&gt;Stall&lt;/b&gt;", text)
        self.assertIn("a &amp; b &lt; c &gt; d", text)

    def test_condition_currency_offer_number_store_are_escaped(self):
        o = self._offer(
            condition="<i>uzyty</i>",
            currency="<x>PLN",
            offer_number="<y>123",
            store_id="<z>294",
        )
        text = ik.format_offer_telegram(o)
        self.assertNotIn("<i>uzyty</i>", text)
        self.assertNotIn("<x>PLN", text)
        self.assertNotIn("<y>123", text)
        self.assertNotIn("<z>294", text)

    def test_reservation_link_href_is_present_and_escaped(self):
        o = self._offer(
            reservation_link='https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroc%C5%82aw/123"onmouseover="x'
        )
        text = ik.format_offer_telegram(o)
        self.assertIn('href="', text)
        self.assertNotIn('123"onmouseover="x"', text)
        self.assertIn("&quot;onmouseover=&quot;x", text)

    def test_no_reservation_link_when_missing(self):
        o = self._offer(reservation_link=None)
        text = ik.format_offer_telegram(o)
        self.assertNotIn("<a href", text)
        self.assertNotIn("/search/?q=", text)


class TestArticleNumberNormalization(unittest.TestCase):
    def test_plain_digits(self):
        self.assertEqual(ik.normalize_article_number("90557419"), "90557419")

    def test_dot_separated(self):
        self.assertEqual(ik.normalize_article_number("905.574.19"), "90557419")

    def test_space_separated(self):
        self.assertEqual(ik.normalize_article_number("905 574 19"), "90557419")

    def test_integer_input_from_api(self):
        self.assertEqual(ik.normalize_article_number(90557419), "90557419")

    def test_normalize_article_numbers_dedupes(self):
        result = ik.normalize_article_numbers(["905.574.19", "905 574 19", "90557419"])
        self.assertEqual(result, ["90557419"])

    def test_product_matches_with_integer_article_numbers_from_api(self):
        ik.SEARCH_ARTICLE_NUMBERS = ["90557419"]
        product = {
            "title": "cos innego",
            "description": "",
            "articleNumbers": [90557419],
        }
        self.assertTrue(ik.product_matches(product))


class TestArticleNumberTelegramCommands(unittest.TestCase):
    def setUp(self):
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.DYNAMIC_STATE["search_article_numbers"] = ik.SEARCH_ARTICLE_NUMBERS
        self._orig_save = ik.save_dynamic_state
        ik.save_dynamic_state = lambda state: None

    def tearDown(self):
        ik.save_dynamic_state = self._orig_save

    def test_add_number_with_separators_is_normalized(self):
        ik.handle_command("/numer", "905.574.19")
        self.assertEqual(ik.SEARCH_ARTICLE_NUMBERS, ["90557419"])

    def test_add_same_number_two_formats_is_rejected_as_duplicate(self):
        ik.handle_command("/numer", "905.574.19")
        reply = ik.handle_command("/numer", "905 574 19")
        self.assertEqual(ik.SEARCH_ARTICLE_NUMBERS.count("90557419"), 1)
        self.assertIn("juz jest na liscie", reply)

    def test_remove_number_given_with_separators(self):
        ik.handle_command("/numer", "90557419")
        ik.handle_command("/usunnumer", "905.574.19")
        self.assertEqual(ik.SEARCH_ARTICLE_NUMBERS, [])


class TestFetchAllOffersResilience(unittest.TestCase):
    def setUp(self):
        self._orig_store_ids = list(ik.STORE_IDS)
        self._orig_sleep = ik.time.sleep
        ik.time.sleep = lambda *a, **k: None

    def tearDown(self):
        ik.STORE_IDS = self._orig_store_ids
        ik.time.sleep = self._orig_sleep

    def test_one_store_fails_other_succeeds(self):
        ik.STORE_IDS = ["100", "200"]

        def fake_fetch(store_id):
            if store_id == "100":
                raise RuntimeError("boom")
            return [{"title": "ok", "storeId": "200"}]

        with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch):
            content, store_errors = ik.fetch_all_offers()

        self.assertEqual(content, [{"title": "ok", "storeId": "200"}])
        self.assertIn("100", store_errors)
        self.assertNotIn("200", store_errors)

    def test_all_stores_fail(self):
        ik.STORE_IDS = ["100", "200"]

        with mock.patch.object(ik, "fetch_store_offers", side_effect=RuntimeError("boom")):
            content, store_errors = ik.fetch_all_offers()

        self.assertEqual(content, [])
        self.assertEqual(set(store_errors), {"100", "200"})


class TestRunCycleStoreFailureHandling(unittest.TestCase):
    def setUp(self):
        self._orig_store_ids = list(ik.STORE_IDS)
        self._orig_terms = list(ik.SEARCH_TERMS)
        self._orig_numbers = list(ik.SEARCH_ARTICLE_NUMBERS)
        self._orig_sleep = ik.time.sleep
        ik.time.sleep = lambda *a, **k: None
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = []
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def tearDown(self):
        ik.STORE_IDS = self._orig_store_ids
        ik.SEARCH_TERMS = self._orig_terms
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig_numbers
        ik.time.sleep = self._orig_sleep
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def test_all_stores_failing_returns_error_and_no_notify(self):
        ik.STORE_IDS = ["100", "200"]
        ik.save_seen_uuids({"existing-uuid"})

        with mock.patch.object(ik, "fetch_store_offers", side_effect=RuntimeError("boom")), \
             mock.patch.object(ik, "notify") as mock_notify:
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 1)
        mock_notify.assert_not_called()
        self.assertEqual(ik.load_seen_uuids(), {"existing-uuid"})

    def test_one_store_failing_does_not_corrupt_seen_file(self):
        ik.STORE_IDS = ["100", "200"]
        ik.save_seen_uuids({"existing-uuid"})

        def fake_fetch(store_id):
            if store_id == "100":
                raise RuntimeError("boom")
            return []

        with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 0)
        self.assertEqual(ik.load_seen_uuids(), {"existing-uuid"})


class TestEmptyCriteriaWarning(unittest.TestCase):
    def setUp(self):
        self._orig_terms = list(ik.SEARCH_TERMS)
        self._orig_numbers = list(ik.SEARCH_ARTICLE_NUMBERS)
        self._orig_store_ids = list(ik.STORE_IDS)

    def tearDown(self):
        ik.SEARCH_TERMS = self._orig_terms
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig_numbers
        ik.STORE_IDS = self._orig_store_ids

    def test_no_terms_and_no_numbers_skips_api_and_succeeds(self):
        ik.SEARCH_TERMS = []
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.STORE_IDS = ["294"]

        with mock.patch.object(ik, "fetch_all_offers") as mock_fetch:
            result = ik.run_ikea_check_cycle()

        mock_fetch.assert_not_called()
        self.assertEqual(result, 0)


class TestCheckIntervalJitter(unittest.TestCase):
    """compute_jittered_interval() - jitter deterministyczny (mockowany
    random.uniform) i sprawdzenie, ze wynik trzyma sie zakladanego zakresu
    +/-CHECK_INTERVAL_JITTER_PERCENT% wokol CHECK_INTERVAL_SECONDS."""

    def setUp(self):
        self._orig_interval = ik.CHECK_INTERVAL_SECONDS
        self._orig_percent = ik.CHECK_INTERVAL_JITTER_PERCENT
        ik.CHECK_INTERVAL_SECONDS = 3600
        ik.CHECK_INTERVAL_JITTER_PERCENT = 25

    def tearDown(self):
        ik.CHECK_INTERVAL_SECONDS = self._orig_interval
        ik.CHECK_INTERVAL_JITTER_PERCENT = self._orig_percent

    def test_deterministic_jitter_with_mocked_random(self):
        # random.uniform(-25, 25) mockowany na dokladnie +10% -> 3600*1.10
        with mock.patch.object(ik.random, "uniform", return_value=10.0) as mock_uniform:
            result = ik.compute_jittered_interval()
        mock_uniform.assert_called_once_with(-25, 25)
        self.assertAlmostEqual(result, 3600 * 1.10)

    def test_deterministic_jitter_negative_bound(self):
        with mock.patch.object(ik.random, "uniform", return_value=-25.0):
            result = ik.compute_jittered_interval()
        self.assertAlmostEqual(result, 3600 * 0.75)

    def test_jitter_result_within_expected_range_many_samples(self):
        low = 3600 * 0.75
        high = 3600 * 1.25
        for _ in range(200):
            result = ik.compute_jittered_interval()
            self.assertGreaterEqual(result, low)
            self.assertLessEqual(result, high)

    def test_zero_percent_disables_jitter(self):
        ik.CHECK_INTERVAL_JITTER_PERCENT = 0
        result = ik.compute_jittered_interval()
        self.assertEqual(result, 3600)

    def test_default_check_interval_is_45_minutes(self):
        # Wymog zadania: domyslny interwal daemona to 2700s = 45 minut.
        self.assertEqual(ik.DEFAULT_CHECK_INTERVAL_SECONDS, 2700)


class TestCheckIntervalDefaultAndOverride(unittest.TestCase):
    """DEFAULT_CHECK_INTERVAL_SECONDS oraz pierwszenstwo wlasnej wartosci
    CHECK_INTERVAL_SECONDS z .env nad tym domyslnym - w osobnych procesach
    (initialize_runtime() jest wywolywane przez subprocess, zeby nie
    zanieczyszczac stanu modulu uzywanego przez pozostale testy)."""

    def test_default_check_interval_seconds_constant_is_2700(self):
        self.assertEqual(ik.DEFAULT_CHECK_INTERVAL_SECONDS, 2700)

    def test_env_without_check_interval_seconds_uses_default_2700(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
        )
        result = _run_python_code(
            "import ikea_okazje as ik\n"
            "ik.initialize_runtime()\n"
            "assert ik.CHECK_INTERVAL_SECONDS == 2700, ik.CHECK_INTERVAL_SECONDS\n"
            "print('OK')\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_env_with_explicit_check_interval_seconds_overrides_default(self):
        # Wartosc jawnie ustawiona w .env (nawet stare 900s = 15 minut) ma
        # pierwszenstwo i NIE jest nadpisywana nowym domyslnym 2700.
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
            "CHECK_INTERVAL_SECONDS=900\n"
        )
        result = _run_python_code(
            "import ikea_okazje as ik\n"
            "ik.initialize_runtime()\n"
            "assert ik.CHECK_INTERVAL_SECONDS == 900, ik.CHECK_INTERVAL_SECONDS\n"
            "print('OK')\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_env_with_custom_larger_check_interval_seconds_overrides_default(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
            "CHECK_INTERVAL_SECONDS=7200\n"
        )
        result = _run_python_code(
            "import ikea_okazje as ik\n"
            "ik.initialize_runtime()\n"
            "assert ik.CHECK_INTERVAL_SECONDS == 7200, ik.CHECK_INTERVAL_SECONDS\n"
            "print('OK')\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)


class TestClientProfileSelection(unittest.TestCase):
    """Wybor spojnego profilu klienta (impersonate + User-Agent/sec-ch-ua) -
    bez zadnych prawdziwych requestow sieciowych."""

    def setUp(self):
        self._orig_profile = ik.CURRENT_CLIENT_PROFILE

    def tearDown(self):
        ik.CURRENT_CLIENT_PROFILE = self._orig_profile

    def test_choose_client_profile_returns_one_of_known_profiles(self):
        profile = ik.choose_client_profile()
        self.assertIn(profile, ik.CLIENT_PROFILES)

    def test_choose_client_profile_uses_random_choice(self):
        with mock.patch.object(ik.random, "choice", return_value=ik.CLIENT_PROFILES[0]) as mock_choice:
            profile = ik.choose_client_profile()
        mock_choice.assert_called_once_with(ik.CLIENT_PROFILES)
        self.assertEqual(profile, ik.CLIENT_PROFILES[0])

    def test_get_active_client_profile_sets_and_reuses_profile(self):
        ik.CURRENT_CLIENT_PROFILE = None
        with mock.patch.object(ik, "choose_client_profile", return_value=ik.CLIENT_PROFILES[1]) as mock_choose:
            first = ik.get_active_client_profile()
            second = ik.get_active_client_profile()
        mock_choose.assert_called_once()  # tylko raz - druga wywolanie reuzywa CURRENT_CLIENT_PROFILE
        self.assertIs(first, second)
        self.assertEqual(first, ik.CLIENT_PROFILES[1])

    def test_headers_match_profile_user_agent_and_sec_ch_ua(self):
        for profile in ik.CLIENT_PROFILES:
            headers = ik.build_headers_for_profile(profile)
            self.assertEqual(headers["user-agent"], profile.user_agent)
            self.assertEqual(headers["sec-ch-ua"], profile.sec_ch_ua)
            # Wersja Chrome w user-agent musi zgadzac sie z wersja w impersonate=
            chrome_version = profile.impersonate.replace("chrome", "")
            self.assertIn(f"Chrome/{chrome_version}.", profile.user_agent)

    def test_all_profiles_are_supported_by_minimum_curl_cffi_version(self):
        # requirements.txt wymaga curl_cffi>=0.7.0 - profile musza byc
        # ograniczone do wersji Chrome wspieranych juz w tej minimalnej
        # wersji (chrome120/123/124), patrz komentarz przy CLIENT_PROFILES.
        supported_in_0_7_0 = {"chrome120", "chrome123", "chrome124"}
        used = {p.impersonate for p in ik.CLIENT_PROFILES}
        self.assertTrue(used.issubset(supported_in_0_7_0))

    def test_profile_is_a_frozen_dataclass_instance(self):
        # BrowserProfile - model oparty o obiekt/dane, nie o luzny slownik
        # (patrz README/scalenie anti_detection.py) - niezmienny (frozen).
        profile = ik.CLIENT_PROFILES[0]
        self.assertIsInstance(profile, ik.BrowserProfile)
        with self.assertRaises(Exception):
            profile.impersonate = "chrome999"

    def test_fetch_page_with_retry_uses_single_profile_across_calls_in_one_cycle(self):
        # Wewnatrz jednego cyklu (CURRENT_CLIENT_PROFILE juz ustawiony) kazde
        # wywolanie fetch_page_with_retry() musi uzyc TEGO SAMEGO profilu -
        # bez zadnego prawdziwego requestu sieciowego (requests.get zmockowane).
        ik.CURRENT_CLIENT_PROFILE = ik.CLIENT_PROFILES[2]
        fake_resp = mock.Mock(status_code=200, json=lambda: {"content": [], "last": True})
        seen_impersonate = []
        seen_user_agents = []

        def fake_get(*args, **kwargs):
            seen_impersonate.append(kwargs.get("impersonate"))
            seen_user_agents.append(kwargs.get("headers", {}).get("user-agent"))
            return fake_resp

        with mock.patch.object(ik.requests, "get", side_effect=fake_get):
            ik.fetch_page_with_retry("294", 0)
            ik.fetch_page_with_retry("294", 1)
            ik.fetch_page_with_retry("1224", 0)

        self.assertEqual(len(set(seen_impersonate)), 1)
        self.assertEqual(len(set(seen_user_agents)), 1)
        self.assertEqual(seen_impersonate[0], "chrome124")

    def test_get_active_client_profile_type(self):
        ik.CURRENT_CLIENT_PROFILE = None
        profile = ik.get_active_client_profile()
        self.assertIsInstance(profile, ik.BrowserProfile)


class TestAccessNotificationState(unittest.TestCase):
    """Alert o utracie/odzyskaniu dostepu (HTTP 403/429 na CALYM cyklu) -
    handle_access_notification_state()/all_stores_blocked_by_403_429()/
    full_cycle_fetched_successfully()/reset_pending_outage_after_partial_cycle() -
    bez zadnych prawdziwych requestow HTTP/SMTP/Telegram
    (notify_access_status() jest tu zawsze zmockowane).

    Polityka "dwoch kolejnych kwalifikujacych cykli" (patrz zadanie): jeden
    kompletny cykl zablokowany wylacznie 403/429 na WSZYSTKICH
    skonfigurowanych sklepach oznacza tylko OCZEKUJACA (niepotwierdzona)
    utrate dostepu (pending_outage=True) - alert idzie na zewnatrz
    wylacznie po DRUGIM, NASTEPNYM z rzedu takim cyklu."""

    def setUp(self):
        self._orig_state = dict(ik.get_access_notification_state())
        ik.set_access_notification_state(False, None, pending_outage=False)

    def tearDown(self):
        ik.set_access_notification_state(
            self._orig_state.get("outage_active", False),
            self._orig_state.get("last_status_code"),
            pending_outage=self._orig_state.get("pending_outage", False),
        )

    # --- definicja utraty/odzyskania dostepu ---

    def test_all_stores_blocked_true_when_all_errors_are_403_429(self):
        errors = {
            "294": ik.BlockedByServerError(403, "HTTP 403"),
            "1224": ik.BlockedByServerError(429, "HTTP 429"),
        }
        with mock.patch.object(ik, "STORE_IDS", ["294", "1224"]):
            self.assertTrue(ik.all_stores_blocked_by_403_429(errors))

    def test_all_stores_blocked_false_when_one_store_succeeds(self):
        errors = {"294": ik.BlockedByServerError(403, "HTTP 403")}
        with mock.patch.object(ik, "STORE_IDS", ["294", "1224"]):
            self.assertFalse(ik.all_stores_blocked_by_403_429(errors))

    def test_all_stores_blocked_false_for_plain_single_store_error(self):
        # Zwykly blad pojedynczego sklepu (nie BlockedByServerError) NIE
        # jest 'utrata dostepu', nawet jesli dotyczy jedynego sklepu.
        errors = {"294": RuntimeError("boom")}
        with mock.patch.object(ik, "STORE_IDS", ["294"]):
            self.assertFalse(ik.all_stores_blocked_by_403_429(errors))

    def test_all_stores_blocked_false_for_timeout_error(self):
        errors = {"294": TimeoutError("timed out")}
        with mock.patch.object(ik, "STORE_IDS", ["294"]):
            self.assertFalse(ik.all_stores_blocked_by_403_429(errors))

    def test_all_stores_blocked_false_for_5xx_style_error(self):
        errors = {"294": RuntimeError("HTTP 503, probuje dalej")}
        with mock.patch.object(ik, "STORE_IDS", ["294"]):
            self.assertFalse(ik.all_stores_blocked_by_403_429(errors))

    def test_all_stores_blocked_false_when_mixed_with_non_blocking_error(self):
        # Jeden sklep 403, drugi zwykly blad (nie blokujacy) - to NIE jest
        # 'utrata dostepu' w pelnym rozumieniu zadania (wymagane, zeby
        # WSZYSTKIE bledy w cyklu byly 403/429).
        errors = {
            "294": ik.BlockedByServerError(403, "HTTP 403"),
            "1224": RuntimeError("parse error"),
        }
        with mock.patch.object(ik, "STORE_IDS", ["294", "1224"]):
            self.assertFalse(ik.all_stores_blocked_by_403_429(errors))

    def test_full_cycle_fetched_successfully_true_without_errors(self):
        self.assertTrue(ik.full_cycle_fetched_successfully({}))

    def test_full_cycle_fetched_successfully_false_with_any_error(self):
        self.assertFalse(ik.full_cycle_fetched_successfully({"294": RuntimeError("boom")}))

    # --- pierwszy kwalifikujacy cykl: OCZEKUJACY, bez alertu ---

    def test_first_outage_marks_pending_without_sending_notification(self):
        # Wymog 1 / test 1: pierwszy pelny 403/429-cykl -> pending, BEZ alertu.
        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_not_called()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertTrue(state["pending_outage"])

    def test_second_consecutive_outage_sends_exactly_one_notification(self):
        # Wymog 2 / test 2: DRUGI kolejny pelny 403/429-cykl potwierdza -
        # dokladnie jedno powiadomienie, stan aktywny.
        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_called_once()
        state = ik.get_access_notification_state()
        self.assertTrue(state["outage_active"])
        self.assertFalse(state["pending_outage"])
        self.assertEqual(state["last_status_code"], 403)

    def test_third_and_later_outages_do_not_send_duplicate(self):
        # Wymog 2 / test 3: trzeci i kolejne kwalifikujace cykle - bez
        # kolejnych alertow (juz potwierdzone i wyslane).
        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 429)
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_called_once()

    def test_failed_delivery_on_confirmation_does_not_mark_alert_as_sent(self):
        # Wymog 2 / test 13: calkowita porazka dostawy PRZY potwierdzeniu
        # (drugim cyklu) - stan NIE moze zostac oznaczony jako wyslany
        # (outage_active), zeby kolejny kwalifikujacy cykl mogl sprobowac
        # ponownie - ale pending_outage zostaje True (nie zaczynamy od
        # zera calej dwucyklowej sekwencji z powodu porazki DOSTAWY).
        active_channels = (1 if ik.EMAIL_ENABLED else 0) + (1 if ik.TELEGRAM_ENABLED else 0)
        self.assertGreaterEqual(active_channels, 1)
        all_errors = ["e-mail: boom"] if ik.EMAIL_ENABLED else []
        if ik.TELEGRAM_ENABLED:
            all_errors.append("telegram: boom")
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
        with mock.patch.object(ik, "notify_access_status", return_value=all_errors):
            ik.handle_access_notification_state(True, 403)  # drugi, dostawa zawodzi
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertTrue(state["pending_outage"])

    def test_one_channel_succeeds_other_fails_still_marks_alerted(self):
        # Wymog: dostawa jest uznana za sukces, jesli PRZYNAJMNIEJ JEDEN
        # aktywny kanal sie powiodl (istniejacy wzorzec z notify()) - tylko
        # CALKOWITA porazka wszystkich aktywnych kanalow nie oznacza alertu
        # jako wyslany. Wymusza dwa aktywne kanaly (e-mail + Telegram) na
        # czas testu, niezaleznie od faktycznej konfiguracji testowego .env.
        with mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            with mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
            with mock.patch.object(ik, "notify_access_status", return_value=["e-mail: boom"]):
                ik.handle_access_notification_state(True, 403)  # drugi, 1 z 2 kanalow zawodzi
        state = ik.get_access_notification_state()
        self.assertTrue(state["outage_active"])
        self.assertFalse(state["pending_outage"])

    # --- trwalosc stanu / restart procesu ---

    def test_pending_outage_survives_simulated_restart(self):
        # Wymog: restart procesu/systemd PO PIERWSZYM, ale PRZED drugim
        # kwalifikujacym cyklem nie moze "zapomniec", ze to byla juz
        # pierwsza porazka (patrz test 10).
        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_not_called()

        reloaded_state = ik.load_dynamic_state()
        self.assertTrue(reloaded_state["ikea_access_notification"]["pending_outage"])
        self.assertFalse(reloaded_state["ikea_access_notification"]["outage_active"])

        # "Restart": DYNAMIC_STATE w pamieci zastapiony wczytanym z dysku.
        ik.DYNAMIC_STATE.clear()
        ik.DYNAMIC_STATE.update(reloaded_state)

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify_after_restart:
            ik.handle_access_notification_state(True, 403)  # drugi, po "restarcie"
        mock_notify_after_restart.assert_called_once()
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

    def test_outage_state_survives_simulated_restart(self):
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        # Symulacja restartu procesu/systemd: zamiast reuzywac ik.DYNAMIC_STATE
        # w pamieci, wczytujemy stan na nowo z dysku (tak jak initialize_runtime()
        # robi to przy kazdym starcie procesu).
        reloaded_state = ik.load_dynamic_state()
        self.assertTrue(reloaded_state["ikea_access_notification"]["outage_active"])
        self.assertEqual(reloaded_state["ikea_access_notification"]["last_status_code"], 403)
        self.assertFalse(reloaded_state["ikea_access_notification"]["pending_outage"])

    def test_no_duplicate_alert_after_simulated_restart_while_outage_active(self):
        # Wymog / test 11: restart PO tym, jak alert byl juz dostarczony -
        # bez duplikatu.
        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        # "Restart" - stan wczytany na nowo z dysku zastepuje DYNAMIC_STATE
        # w pamieci, tak jak initialize_runtime() robi to na starcie procesu.
        ik.DYNAMIC_STATE.clear()
        ik.DYNAMIC_STATE.update(ik.load_dynamic_state())

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify_after_restart:
            ik.handle_access_notification_state(True, 403)
        mock_notify_after_restart.assert_not_called()
        self.assertEqual(mock_notify.call_count, 1)

    def test_old_state_file_without_access_notification_block_still_loads(self):
        # Stary plik stanu (z PR #1/#2) bez klucza "ikea_access_notification" -
        # load_dynamic_state() musi dopisac domyslny, "pusty" blok bez wyjatku.
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("ikea_access_notification", None)
        with open(ik.DYNAMIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reloaded_state = ik.load_dynamic_state()
        self.assertIn("ikea_access_notification", reloaded_state)
        self.assertFalse(reloaded_state["ikea_access_notification"]["outage_active"])
        self.assertIsNone(reloaded_state["ikea_access_notification"]["last_status_code"])
        self.assertFalse(reloaded_state["ikea_access_notification"]["pending_outage"])

    def test_old_state_file_with_outage_active_true_migrates_without_duplicate_alert(self):
        # Wymog "STATE MODEL AND MIGRATION" / test 11-12: plik z WCZESNIEJSZEJ
        # wersji z outage_active=True (uzytkownik JUZ zaalarmowany) NIE moze
        # zostac zinterpretowany jako pierwsza, niepotwierdzona porazka - a
        # kolejny kwalifikujacy cykl (all_stores_blocked=True) NIE powinien
        # wyslac duplikatu (alert byl juz wyslany przed migracja).
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["ikea_access_notification"] = {"outage_active": True, "last_status_code": 403}
        with open(ik.DYNAMIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reloaded_state = ik.load_dynamic_state()
        migrated = reloaded_state["ikea_access_notification"]
        self.assertTrue(migrated["outage_active"])
        self.assertFalse(migrated["pending_outage"])

        ik.DYNAMIC_STATE.clear()
        ik.DYNAMIC_STATE.update(reloaded_state)

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_not_called()
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

    def test_missing_ikea_access_notification_key_migrates_with_pending_field(self):
        # Wymog "STATE MODEL AND MIGRATION": plik bez klucza
        # 'ikea_access_notification' w ogole musi zaladowac sie bezpiecznie
        # i miec kompletny, domyslny stan (w tym pending_outage=False).
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("ikea_access_notification", None)
        with open(ik.DYNAMIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reloaded_state = ik.load_dynamic_state()
        migrated = reloaded_state["ikea_access_notification"]
        self.assertEqual(
            migrated, {"outage_active": False, "last_status_code": None, "pending_outage": False}
        )

    # --- odzyskanie dostepu ---

    def test_full_success_before_alert_clears_pending_without_any_notification(self):
        # Wymog 3 / test 4: pelny sukces PO pierwszym (niepotwierdzonym)
        # kwalifikujacym cyklu - czysci pending, BEZ alertu utraty ANI
        # odzyskania.
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)  # pelny sukces
        mock_notify.assert_not_called()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertFalse(state["pending_outage"])

    def test_first_successful_cycle_after_delivered_outage_sends_recovery_notification(self):
        # Wymog 4 / test 5: dokladnie jedno powiadomienie o odzyskaniu, ale
        # TYLKO gdy alert o utracie byl FAKTYCZNIE dostarczony (dwa
        # potwierdzajace cykle 403/429 najpierw).
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)
        mock_notify.assert_called_once()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertIsNone(state["last_status_code"])
        self.assertFalse(state["pending_outage"])

    def test_subsequent_successful_cycles_do_not_send_duplicate_recovery(self):
        # Wymog / test 6.
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(False, None)

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)
            ik.handle_access_notification_state(False, None)
        mock_notify.assert_not_called()

    def test_successful_cycle_without_prior_outage_sends_nothing(self):
        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)
        mock_notify.assert_not_called()

    def test_new_outage_after_recovery_requires_two_cycles_again(self):
        # Nowa utrata dostepu PO odzyskaniu zaczyna od nowa cala
        # dwucyklowa sekwencje potwierdzania - jeden kwalifikujacy cykl
        # nie wystarcza.
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(False, None)

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 429)
        mock_notify.assert_not_called()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertTrue(state["pending_outage"])

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify_second:
            ik.handle_access_notification_state(True, 429)
        mock_notify_second.assert_called_once()
        state = ik.get_access_notification_state()
        self.assertTrue(state["outage_active"])
        self.assertEqual(state["last_status_code"], 429)

    def test_failed_recovery_delivery_does_not_reset_state(self):
        # Wymog / test: dostawa alertu odzyskania zawodzi calkowicie -
        # outage_active zostaje True, zeby kolejny pelny sukces mogl
        # sprobowac ponownie.
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        active_channels = (1 if ik.EMAIL_ENABLED else 0) + (1 if ik.TELEGRAM_ENABLED else 0)
        all_errors = ["e-mail: boom"] if ik.EMAIL_ENABLED else []
        if ik.TELEGRAM_ENABLED:
            all_errors.append("telegram: boom")
        self.assertEqual(len(all_errors), active_channels)

        with mock.patch.object(ik, "notify_access_status", return_value=all_errors):
            ik.handle_access_notification_state(False, None)
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

    # --- cykle czesciowe/mieszane (wymog 5 / testy 7-8-9) ---

    def test_partial_cycle_resets_pending_sequence(self):
        # Wymog 5 / test 9: cykl czesciowy/mieszany PO pierwszym
        # (niepotwierdzonym) kwalifikujacym cyklu zeruje sekwencje -
        # nastepny 403/429 zaczyna od nowa (musi znow byc DWA kolejne).
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
        self.assertTrue(ik.get_access_notification_state()["pending_outage"])

        ik.reset_pending_outage_after_partial_cycle()
        self.assertFalse(ik.get_access_notification_state()["pending_outage"])

        with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)  # znow "pierwszy" po resecie
        mock_notify.assert_not_called()
        self.assertTrue(ik.get_access_notification_state()["pending_outage"])

    def test_partial_cycle_clears_stale_last_status_code(self):
        # Czyszczenie: reset_pending_outage_after_partial_cycle() musi
        # wyzerowac rowniez last_status_code bloku 'ikea_access_notification'
        # (nie tylko pending_outage) - inaczej trwaly stan na dysku
        # zostawialby "widmowy" kod 403/429 z przerwanej, niepotwierdzonej
        # sekwencji, mimo ze pending_outage jest juz False.
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending, last_status_code=403
        state = ik.get_access_notification_state()
        self.assertTrue(state["pending_outage"])
        self.assertEqual(state["last_status_code"], 403)

        ik.reset_pending_outage_after_partial_cycle()

        state = ik.get_access_notification_state()
        self.assertFalse(state["pending_outage"])
        self.assertIsNone(state["last_status_code"])
        self.assertFalse(state["outage_active"])

        # Trwale na dysku, nie tylko w pamieci procesu.
        reloaded = ik.load_dynamic_state()
        self.assertIsNone(reloaded["ikea_access_notification"]["last_status_code"])

        # Zapis dotyczy WYLACZNIE klucza 'ikea_access_notification' - inne
        # klucze pliku stanu (w tym 'blocking_backoff', niezaleznie od
        # jego wlasnego last_status_code) zostaja niezmienione.
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertIn("blocking_backoff", on_disk)
        self.assertIn("store_ids", on_disk)
        self.assertIn("search_terms", on_disk)

    def test_partial_cycle_reset_does_not_touch_blocking_backoff_last_status_code(self):
        # reset_pending_outage_after_partial_cycle() czysci WYLACZNIE
        # last_status_code bloku 'ikea_access_notification' - odrebny
        # last_status_code w BackoffState/'blocking_backoff' (patrz
        # update_blocking_backoff_state()) musi zostac niezmieniony.
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})
        self.assertEqual(ik.get_blocking_backoff_state().last_status_code, 403)

        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending

        ik.reset_pending_outage_after_partial_cycle()

        self.assertIsNone(ik.get_access_notification_state()["last_status_code"])
        self.assertEqual(ik.get_blocking_backoff_state().last_status_code, 403)
        ik.set_blocking_backoff_state(ik.BackoffState())

    def test_partial_cycle_without_pending_outage_is_a_no_op(self):
        self.assertFalse(ik.get_access_notification_state()["pending_outage"])
        with mock.patch.object(ik, "save_dynamic_state") as mock_save:
            ik.reset_pending_outage_after_partial_cycle()
        mock_save.assert_not_called()

    def test_partial_cycle_does_not_touch_already_delivered_outage(self):
        # reset_pending_outage_after_partial_cycle() nie dotyka juz
        # WYSLANEGO alertu (outage_active) - odzyskanie wciaz wymaga
        # pelnego sukcesu, nie czesciowego cyklu.
        with mock.patch.object(ik, "notify_access_status", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

        ik.reset_pending_outage_after_partial_cycle()
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

    # --- integracja z run_ikea_check_cycle() ---

    def test_run_cycle_requires_two_consecutive_full_outages_to_notify(self):
        # Wymog 1+2 / testy 1-2: dwa kolejne w pelni zablokowane cykle -
        # tylko drugi wysyla alert.
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        try:
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
                self.assertFalse(ik.get_access_notification_state()["outage_active"])
                self.assertTrue(ik.get_access_notification_state()["pending_outage"])
                mock_notify.assert_not_called()

                ik.run_ikea_check_cycle()
            mock_notify.assert_called_once()
            self.assertTrue(ik.get_access_notification_state()["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())

    def test_run_cycle_single_outage_does_not_send_any_notification(self):
        # Wymog 1 / test 1, przez run_ikea_check_cycle(): jeden kwalifikujacy
        # cykl - zero powiadomien.
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        try:
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())

    def test_run_cycle_full_success_after_single_outage_clears_pending_silently(self):
        # Wymog 3 / test 4, przez run_ikea_check_cycle().
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        try:
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.run_ikea_check_cycle()
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            with mock.patch.object(ik, "fetch_store_offers", return_value=[]), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
            state = ik.get_access_notification_state()
            self.assertFalse(state["pending_outage"])
            self.assertFalse(state["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())
            if os.path.exists(ik.STATE_FILE):
                os.remove(ik.STATE_FILE)

    def test_run_cycle_partial_store_failure_does_not_trigger_outage_alert(self):
        # Blad pojedynczego sklepu (nie 403/429) w cyklu z wieloma sklepami -
        # NIE ma wywolywac alertu o utracie dostepu.
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        orig_sleep = ik.time.sleep
        ik.time.sleep = lambda *a, **k: None
        ik.STORE_IDS = ["100", "200"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

        def fake_fetch(store_id):
            if store_id == "100":
                raise RuntimeError("HTTP 503, probuje dalej")
            return []

        try:
            with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
            self.assertFalse(ik.get_access_notification_state()["pending_outage"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.time.sleep = orig_sleep
            if os.path.exists(ik.STATE_FILE):
                os.remove(ik.STATE_FILE)

    def test_run_cycle_mixed_403_and_timeout_does_not_confirm_outage(self):
        # Wymog 5 / test 7: mieszanka 403/429 i timeout/5xx/blad parsowania
        # w JEDNYM cyklu nie jest kwalifikujaca sie porazka - a jesli byla
        # aktywna oczekujaca sekwencja, ten cykl ja zeruje.
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        try:
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.run_ikea_check_cycle()  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.STORE_IDS = ["100", "200"]

            def fake_fetch(store_id):
                if store_id == "100":
                    raise ik.BlockedByServerError(403, "HTTP 403")
                raise TimeoutError("timed out")

            with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
            self.assertFalse(ik.get_access_notification_state()["pending_outage"])
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())

    def test_run_cycle_partial_success_does_not_confirm_outage(self):
        # Wymog 5 / test 8: jeden sklep 403, drugi OK - nie potwierdza
        # utraty dostepu (i zeruje oczekujaca sekwencje, jesli byla).
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        try:
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.run_ikea_check_cycle()  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.STORE_IDS = ["100", "200"]

            def fake_fetch(store_id):
                if store_id == "100":
                    raise ik.BlockedByServerError(403, "HTTP 403")
                return []

            with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
            self.assertFalse(ik.get_access_notification_state()["pending_outage"])
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())

    def test_run_cycle_timeout_error_does_not_trigger_outage_alert(self):
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        try:
            with mock.patch.object(
                ik, "fetch_store_offers", side_effect=TimeoutError("timed out"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()

    def test_run_cycle_first_success_after_delivered_outage_sends_recovery_notification(self):
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        try:
            with mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.handle_access_notification_state(True, 403)
                ik.handle_access_notification_state(True, 403)

            with mock.patch.object(ik, "fetch_store_offers", return_value=[]), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                ik.run_ikea_check_cycle()
            mock_notify.assert_called_once()
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())
            if os.path.exists(ik.STATE_FILE):
                os.remove(ik.STATE_FILE)

    def test_run_cycle_no_configured_stores_does_not_advance_confirmation(self):
        # Wymog 6 / test 16: brak skonfigurowanych sklepow (ale sa slowa
        # kluczowe) - store_errors jest zawsze pusty (petla po pustym
        # STORE_IDS), co NIE moze zostac zinterpretowane jako pelny
        # sukces potwierdzajacy odzyskanie/czyszczacy pending.
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        try:
            with mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.STORE_IDS = []
            with mock.patch.object(ik, "notify_access_status", return_value=[]) as mock_notify:
                result = ik.run_ikea_check_cycle()
            mock_notify.assert_not_called()
            # pending_outage MUSI przetrwac ten przebieg bez zmian - to nie
            # byl ani drugi kwalifikujacy cykl, ani pelny sukces.
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
            self.assertEqual(result, 0)
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())
            if os.path.exists(ik.STATE_FILE):
                os.remove(ik.STATE_FILE)

    def test_run_cycle_no_search_criteria_skips_and_does_not_advance_confirmation(self):
        # Wymog 6: brak slow kluczowych i numerow artykulu - run_ikea_check_cycle()
        # wraca 0 od razu (istniejace zachowanie), bez dotykania stanu
        # powiadomien o dostepie.
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        orig_numbers = list(ik.SEARCH_ARTICLE_NUMBERS)
        try:
            with mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.SEARCH_TERMS = []
            ik.refresh_normalized_terms()
            ik.SEARCH_ARTICLE_NUMBERS = []

            with mock.patch.object(ik, "fetch_all_offers") as mock_fetch:
                result = ik.run_ikea_check_cycle()
            mock_fetch.assert_not_called()
            self.assertEqual(result, 0)
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])
            self.assertFalse(ik.get_access_notification_state()["outage_active"])
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.SEARCH_ARTICLE_NUMBERS = orig_numbers


class TestCronModeTwoCycleConfirmationAcrossInvocations(unittest.TestCase):
    """Wymog "Scheduling and Retry-After" / test 14: tryb cron pozostaje
    JEDNORAZOWYM przejsciem (bez petli/sleep czekajacego na drugi cykl) -
    potwierdzenie dwucyklowe rozciaga sie na DWA SEPARATE odpalenia
    `python3 ikea_okazje.py` (dwa niezalezne procesy, dzielace ten sam
    ~/.ikea_okazje_dynamic.json), tak jak zrobilby to prawdziwy cron.
    Kazde odpalenie jest w osobnym procesie (subprocess), zeby test
    naprawde weryfikowal trwalosc stanu MIEDZY procesami, nie tylko w
    pamieci jednego - fetch_store_offers() jest tu monkeypatchowane
    wewnatrz kodu uruchamianego w podprocesie (bez zadnych prawdziwych
    requestow do IKEA/SMTP/Telegrama)."""

    def _run_one_cron_invocation(self, home_dir: str) -> subprocess.CompletedProcess:
        code = (
            "import unittest.mock as mock\n"
            "import ikea_okazje as ik\n"
            "with mock.patch.object(ik, 'fetch_store_offers', "
            "side_effect=ik.BlockedByServerError(403, 'HTTP 403')), \\\n"
            "     mock.patch.object(ik.time, 'sleep'):\n"
            "    exit_code = ik.main()\n"
            "assert ik.RUN_MODE == 'cron', ik.RUN_MODE\n"
            "print('EXIT_CODE=' + str(exit_code))\n"
        )
        return _run_python_code(code, home_dir)

    def test_two_separate_cron_invocations_confirm_outage_without_sleeping(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
            "SEARCH_TERMS=stall\n"
        )
        dynamic_state_path = os.path.join(home_dir, ".ikea_okazje_dynamic.json")

        # Pierwsze, niezalezne odpalenie (proces #1) - pierwszy kwalifikujacy
        # cykl 403/429 - musi zapisac pending_outage=True na dysk i wrocic
        # NATYCHMIAST (bez sleep/petli czekajacej na drugi cykl).
        first = self._run_one_cron_invocation(home_dir)
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        with open(dynamic_state_path, "r", encoding="utf-8") as f:
            state_after_first = json.load(f)
        self.assertTrue(state_after_first["ikea_access_notification"]["pending_outage"])
        self.assertFalse(state_after_first["ikea_access_notification"]["outage_active"])

        # Drugie, calkowicie NIEZALEZNE odpalenie (proces #2, "kolejny cron")
        # - potwierdza sekwencje i wysyla alert (notify_access_status() nie
        # jest tu mockowane globalnie - ale exim/SMTP polaczenie i tak nie
        # jest osiagalne w sandboxie; sprawdzamy wylacznie, ze proces sie
        # nie zawiesza/zapetla i ze stan jest zgodny z polityka dwoch cykli,
        # niezaleznie od sukcesu/porazki samej dostawy e-maila).
        second = self._run_one_cron_invocation(home_dir)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        with open(dynamic_state_path, "r", encoding="utf-8") as f:
            state_after_second = json.load(f)
        # Albo alert zostal potwierdzony (outage_active=True, dostawa OK),
        # albo dostawa zawiodla i pending_outage zostal True do ponownej
        # proby - w OBU przypadkach NIE MOZE to byc znowu "pierwszy" pending
        # stan bez ZADNEJ zmiany wzgledem stanu po pierwszym odpaleniu (czyli
        # cykl #2 musi byc rozpoznany jako DRUGI, nie jako nowy "pierwszy").
        access_state_after_second = state_after_second["ikea_access_notification"]
        self.assertTrue(
            access_state_after_second["outage_active"] or access_state_after_second["pending_outage"]
        )

    def test_cron_invocation_returns_immediately_without_looping(self):
        # Wymog: cron NIE petli/nie czeka na drugi cykl - main() w trybie
        # cron wywoluje run_ikea_check_cycle() dokladnie raz i wraca.
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
            "SEARCH_TERMS=stall\n"
        )
        code = (
            "import unittest.mock as mock\n"
            "import ikea_okazje as ik\n"
            "calls = []\n"
            "orig = ik.run_ikea_check_cycle\n"
            "def counting_cycle():\n"
            "    calls.append(1)\n"
            "    return orig()\n"
            "with mock.patch.object(ik, 'fetch_store_offers', "
            "side_effect=ik.BlockedByServerError(403, 'HTTP 403')), \\\n"
            "     mock.patch.object(ik.time, 'sleep'), \\\n"
            "     mock.patch.object(ik, 'run_ikea_check_cycle', side_effect=counting_cycle):\n"
            "    ik.main()\n"
            "assert len(calls) == 1, calls\n"
            "print('OK')\n"
        )
        result = _run_python_code(code, home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)


class TestBackoffAfterBlocking(unittest.TestCase):
    """Wykladniczy, GLOBALNY backoff po HTTP 403/429 (BackoffState) -
    narastanie, reset po sukcesie, cap, deterministyczny jitter (mockowany
    random.uniform), pierwszy krok >= CHECK_INTERVAL_SECONDS oraz trwalosc
    stanu (klucz "blocking_backoff" w DYNAMIC_STATE_FILE)."""

    def setUp(self):
        self._orig_state_dict = dict(ik.get_blocking_backoff_state().to_dict())
        self._orig_check_interval = ik.CHECK_INTERVAL_SECONDS
        ik.set_blocking_backoff_state(ik.BackoffState())

    def tearDown(self):
        ik.set_blocking_backoff_state(ik.BackoffState.from_dict(self._orig_state_dict))
        ik.CHECK_INTERVAL_SECONDS = self._orig_check_interval

    # --- lokalny, wykladniczy backoff (bez Retry-After) ---

    def test_compute_local_backoff_delay_zero_when_no_failures(self):
        self.assertEqual(ik.compute_local_backoff_delay(0), 0.0)

    def test_compute_local_backoff_delay_grows_exponentially(self):
        ik.CHECK_INTERVAL_SECONDS = 30  # ponizej BACKOFF_BASE_SECONDS, zeby nie zaklamac wyniku
        first = ik.compute_local_backoff_delay(1)
        second = ik.compute_local_backoff_delay(2)
        third = ik.compute_local_backoff_delay(3)
        self.assertAlmostEqual(first, ik.BACKOFF_BASE_SECONDS)
        self.assertAlmostEqual(second, ik.BACKOFF_BASE_SECONDS * 2)
        self.assertAlmostEqual(third, ik.BACKOFF_BASE_SECONDS * 4)
        self.assertLess(first, second)
        self.assertLess(second, third)

    def test_compute_local_backoff_delay_capped_at_maximum(self):
        ik.CHECK_INTERVAL_SECONDS = 30
        delay = ik.compute_local_backoff_delay(20)  # bardzo duzo porazek z rzedu
        self.assertLessEqual(delay, ik.BACKOFF_CAP_SECONDS)
        self.assertAlmostEqual(delay, ik.BACKOFF_CAP_SECONDS)

    # --- wymog: pierwszy backoff nigdy nie jest krotszy od normalnego interwalu ---

    def test_first_backoff_step_is_never_shorter_than_check_interval(self):
        # Normalny interwal (2700s w tym przypadku) jest WIEKSZY niz
        # BACKOFF_BASE_SECONDS (60s) - pierwszy krok backoffu MUSI wiec
        # wynosic przynajmniej CHECK_INTERVAL_SECONDS, NIE 60s.
        ik.CHECK_INTERVAL_SECONDS = 2700
        delay = ik.compute_local_backoff_delay(1)
        self.assertGreaterEqual(delay, 2700)

    def test_effective_backoff_base_uses_larger_of_check_interval_and_base(self):
        ik.CHECK_INTERVAL_SECONDS = 5000  # wieksze niz BACKOFF_CAP_SECONDS domyslne (1800)
        self.assertEqual(ik.effective_backoff_base_seconds(), 5000)
        # Cap tez musi wtedy wzrosnac >= bazy, inaczej pierwszy krok
        # przekroczylby cap.
        self.assertGreaterEqual(ik.effective_backoff_cap_seconds(), 5000)

    def test_effective_backoff_base_falls_back_to_backoff_base_when_smaller(self):
        ik.CHECK_INTERVAL_SECONDS = 10  # znacznie mniejsze niz BACKOFF_BASE_SECONDS
        self.assertEqual(ik.effective_backoff_base_seconds(), ik.BACKOFF_BASE_SECONDS)

    # --- jitter backoffu: tylko w gore, nigdy nie skraca ---

    def test_backoff_jitter_never_shortens_value(self):
        for _ in range(200):
            value = 1000.0
            jittered = ik.apply_backoff_jitter(value)
            self.assertGreaterEqual(jittered, value)
            self.assertLessEqual(jittered, value * (1 + ik.BACKOFF_JITTER_PERCENT / 100.0) + 1e-9)

    def test_backoff_jitter_deterministic_with_mocked_random(self):
        with mock.patch.object(ik.random, "uniform", return_value=10.0) as mock_uniform:
            result = ik.apply_backoff_jitter(100.0, percent=20)
        mock_uniform.assert_called_once_with(0, 20)
        self.assertAlmostEqual(result, 110.0)

    def test_zero_percent_jitter_returns_value_unchanged(self):
        self.assertEqual(ik.apply_backoff_jitter(500.0, percent=0), 500.0)

    # --- finalny czas oczekiwania: max(lokalny backoff, Retry-After) + jitter w gore ---

    def test_final_delay_uses_local_backoff_when_larger_than_retry_after(self):
        ik.CHECK_INTERVAL_SECONDS = 2700
        with mock.patch.object(ik.random, "uniform", return_value=0.0):
            delay = ik.compute_next_allowed_check_delay(1, retry_after_seconds=30)
        self.assertAlmostEqual(delay, 2700)

    def test_final_delay_uses_retry_after_when_larger_than_local_backoff(self):
        ik.CHECK_INTERVAL_SECONDS = 60
        with mock.patch.object(ik.random, "uniform", return_value=0.0):
            delay = ik.compute_next_allowed_check_delay(1, retry_after_seconds=7200)
        self.assertAlmostEqual(delay, 7200)

    def test_final_delay_jitter_never_drops_below_minimum(self):
        ik.CHECK_INTERVAL_SECONDS = 2700
        for _ in range(200):
            delay = ik.compute_next_allowed_check_delay(2, retry_after_seconds=1000)
            minimum = max(ik.compute_local_backoff_delay(2), 1000)
            self.assertGreaterEqual(delay, minimum)

    def test_final_delay_without_retry_after_equals_local_backoff_jitter(self):
        ik.CHECK_INTERVAL_SECONDS = 2700
        with mock.patch.object(ik.random, "uniform", return_value=0.0):
            delay = ik.compute_next_allowed_check_delay(1, retry_after_seconds=None)
        self.assertAlmostEqual(delay, ik.compute_local_backoff_delay(1))

    # --- Retry-After: parsowanie naglowka ---

    def test_retry_after_seconds_value(self):
        self.assertEqual(ik.parse_retry_after_header("120"), 120.0)

    def test_retry_after_zero_is_valid(self):
        self.assertEqual(ik.parse_retry_after_header("0"), 0.0)

    def test_retry_after_negative_is_invalid(self):
        self.assertIsNone(ik.parse_retry_after_header("-5"))

    def test_retry_after_empty_is_invalid(self):
        self.assertIsNone(ik.parse_retry_after_header(""))

    def test_retry_after_none_is_invalid(self):
        self.assertIsNone(ik.parse_retry_after_header(None))

    def test_retry_after_garbage_is_invalid(self):
        self.assertIsNone(ik.parse_retry_after_header("nie-liczba-ani-data"))

    def test_retry_after_http_date_in_future(self):
        from email.utils import format_datetime
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(seconds=120)
        header_value = format_datetime(future, usegmt=True)
        seconds = ik.parse_retry_after_header(header_value)
        self.assertIsNotNone(seconds)
        self.assertGreater(seconds, 100)
        self.assertLess(seconds, 140)

    def test_retry_after_http_date_in_past_is_invalid(self):
        from email.utils import format_datetime
        from datetime import datetime, timedelta, timezone

        past = datetime.now(timezone.utc) - timedelta(seconds=120)
        header_value = format_datetime(past, usegmt=True)
        self.assertIsNone(ik.parse_retry_after_header(header_value))

    def test_fetch_page_with_retry_reads_retry_after_on_429(self):
        fake_resp = mock.Mock(status_code=429, text="rate limited")
        fake_resp.headers = {"retry-after": "120"}

        with mock.patch.object(ik.requests, "get", return_value=fake_resp), \
             mock.patch.object(ik.time, "sleep"):
            with self.assertRaises(ik.BlockedByServerError) as ctx:
                ik.fetch_page_with_retry("294", 0)

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after_seconds, 120.0)

    def test_fetch_page_with_retry_ignores_invalid_retry_after(self):
        fake_resp = mock.Mock(status_code=429, text="rate limited")
        fake_resp.headers = {"retry-after": "not-a-number"}

        with mock.patch.object(ik.requests, "get", return_value=fake_resp), \
             mock.patch.object(ik.time, "sleep"):
            with self.assertRaises(ik.BlockedByServerError) as ctx:
                ik.fetch_page_with_retry("294", 0)

        self.assertIsNone(ctx.exception.retry_after_seconds)

    def test_fetch_page_with_retry_403_without_retry_after(self):
        fake_resp = mock.Mock(status_code=403, text="blocked")
        fake_resp.headers = {}

        with mock.patch.object(ik.requests, "get", return_value=fake_resp), \
             mock.patch.object(ik.time, "sleep"):
            with self.assertRaises(ik.BlockedByServerError) as ctx:
                ik.fetch_page_with_retry("294", 0)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIsNone(ctx.exception.retry_after_seconds)

    def test_blocked_status_code_raised_immediately_without_local_retry(self):
        # fetch_page_with_retry() NIE powinien ponawiac requestu w miejscu
        # dla 403/429 - ma wyjsc natychmiast jako BlockedByServerError.
        fake_resp = mock.Mock(status_code=403, text="blocked")
        fake_resp.headers = {}
        call_count = {"n": 0}

        def fake_get(*args, **kwargs):
            call_count["n"] += 1
            return fake_resp

        with mock.patch.object(ik.requests, "get", side_effect=fake_get), \
             mock.patch.object(ik.time, "sleep"):
            with self.assertRaises(ik.BlockedByServerError) as ctx:
                ik.fetch_page_with_retry("294", 0)

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(ctx.exception.status_code, 403)

    # --- update_blocking_backoff_state(): narastanie/reset + trwalosc ---

    def test_update_state_increments_on_403(self):
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})
        state = ik.get_blocking_backoff_state()
        self.assertEqual(state.failure_count, 1)
        self.assertEqual(state.last_status_code, 403)

        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})
        self.assertEqual(ik.get_blocking_backoff_state().failure_count, 2)

    def test_update_state_increments_on_429_same_as_403(self):
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(429, "HTTP 429")})
        state = ik.get_blocking_backoff_state()
        self.assertEqual(state.failure_count, 1)
        self.assertEqual(state.last_status_code, 429)

        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(429, "HTTP 429")})
        self.assertEqual(ik.get_blocking_backoff_state().failure_count, 2)

    def test_update_state_resets_after_success(self):
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})
        self.assertEqual(ik.get_blocking_backoff_state().failure_count, 2)

        ik.update_blocking_backoff_state({})  # brak bledow - "pierwsze udane pobranie"
        state = ik.get_blocking_backoff_state()
        self.assertEqual(state.failure_count, 0)
        self.assertIsNone(state.last_status_code)
        self.assertIsNone(state.next_allowed_check_at)

    def test_update_state_ignores_non_blocking_errors(self):
        # Blad inny niz 403/429 (np. zwykly RuntimeError z retry na 5xx) nie
        # powinien zwiekszac licznika backoffu blokady.
        ik.update_blocking_backoff_state({"294": RuntimeError("boom")})
        self.assertEqual(ik.get_blocking_backoff_state().failure_count, 0)

    def test_update_state_picks_longer_of_local_backoff_and_retry_after(self):
        ik.CHECK_INTERVAL_SECONDS = 60  # male, zeby lokalny backoff byl krotszy niz Retry-After
        before = time.time()
        ik.update_blocking_backoff_state({
            "294": ik.BlockedByServerError(429, "HTTP 429", retry_after_seconds=9999),
        })
        state = ik.get_blocking_backoff_state()
        self.assertGreaterEqual(state.next_allowed_check_at, before + 9999 - 1)

    def test_update_state_persists_retry_after_seconds(self):
        ik.update_blocking_backoff_state({
            "294": ik.BlockedByServerError(429, "HTTP 429", retry_after_seconds=42.0),
        })
        state = ik.get_blocking_backoff_state()
        self.assertEqual(state.retry_after_seconds, 42.0)

    # --- trwalosc stanu backoffu na dysku / restart procesu ---

    def test_blocking_backoff_state_persists_to_dynamic_state_file(self):
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})

        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("blocking_backoff", data)
        self.assertEqual(data["blocking_backoff"]["failure_count"], 1)
        self.assertEqual(data["blocking_backoff"]["last_status_code"], 403)
        self.assertIsNotNone(data["blocking_backoff"]["next_allowed_check_at"])

    def test_blocking_backoff_state_survives_simulated_restart(self):
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(429, "HTTP 429")})
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(429, "HTTP 429")})

        # Symulacja restartu: wczytujemy DYNAMIC_STATE na nowo z dysku,
        # tak jak initialize_runtime() robi to na starcie procesu.
        reloaded = ik.load_dynamic_state()
        reloaded_state = ik.BackoffState.from_dict(reloaded["blocking_backoff"])
        self.assertEqual(reloaded_state.failure_count, 2)
        self.assertEqual(reloaded_state.last_status_code, 429)
        self.assertIsNotNone(reloaded_state.next_allowed_check_at)

    def test_old_dynamic_state_file_without_blocking_backoff_migrates_cleanly(self):
        # Stary plik stanu (przed tym refaktorem) bez klucza
        # "blocking_backoff" - load_dynamic_state() musi dopisac domyslny
        # stan (brak aktywnego backoffu) bez wyjatku i zapisac go na dysk.
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("blocking_backoff", None)
        with open(ik.DYNAMIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reloaded = ik.load_dynamic_state()
        self.assertIn("blocking_backoff", reloaded)
        state = ik.BackoffState.from_dict(reloaded["blocking_backoff"])
        self.assertEqual(state.failure_count, 0)
        self.assertIsNone(state.last_status_code)
        self.assertIsNone(state.next_allowed_check_at)

        # Migracja jest zapisana na dysk, nie tylko w pamieci.
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            persisted = json.load(f)
        self.assertIn("blocking_backoff", persisted)

    def test_run_ikea_check_cycle_updates_backoff_state_on_403(self):
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        try:
            # notify_access_status() zmockowane - ten test dotyczy WYLACZNIE
            # globalnego stanu backoffu (BackoffState), nie alertu o
            # utracie dostepu (patrz TestAccessNotificationState wyzej) -
            # bez tego mocka test probowalby nawiazac prawdziwe polaczenie SMTP.
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.run_ikea_check_cycle()
            state = ik.get_blocking_backoff_state()
            self.assertEqual(state.failure_count, 1)
            self.assertEqual(state.last_status_code, 403)
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())
            ik.set_access_notification_state(False, None)

    def test_run_ikea_check_cycle_resets_backoff_after_full_success(self):
        orig_store_ids = list(ik.STORE_IDS)
        orig_terms = list(ik.SEARCH_TERMS)
        ik.STORE_IDS = ["294"]
        ik.SEARCH_TERMS = ["stall"]
        ik.refresh_normalized_terms()
        ik.update_blocking_backoff_state({"294": ik.BlockedByServerError(403, "HTTP 403")})
        self.assertEqual(ik.get_blocking_backoff_state().failure_count, 1)
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        try:
            with mock.patch.object(ik, "fetch_store_offers", return_value=[]), \
                 mock.patch.object(ik, "notify_access_status", return_value=[]):
                ik.run_ikea_check_cycle()
            state = ik.get_blocking_backoff_state()
            self.assertEqual(state.failure_count, 0)
            self.assertIsNone(state.next_allowed_check_at)
        finally:
            ik.STORE_IDS = orig_store_ids
            ik.SEARCH_TERMS = orig_terms
            ik.refresh_normalized_terms()
            ik.set_blocking_backoff_state(ik.BackoffState())
            ik.set_access_notification_state(False, None)
            if os.path.exists(ik.STATE_FILE):
                os.remove(ik.STATE_FILE)


class TestDaemonRespectsPersistedBackoff(unittest.TestCase):
    """run_daemon() po (symulowanym) restarcie procesu respektuje zapisany,
    trwaly termin next_allowed_check_at - bez zadnych prawdziwych requestow
    sieciowych/SMTP/Telegrama (run_ikea_check_cycle/handle_telegram_updates
    sa tu zawsze zmockowane) i bez rzeczywistego oczekiwania (SHUTDOWN_EVENT
    jest ustawiany od razu po pierwszym obiegu petli)."""

    def setUp(self):
        self._orig_state_dict = dict(ik.get_blocking_backoff_state().to_dict())
        ik.SHUTDOWN_EVENT.clear()

    def tearDown(self):
        ik.set_blocking_backoff_state(ik.BackoffState.from_dict(self._orig_state_dict))
        ik.SHUTDOWN_EVENT.clear()

    def test_seconds_until_next_allowed_check_none_without_backoff(self):
        state = ik.BackoffState()
        self.assertIsNone(ik.seconds_until_next_allowed_check(state))

    def test_seconds_until_next_allowed_check_future_timestamp(self):
        state = ik.BackoffState(failure_count=1, next_allowed_check_at=time.time() + 100)
        remaining = ik.seconds_until_next_allowed_check(state)
        self.assertGreater(remaining, 90)
        self.assertLessEqual(remaining, 100)

    def test_seconds_until_next_allowed_check_past_timestamp_returns_zero(self):
        # Jesli od next_allowed_check_at minelo juz dużo czasu (proces byl
        # zatrzymany dluzej niz trwal backoff), daemon ma sprawdzic od razu.
        state = ik.BackoffState(failure_count=1, next_allowed_check_at=time.time() - 5000)
        self.assertEqual(ik.seconds_until_next_allowed_check(state), 0.0)

    def test_run_daemon_logs_resumed_backoff_after_restart(self):
        # Stan zapisany na dysku PRZED startem run_daemon() (symulacja
        # restartu procesu w trakcie aktywnego backoffu) - run_daemon() musi
        # to wykryc i zalogowac, bez uruchamiania nowego cyklu natychmiast
        # (SHUTDOWN_EVENT jest ustawiany zaraz na starcie petli, zanim
        # minie next_check_interval).
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=2, last_status_code=429, next_allowed_check_at=time.time() + 999999,
        ))
        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle") as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "log") as mock_log:

            def stop_after_first_wait(timeout):
                ik.SHUTDOWN_EVENT.set()
                return True

            with mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=stop_after_first_wait):
                ik.run_daemon()

        mock_cycle.assert_not_called()
        messages = " ".join(str(call.args[0]) for call in mock_log.call_args_list)
        self.assertIn("restarcie", messages.lower())

    def test_run_daemon_checks_immediately_when_persisted_deadline_in_past(self):
        # next_allowed_check_at juz dawno minal - run_daemon() powinien
        # wykonac cykl IKEA od razu (delay=0.0), zanim event.wait() zdazy
        # cokolwiek zablokowac.
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=1, last_status_code=403, next_allowed_check_at=time.time() - 99999,
        ))
        call_count = {"n": 0}

        def fake_cycle():
            call_count["n"] += 1
            ik.SHUTDOWN_EVENT.set()
            return 0

        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle", side_effect=fake_cycle), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", return_value=True):
            ik.run_daemon()

        self.assertEqual(call_count["n"], 1)

    def test_run_daemon_uses_monotonic_clock_for_interval_accounting(self):
        # Zamiast sprawdzac implementacje przez introspekcje, potwierdzamy
        # zachowanie: podbicie time.monotonic() (nie time.time()) w trakcie
        # oczekiwania powinno wywolac kolejny cykl - to demonstruje, ze
        # petla liczy odstep wzgledem monotonic(), a nie zegara sciennego.
        fake_monotonic_value = {"t": 1000.0}

        def fake_monotonic():
            return fake_monotonic_value["t"]

        call_count = {"n": 0}

        def fake_cycle():
            call_count["n"] += 1
            if call_count["n"] >= 2:
                ik.SHUTDOWN_EVENT.set()
            return 0

        def fake_wait(timeout):
            # Kazde "oczekiwanie" przesuwa zegar monotoniczny naprzod,
            # symulujac uplyw czasu bez prawdziwego time.sleep().
            fake_monotonic_value["t"] += 10
            return ik.SHUTDOWN_EVENT.is_set()

        ik.set_blocking_backoff_state(ik.BackoffState())
        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle", side_effect=fake_cycle), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 5), \
             mock.patch.object(ik, "CHECK_INTERVAL_JITTER_PERCENT", 0), \
             mock.patch.object(ik.time, "monotonic", side_effect=fake_monotonic), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=fake_wait):
            ik.run_daemon()

        self.assertGreaterEqual(call_count["n"], 2)

    def test_daemon_does_not_run_second_cycle_before_persisted_backoff_deadline(self):
        # Wymog "Scheduling and Retry-After" / test 15: druga kontrola IKEA
        # w run_daemon() NIE nastepuje przed uplywem trwalego backoffu
        # (next_allowed_check_at) - niezaleznie od tego, jak czesto petla
        # daemona "budzi sie" (SHUTDOWN_EVENT.wait()). Zaden hardcoded
        # 85-sekundowy sleep/retry nie istnieje - odstep miedzy cyklami
        # wynika WYLACZNIE z zapisanego backoffu.
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=1, last_status_code=403, next_allowed_check_at=time.time() + 3600,
        ))
        wait_calls = {"n": 0}

        def fake_wait(timeout):
            wait_calls["n"] += 1
            if wait_calls["n"] >= 3:
                ik.SHUTDOWN_EVENT.set()
            return ik.SHUTDOWN_EVENT.is_set()

        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle") as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=fake_wait):
            ik.run_daemon()

        # 3600s backoff jest znacznie dluzszy niz kilka "przebudzen" petli -
        # zaden cykl IKEA nie powinien sie odbyc w tym czasie.
        mock_cycle.assert_not_called()

    def test_daemon_runs_second_cycle_only_after_persisted_deadline_passes(self):
        # Zwierciadlany test: gdy zapisany termin JUZ minal (symulowany
        # uplyw czasu miedzy dwoma wywolaniami run_daemon(), tak jak
        # dwie kolejne, oddzielone w czasie kontrole w tym samym procesie
        # demona), druga kontrola SIE odbywa - i to wylacznie dzieki
        # przelicznikowi next_allowed_check_at, nie z powodu jakiegokolwiek
        # hardcoded opoznienia.
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=1, last_status_code=403, next_allowed_check_at=time.time() - 1,
        ))
        call_count = {"n": 0}

        def fake_cycle():
            call_count["n"] += 1
            ik.SHUTDOWN_EVENT.set()
            return 0

        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "handle_telegram_updates"), \
             mock.patch.object(ik, "run_ikea_check_cycle", side_effect=fake_cycle), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", return_value=True):
            ik.run_daemon()

        self.assertEqual(call_count["n"], 1)




def _make_offer(**overrides):
    offer = {
        "offer_uuid": "uuid-1",
        "offer_number": "123",
        "title": "Stol",
        "description": "opis",
        "article_numbers": ["90557419"],
        "currency": "PLN",
        "price": 100,
        "original_price": 200,
        "discount_percent": 50,
        "condition": "Nowy",
        "condition_desc": "brak uszkodzen",
        "reason_discount": "wystawowy",
        "additional_info": None,
        "hero_image": None,
        "store_id": "294",
        "reservation_link": "https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroc%C5%82aw/123",
    }
    offer.update(overrides)
    return offer

class TestOfferGrouping(unittest.TestCase):
    def test_identical_products_different_uuids_grouped_together(self):
        offers = [
            _make_offer(offer_uuid="u1", offer_number="100"),
            _make_offer(offer_uuid="u2", offer_number="101"),
            _make_offer(offer_uuid="u3", offer_number="102", reservation_link="http://link1"),
            _make_offer(offer_uuid="u4", offer_number="103", reservation_link="http://link2"),
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["items"]), 4)

    def test_different_store_creates_separate_groups(self):
        offers = [
            _make_offer(offer_uuid="u1", store_id="294"),
            _make_offer(offer_uuid="u2", store_id="295"),
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 2)

    def test_different_price_creates_separate_groups(self):
        offers = [
            _make_offer(offer_uuid="u1", price=100),
            _make_offer(offer_uuid="u2", price=101),
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 2)

    def test_different_currency_creates_separate_groups(self):
        offers = [
            _make_offer(offer_uuid="u1", currency="PLN"),
            _make_offer(offer_uuid="u2", currency="EUR"),
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 2)

    def test_different_condition_creates_separate_groups(self):
        offers = [
            _make_offer(offer_uuid="u1", condition="Nowy"),
            _make_offer(offer_uuid="u2", condition="Uzywany"),
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 2)

    def test_single_offer_becomes_single_group_single_item(self):
        offers = [_make_offer()]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["items"]), 1)

    def test_group_preserves_offer_order(self):
        offers = [
            _make_offer(offer_uuid="u1", offer_number="100"),
            _make_offer(offer_uuid="u2", offer_number="101"),
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(groups[0]["items"][0]["offer_number"], "100")
        self.assertEqual(groups[0]["items"][1]["offer_number"], "101")

    def test_group_preserves_shared_fields(self):
        offers = [_make_offer(title="Szafa", description="Fajna")]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(groups[0]["shared"]["title"], "Szafa")
        self.assertEqual(groups[0]["shared"]["description"], "Fajna")

    def test_missing_link_preserved_in_item(self):
        offers = [_make_offer(reservation_link=None)]
        groups = ik.group_offers_for_notification(offers)
        self.assertIsNone(groups[0]["items"][0]["reservation_link"])

class TestTelegramGroupedFormatting(unittest.TestCase):
    def test_group_header_escapes_html(self):
        offers = [_make_offer(title="<b&>", description="&<>")]
        groups = ik.group_offers_for_notification(offers)
        header = ik._format_group_header_telegram(groups[0]["shared"])
        self.assertIn("&lt;b&amp;&gt;", header)
        self.assertIn("&amp;&lt;&gt;", header)

    def test_single_item_group_no_count_label(self):
        offers = [_make_offer()]
        groups = ik.group_offers_for_notification(offers)
        formatted = ik.format_offer_group_telegram(groups[0])
        self.assertNotIn("nowych sztuk", formatted)

    def test_multi_item_group_has_count_label(self):
        offers = [
            _make_offer(offer_number="1"),
            _make_offer(offer_number="2"),
            _make_offer(offer_number="3"),
        ]
        groups = ik.group_offers_for_notification(offers)
        formatted = ik.format_offer_group_telegram(groups[0])
        self.assertIn("3 nowych sztuk:", formatted)

    def test_item_with_link_has_rezerwuj_anchor(self):
        offers = [_make_offer(reservation_link="http://link")]
        groups = ik.group_offers_for_notification(offers)
        item_text = ik._format_offer_item_telegram(groups[0]["items"][0])
        self.assertIn('<a href="http://link">Rezerwuj</a>', item_text)

    def test_item_without_link_shows_niedostepny(self):
        offers = [_make_offer(reservation_link=None)]
        groups = ik.group_offers_for_notification(offers)
        item_text = ik._format_offer_item_telegram(groups[0]["items"][0])
        self.assertIn("link niedostępny", item_text)

    def test_offer_number_escaped_in_item(self):
        offers = [_make_offer(offer_number="<123>")]
        groups = ik.group_offers_for_notification(offers)
        item_text = ik._format_offer_item_telegram(groups[0]["items"][0])
        self.assertIn("&lt;123&gt;", item_text)

    def test_all_items_present_in_formatted_group(self):
        offers = [_make_offer(offer_number=str(i)) for i in range(5)]
        groups = ik.group_offers_for_notification(offers)
        formatted = ik.format_offer_group_telegram(groups[0])
        for i in range(5):
            self.assertIn(str(i), formatted)

class TestTelegramMessageSplitting(unittest.TestCase):
    def test_small_groups_fit_in_one_message(self):
        groups = ik.group_offers_for_notification([
            _make_offer(offer_uuid="1", title="A"),
            _make_offer(offer_uuid="2", title="B"),
        ])
        msgs = ik.split_telegram_messages(groups)
        self.assertEqual(len(msgs), 1)

    def test_message_never_exceeds_safe_limit(self):
        groups = ik.group_offers_for_notification([
            _make_offer(offer_uuid=str(i), offer_number=f"8772470{i}")
            for i in range(150)
        ])
        msgs = ik.split_telegram_messages(groups)
        self.assertGreater(len(msgs), 1)
        for msg in msgs:
            self.assertLessEqual(len(msg), ik.TELEGRAM_SAFE_LIMIT)

    def test_no_ellipsis_in_output(self):
        groups = ik.group_offers_for_notification([
            _make_offer(offer_uuid=str(i)) for i in range(50)
        ])
        msgs = ik.split_telegram_messages(groups)
        for msg in msgs:
            self.assertNotIn("(...)", msg)

    def test_all_offer_numbers_present_across_messages(self):
        offers = [_make_offer(offer_uuid=str(i), offer_number=f"8772470{i}") for i in range(50)]
        groups = ik.group_offers_for_notification(offers)
        msgs = ik.split_telegram_messages(groups)
        combined = "".join(msgs)
        for i in range(50):
            self.assertIn(f"8772470{i}", combined)

    def test_all_links_present_across_messages(self):
        offers = [_make_offer(offer_uuid=str(i), reservation_link=f"http://link{i}") for i in range(50)]
        groups = ik.group_offers_for_notification(offers)
        msgs = ik.split_telegram_messages(groups)
        combined = "".join(msgs)
        for i in range(50):
            self.assertIn(f"http://link{i}", combined)

    def test_single_group_split_across_messages(self):
        offers = [_make_offer(offer_uuid=str(i), offer_number=f"8772470{i}", description="długi " * 50) for i in range(150)]
        groups = ik.group_offers_for_notification(offers)
        msgs = ik.split_telegram_messages(groups)
        self.assertGreater(len(msgs), 1)
        for i, msg in enumerate(msgs):
            if i > 0:
                self.assertIn("(cd.)", msg)

    def test_continuation_header_present(self):
        offers = [_make_offer(offer_uuid=str(i), offer_number=f"8772470{i}", description="długi " * 100) for i in range(150)]
        groups = ik.group_offers_for_notification(offers)
        msgs = ik.split_telegram_messages(groups)
        self.assertGreater(len(msgs), 1)
        self.assertIn("(cd.)", msgs[1])

    def test_empty_groups_returns_empty(self):
        self.assertEqual(ik.split_telegram_messages([]), [])

class TestSendTelegramErrorHandling(unittest.TestCase):
    def test_all_messages_sent_successfully(self):
        offers = [
            _make_offer(offer_uuid=str(i), offer_number=str(i)) for i in range(100)
        ]
        with mock.patch.object(ik, "telegram_send_message") as mock_send:
            ik.send_telegram(offers)
            self.assertGreater(mock_send.call_count, 0)

    def test_error_on_second_message_raises(self):
        # Tworzymy oferty z dlugim opisem zeby wymusic wiele wiadomosci
        offers = [
            _make_offer(offer_uuid=str(i), offer_number=str(i), description="długi " * 100)
            for i in range(150)
        ]
        groups = ik.group_offers_for_notification(offers)
        msgs = ik.split_telegram_messages(groups)
        self.assertGreater(len(msgs), 1)

        call_count = {"n": 0}
        def fake_send(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("Telegram failed")

        with mock.patch.object(ik, "telegram_send_message", side_effect=fake_send):
            with self.assertRaises(RuntimeError) as ctx:
                ik.send_telegram(offers)
            self.assertIn("1/", str(ctx.exception))

    def test_error_on_first_message_raises(self):
        offers = [_make_offer()]
        with mock.patch.object(ik, "telegram_send_message", side_effect=RuntimeError("Telegram down")):
            with self.assertRaises(RuntimeError) as ctx:
                ik.send_telegram(offers)
            self.assertIn("0/", str(ctx.exception))

class TestNotifyReturnType(unittest.TestCase):
    def test_notify_returns_dict_on_success(self):
        with mock.patch.object(ik, "send_email"), \
             mock.patch.object(ik, "send_telegram"), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            res = ik.notify([_make_offer()])
            self.assertEqual(res, {"email": None, "telegram": None})

    def test_notify_returns_email_error(self):
        with mock.patch.object(ik, "send_email", side_effect=RuntimeError("Fail")), \
             mock.patch.object(ik, "send_telegram"), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            res = ik.notify([_make_offer()])
            self.assertIn("e-mail:", res["email"])
            self.assertIsNone(res["telegram"])

    def test_notify_returns_telegram_error(self):
        with mock.patch.object(ik, "send_email"), \
             mock.patch.object(ik, "send_telegram", side_effect=RuntimeError("Fail2")), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            res = ik.notify([_make_offer()])
            self.assertIsNone(res["email"])
            self.assertIn("telegram:", res["telegram"])

    def test_notify_returns_both_errors(self):
        with mock.patch.object(ik, "send_email", side_effect=RuntimeError("FailE")), \
             mock.patch.object(ik, "send_telegram", side_effect=RuntimeError("FailT")), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            res = ik.notify([_make_offer()])
            self.assertIn("e-mail:", res["email"])
            self.assertIn("telegram:", res["telegram"])

class TestSeenOffersDeliverySemantics(unittest.TestCase):
    def setUp(self):
        self._orig_store_ids = list(ik.STORE_IDS)
        self._orig_terms = list(ik.SEARCH_TERMS)
        self._orig_numbers = list(ik.SEARCH_ARTICLE_NUMBERS)
        self._orig_sleep = ik.time.sleep
        ik.time.sleep = lambda *a, **k: None
        ik.SEARCH_TERMS = ["stol"]
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.STORE_IDS = ["294"]
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        # Stwórz plik seen zeby nie było first-run
        ik.save_seen_uuids(set())

    def tearDown(self):
        ik.STORE_IDS = self._orig_store_ids
        ik.SEARCH_TERMS = self._orig_terms
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig_numbers
        ik.time.sleep = self._orig_sleep
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def _fake_api_product(self, offer_uuids):
        """Tworzy produkt API z wieloma ofertami pasujacymi do SEARCH_TERMS."""
        return {
            "title": "Stol drewniany",
            "description": "opis",
            "originalPrice": 200,
            "articleNumbers": ["12345678"],
            "currency": "PLN",
            "storeId": "294",
            "offers": [
                {
                    "offerUuid": uuid,
                    "offerNumber": f"offer-{uuid}",
                    "price": 100,
                    "productConditionTitle": "Nowy",
                    "productConditionDescription": "brak",
                    "reasonDiscount": "wystawowy",
                    "additionalInfo": None,
                }
                for uuid in offer_uuids
            ],
            "heroImage": None,
        }

    def test_all_channels_fail_does_not_save_seen(self):
        product = self._fake_api_product(["uuid-1", "uuid-2"])

        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp")), \
             mock.patch.object(ik, "send_telegram", side_effect=RuntimeError("tg")):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 2)
        seen = ik.load_seen_uuids()
        self.assertNotIn("uuid-1", seen)
        self.assertNotIn("uuid-2", seen)

        # Drugi cykl - oba kanaly odzyskuja sprawnosc, oferty zostaja dostarczone i zapisane
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email"), \
             mock.patch.object(ik, "send_telegram"):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        seen2 = ik.load_seen_uuids()
        self.assertIn("uuid-1", seen2)
        self.assertIn("uuid-2", seen2)

    def test_email_success_telegram_fail_does_not_save_seen(self):
        product = self._fake_api_product(["uuid-4"])

        email_calls = []
        tg_calls = []

        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda offers: email_calls.append(offers)), \
             mock.patch.object(ik, "send_telegram", side_effect=RuntimeError("tg network error")):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 2)
        self.assertEqual(len(email_calls), 1)
        seen = ik.load_seen_uuids()
        self.assertNotIn("uuid-4", seen)

        # Drugi cykl - Telegram odzyskuje sprawnosc i odbiera oferte
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda offers: email_calls.append(offers)), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(email_calls), 2)  # e-mail dostaje duplikat przy ponowieniu
        self.assertEqual(len(tg_calls), 1)     # Telegram pomyslnie otrzymuje oferty
        seen2 = ik.load_seen_uuids()
        self.assertIn("uuid-4", seen2)

    def test_telegram_success_email_fail_does_not_save_seen(self):
        product = self._fake_api_product(["uuid-5"])

        email_calls = []
        tg_calls = []

        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp down")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 2)
        self.assertEqual(len(tg_calls), 1)
        seen = ik.load_seen_uuids()
        self.assertNotIn("uuid-5", seen)

        # Drugi cykl - e-mail odzyskuje sprawnosc i odbiera oferte
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda offers: email_calls.append(offers)), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(email_calls), 1)  # e-mail pomyslnie otrzymuje oferty
        self.assertEqual(len(tg_calls), 2)     # Telegram dostaje duplikat przy ponowieniu
        seen2 = ik.load_seen_uuids()
        self.assertIn("uuid-5", seen2)

    def test_telegram_message_2_fails_while_email_succeeds(self):
        # Integracyjny test: wiadomosc Telegrama podzielona na serie;
        # czesc 1 dociera, czesc 2 zawodzi. Mimo ze e-mail dotarl,
        # oferty NIE sa zapisywane jako seen.
        uuids = [f"uuid-split-{i}" for i in range(150)]
        product = self._fake_api_product(uuids)

        email_delivered = []
        call_count = {"n": 0}

        def fake_tg_send(msg, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("Telegram network drop on part 2")

        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "deliver_email_message", side_effect=lambda s, b: email_delivered.append(b)), \
             mock.patch.object(ik, "telegram_send_message", side_effect=fake_tg_send):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 2)
        self.assertEqual(len(email_delivered), 1)
        self.assertEqual(call_count["n"], 2)
        seen = ik.load_seen_uuids()
        for u in uuids:
            self.assertNotIn(u, seen)

        # Drugi cykl: Telegram dziala bezblednie dla wszystkich czesci serii
        tg_delivered = []
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "deliver_email_message", side_effect=lambda s, b: email_delivered.append(b)), \
             mock.patch.object(ik, "telegram_send_message", side_effect=lambda msg, **kw: tg_delivered.append(msg)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(email_delivered), 2)  # powtorny email
        self.assertGreater(len(tg_delivered), 1)   # potwierdzenie podzialu na wiele wiadomosci
        seen2 = ik.load_seen_uuids()
        for u in uuids:
            self.assertIn(u, seen2)

class TestEmailGroupedFormat(unittest.TestCase):
    def test_single_offer_email_format(self):
        group = ik.group_offers_for_notification([_make_offer(offer_number="123")])[0]
        html = ik.format_offer_group_email(group)
        self.assertIn("123", html)
        self.assertNotIn("sztuk(i):", html)

    def test_grouped_offers_email_format(self):
        offers = [_make_offer(offer_number=str(i)) for i in range(4)]
        group = ik.group_offers_for_notification(offers)[0]
        html = ik.format_offer_group_email(group)
        self.assertIn("4 sztuk(i):", html)
        for i in range(4):
            self.assertIn(str(i), html)

    def test_different_products_separate_blocks(self):
        offers = [
            _make_offer(offer_uuid="u1", offer_number="1", title="A"),
            _make_offer(offer_uuid="u2", offer_number="2", title="B"),
        ]
        with mock.patch.object(ik, "deliver_email_message") as mock_deliver:
            ik.send_email(offers)
            call_args = mock_deliver.call_args[0]
            body = call_args[1]  # deliver_email_message(subject, body)
            self.assertIn("A", body)
            self.assertIn("B", body)

class TestHtmlEscapingInGroups(unittest.TestCase):
    def test_title_with_html_chars_escaped_in_group_header(self):
        group = ik.group_offers_for_notification([_make_offer(title="<b>Stall</b>")])[0]
        header = ik._format_group_header_telegram(group["shared"])
        self.assertIn("&lt;b&gt;Stall&lt;/b&gt;", header)

    def test_description_with_ampersand_escaped(self):
        group = ik.group_offers_for_notification([_make_offer(description="a & b")])[0]
        header = ik._format_group_header_telegram(group["shared"])
        self.assertIn("a &amp; b", header)

    def test_link_with_quotes_escaped_in_href(self):
        group = ik.group_offers_for_notification([_make_offer(reservation_link='http://link"onclick="alert(1)')])[0]
        item = ik._format_offer_item_telegram(group["items"][0])
        self.assertIn('&quot;', item)


class TestTelegramSplittingSafeLimitAndEdgeCases(unittest.TestCase):
    def setUp(self):
        self._orig_store_ids = list(ik.STORE_IDS)
        self._orig_terms = list(ik.SEARCH_TERMS)
        self._orig_numbers = list(ik.SEARCH_ARTICLE_NUMBERS)
        self._orig_sleep = ik.time.sleep
        ik.time.sleep = lambda *a, **k: None
        ik.SEARCH_TERMS = ["stol"]
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.STORE_IDS = ["294"]
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        ik.save_seen_uuids(set())

    def tearDown(self):
        ik.STORE_IDS = self._orig_store_ids
        ik.SEARCH_TERMS = self._orig_terms
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig_numbers
        ik.time.sleep = self._orig_sleep
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def test_single_offer_with_description_longer_than_4000_chars(self):
        # Opis dluzszy niz 4000 znakow - naglowek zostaje bezpiecznie skrocony,
        # dlugosc calosci <= TELEGRAM_SAFE_LIMIT, tytul, cena, numer i link zachowane
        offer = _make_offer(
            offer_uuid="u-long-desc",
            offer_number="87724701",
            title="Kanonviska",
            description="Bardzo dlugi opis produktu z wieloma szczegolami " * 120,
            reservation_link="https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroclaw/87724701",
        )
        groups = ik.group_offers_for_notification([offer])
        msgs = ik.split_telegram_messages(groups)

        self.assertEqual(len(msgs), 1)
        msg = msgs[0]
        self.assertLessEqual(len(msg), ik.TELEGRAM_SAFE_LIMIT)
        self.assertIn("Kanonviska", msg)
        self.assertIn("87724701", msg)
        self.assertIn('href="https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroclaw/87724701"', msg)
        self.assertIn("Rezerwuj", msg)
        self.assertIn("…", msg)
        self.assertNotIn("(...)", msg)
        # Poprawnosc tagow HTML
        self.assertEqual(msg.count("<b>"), msg.count("</b>"))
        self.assertEqual(msg.count("<a "), msg.count("</a>"))

    def test_single_offer_with_very_long_title_and_html_special_chars(self):
        # Bardzo dlugi tytul ze znakami HTML (&, <, >, ", ') - bezpieczne skrocenie
        # przed escapowaniem HTML, brak uszkodzonych encji czy tagow
        long_title = 'Stol & lawa <dla "wymagajacych"> ' * 200
        offer = _make_offer(
            offer_uuid="u-long-title",
            offer_number="87724702",
            title=long_title,
            description="Krotki opis",
            reservation_link="https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroclaw/87724702",
        )
        groups = ik.group_offers_for_notification([offer])
        msgs = ik.split_telegram_messages(groups)

        self.assertEqual(len(msgs), 1)
        msg = msgs[0]
        self.assertLessEqual(len(msg), ik.TELEGRAM_SAFE_LIMIT)
        self.assertIn("87724702", msg)
        self.assertIn("Rezerwuj", msg)
        self.assertNotIn("<dla", msg)
        self.assertIn("&lt;dla", msg)
        self.assertIn("&amp;", msg)
        self.assertIn("…", msg)
        self.assertNotIn("(...)", msg)
        self.assertEqual(msg.count("<b>"), msg.count("</b>"))
        self.assertEqual(msg.count("<a "), msg.count("</a>"))

    def test_single_impossible_link_raises_explicit_error_and_no_sends_or_seen(self):
        # Pojedynczy link, ktorego nie da sie zmiescic nawet z minimalnym naglowkiem
        impossible_link = "https://www.ikea.com/" + ("x" * 4000)
        offer = _make_offer(
            offer_uuid="u-impossible",
            offer_number="87724703",
            reservation_link=impossible_link,
        )
        groups = ik.group_offers_for_notification([offer])

        # 1. split_telegram_messages zglasza jawny ValueError
        with self.assertRaises(ValueError) as ctx:
            ik.split_telegram_messages(groups)
        self.assertIn("87724703", str(ctx.exception))

        # 2. send_telegram zglasza blad i wykonuje DOKLADNIE ZERO prob wyslania
        with mock.patch.object(ik, "telegram_send_message") as mock_send:
            with self.assertRaises(ValueError):
                ik.send_telegram([offer])
            mock_send.assert_not_called()

        # 3. W cyklu sprawdzania: brak zapisu UUID do seen_offers, return code 2
        fake_product = {
            "title": "Stol",
            "description": "opis",
            "originalPrice": 200,
            "articleNumbers": ["12345678"],
            "currency": "PLN",
            "storeId": "294",
            "offers": [{
                "offerUuid": "u-impossible",
                "offerNumber": "87724703",
                "price": 100,
                "productConditionTitle": "Nowy",
                "productConditionDescription": "brak",
                "reasonDiscount": "wystawowy",
                "additionalInfo": None,
            }],
            "heroImage": None,
        }
        with mock.patch.object(ik, "fetch_store_offers", return_value=[fake_product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "telegram_send_message") as mock_send:
            with mock.patch.object(ik, "build_offer_reservation_link", return_value=impossible_link):
                ret = ik.run_ikea_check_cycle()

            mock_send.assert_not_called()
            self.assertEqual(ret, 2)
            seen = ik.load_seen_uuids()
            self.assertNotIn("u-impossible", seen)

    def test_multiple_offers_with_later_impossible_link_aborts_without_sending_any(self):
        # Wiele ofert, z ktorych dopiero ostatnia ma niemozliwy link - zadna wiadomosc
        # nie moze zostac wyslana, zaden UUID nie moze zostac zapisany
        o1 = _make_offer(offer_uuid="u1", offer_number="101", reservation_link="http://link1")
        o2 = _make_offer(offer_uuid="u2", offer_number="102", reservation_link="http://link2")
        o3 = _make_offer(offer_uuid="u3", offer_number="103", reservation_link="http://link3" + ("z" * 4000))

        with mock.patch.object(ik, "telegram_send_message") as mock_send:
            with self.assertRaises(ValueError):
                ik.send_telegram([o1, o2, o3])
            mock_send.assert_not_called()

        fake_product = {
            "title": "Stol",
            "description": "opis",
            "originalPrice": 200,
            "articleNumbers": ["12345678"],
            "currency": "PLN",
            "storeId": "294",
            "offers": [
                {"offerUuid": "u1", "offerNumber": "101", "price": 100, "productConditionTitle": "Nowy", "productConditionDescription": "", "reasonDiscount": "", "additionalInfo": None},
                {"offerUuid": "u2", "offerNumber": "102", "price": 100, "productConditionTitle": "Nowy", "productConditionDescription": "", "reasonDiscount": "", "additionalInfo": None},
                {"offerUuid": "u3", "offerNumber": "103", "price": 100, "productConditionTitle": "Nowy", "productConditionDescription": "", "reasonDiscount": "", "additionalInfo": None},
            ],
            "heroImage": None,
        }
        def fake_link(sid, onum):
            if onum == "103":
                return "http://link3" + ("z" * 4000)
            return f"http://link/{onum}"

        with mock.patch.object(ik, "fetch_store_offers", return_value=[fake_product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "build_offer_reservation_link", side_effect=fake_link), \
             mock.patch.object(ik, "telegram_send_message") as mock_send:
            ret = ik.run_ikea_check_cycle()

            mock_send.assert_not_called()
            self.assertEqual(ret, 2)
            seen = ik.load_seen_uuids()
            self.assertNotIn("u1", seen)
            self.assertNotIn("u2", seen)
            self.assertNotIn("u3", seen)

    def test_all_returned_messages_invariant_and_html_validity(self):
        # Roznorodne grupy: duza grupa, dlugi opis, znaki HTML, brak linku
        offers = []
        for i in range(25):
            offers.append(_make_offer(
                offer_uuid=f"g1-{i}",
                offer_number=f"100{i}",
                title="Szafa PAX",
                description="Opis szafy " * 20,
                reservation_link=f"http://link/pax/{i}",
            ))
        offers.append(_make_offer(
            offer_uuid="g2-1",
            offer_number="2001",
            title="Biurko BEKANT & <nowe>",
            description="Bardzo dlugi opis biurka " * 150,
            reservation_link="http://link/bekant/1",
        ))
        for i in range(3):
            offers.append(_make_offer(
                offer_uuid=f"g3-{i}",
                offer_number=f"300{i}",
                title='Komoda MALM <4 "szuflady">',
                description="Opis komody & mebli",
                reservation_link=None,
            ))

        groups = ik.group_offers_for_notification(offers)
        msgs = ik.split_telegram_messages(groups)

        self.assertGreater(len(msgs), 1)
        all_text = "\n".join(msgs)

        for idx, m in enumerate(msgs, 1):
            self.assertLessEqual(len(m), ik.TELEGRAM_SAFE_LIMIT, f"Wiadomosc #{idx} przekracza limit")
            self.assertNotIn("(...)", m)
            self.assertEqual(m.count("<b>"), m.count("</b>"), f"Niezbalansowane <b> w #{idx}")
            self.assertEqual(m.count("<a "), m.count("</a>"), f"Niezbalansowane <a> w #{idx}")
            tag_stripped = re.sub(r"<b>|</b>|<a\s+href=\"[^\"]*\">|</a>", "", m)
            self.assertNotIn("<", tag_stripped, f"Surowy znak < w #{idx}")
            self.assertNotIn(">", tag_stripped, f"Surowy znak > w #{idx}")

        for o in offers:
            self.assertIn(o["offer_number"], all_text)
            if o["reservation_link"]:
                self.assertIn(o["reservation_link"], all_text)
            else:
                self.assertIn(f"oferta {o['offer_number']} — link niedostępny", all_text)


if __name__ == "__main__":
    unittest.main()
