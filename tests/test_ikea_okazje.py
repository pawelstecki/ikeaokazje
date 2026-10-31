"""Testy jednostkowe dla ikea_okazje.py - bez polaczenia z IKEA/Telegramem.

Uruchomienie: python3 -m unittest tests/test_ikea_okazje.py
"""

import io
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
    # HEALTH_ALERT_AFTER_CYCLES=0: licznik nieudanych cykli w bloku
    # 'health' narasta miedzy testami (wspolny DYNAMIC_STATE), a alert o
    # awarii zaburzylby liczniki wywolan kanalow w innych testach. Testy
    # alertu (TestHealthAlert) ustawiaja prog jawnie.
    _f.write(
        "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
        "HEALTH_ALERT_AFTER_CYCLES=0\n"
    )
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


def _load_seen() -> set:
    """Zbior UUID z klucza 'seen' w STATE_FILE."""
    return ik.load_seen_state()["seen"]


def _save_seen(uuids) -> None:
    """Zapisuje klucz 'seen' w STATE_FILE, zachowujac istniejacy klucz
    'pending' (nieczytelny/uszkodzony plik traktowany jak brak pending)."""
    try:
        pending = ik.load_seen_state()["pending"]
    except (OSError, json.JSONDecodeError):
        pending = {}
    ik.save_seen_state(set(uuids), pending)


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


class TestCronRespectsPersistedBackoff(unittest.TestCase):
    """Tryb cron: aktywny trwaly backoff po 403/429 konczy przebieg kodem 0
    bez cyklu (bez zapytania do IKEA); komendy Telegrama dzialaja dalej."""

    def setUp(self):
        self._orig_state_dict = dict(ik.get_blocking_backoff_state().to_dict())
        ik.set_blocking_backoff_state(ik.BackoffState())

    def tearDown(self):
        ik.set_blocking_backoff_state(ik.BackoffState.from_dict(self._orig_state_dict))

    def _set_backoff(self, deadline_offset_seconds):
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=2,
            last_status_code=429,
            next_allowed_check_at=time.time() + deadline_offset_seconds,
        ))

    def test_blocking_backoff_active_none_without_backoff(self):
        self.assertIsNone(ik.blocking_backoff_active())

    def test_blocking_backoff_active_none_when_deadline_passed(self):
        self._set_backoff(-10)
        self.assertIsNone(ik.blocking_backoff_active())

    def test_blocking_backoff_active_returns_remaining_seconds(self):
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=1, last_status_code=403, next_allowed_check_at=1000.0,
        ))
        self.assertAlmostEqual(ik.blocking_backoff_active(now_wall_clock=400.0), 600.0)
        self.assertIsNone(ik.blocking_backoff_active(now_wall_clock=1000.0))

    def test_cron_skips_cycle_during_active_backoff(self):
        self._set_backoff(1200)
        with mock.patch.object(ik, "initialize_runtime"), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "run_ikea_check_cycle") as mock_cycle, \
             mock.patch.object(ik, "fetch_store_offers") as mock_fetch, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "RUN_MODE", "cron"), \
             mock.patch.object(ik, "log") as mock_log:
            result = ik.main()

        self.assertEqual(result, 0)
        mock_cycle.assert_not_called()
        mock_fetch.assert_not_called()
        message = " ".join(str(call.args[0]) for call in mock_log.call_args_list)
        self.assertIn("#2", message)
        self.assertIn("HTTP 429", message)
        deadline = ik.get_blocking_backoff_state().next_allowed_check_at
        self.assertIn(
            ik.datetime.fromtimestamp(deadline).strftime("%Y-%m-%d %H:%M:%S"), message
        )
        self.assertRegex(message, r"jeszcze 1(19\d|200)s")

    def test_cron_handles_telegram_commands_before_backoff_skip(self):
        self._set_backoff(1200)
        calls = []
        with mock.patch.object(ik, "initialize_runtime"), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "handle_telegram_updates", side_effect=lambda: calls.append("telegram")), \
             mock.patch.object(ik, "run_ikea_check_cycle", side_effect=lambda: calls.append("cycle")), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "RUN_MODE", "cron"):
            result = ik.main()

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["telegram"])

    def test_cron_runs_cycle_after_backoff_deadline_passed(self):
        self._set_backoff(-1)
        with mock.patch.object(ik, "initialize_runtime"), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "run_ikea_check_cycle", return_value=0) as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "RUN_MODE", "cron"):
            result = ik.main()

        self.assertEqual(result, 0)
        mock_cycle.assert_called_once()

    def test_cron_returns_cycle_exit_code_without_backoff(self):
        with mock.patch.object(ik, "initialize_runtime"), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "run_ikea_check_cycle", return_value=3) as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "RUN_MODE", "cron"):
            result = ik.main()

        self.assertEqual(result, 3)
        mock_cycle.assert_called_once()

    def test_daemon_path_unchanged_during_active_backoff(self):
        self._set_backoff(1200)
        with mock.patch.object(ik, "initialize_runtime"), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "run_daemon", return_value=0) as mock_daemon, \
             mock.patch.object(ik, "run_ikea_check_cycle") as mock_cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "RUN_MODE", "daemon"):
            result = ik.main()

        self.assertEqual(result, 0)
        mock_daemon.assert_called_once()
        mock_cycle.assert_not_called()


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

        def fake_handle_telegram_updates(long_poll_seconds=0):
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
    """EMAIL_ENABLED=False (SMTP_MODE=disabled) - run_ikea_check_cycle()
    nie wywoluje send_email() w ogole."""

    def setUp(self):
        self._orig_store_ids = list(ik.STORE_IDS)
        self._orig_terms = list(ik.SEARCH_TERMS)
        self._orig_numbers = list(ik.SEARCH_ARTICLE_NUMBERS)
        ik.SEARCH_TERMS = ["stol"]
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.STORE_IDS = ["294"]
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        _save_seen(set())  # nie-first-run

    def tearDown(self):
        ik.STORE_IDS = self._orig_store_ids
        ik.SEARCH_TERMS = self._orig_terms
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig_numbers
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def _product(self, uuid):
        return {
            "title": "Stol drewniany",
            "description": "opis",
            "originalPrice": 200,
            "articleNumbers": ["12345678"],
            "currency": "PLN",
            "storeId": "294",
            "offers": [{
                "offerUuid": uuid,
                "offerNumber": "12345",
                "price": 100,
                "productConditionTitle": "Nowy",
                "productConditionDescription": "brak",
                "reasonDiscount": "wystawowy",
                "additionalInfo": None,
            }],
            "heroImage": None,
        }

    def test_cycle_skips_email_when_disabled(self):
        with mock.patch.object(ik, "fetch_store_offers", return_value=[self._product("uuid-off")]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "send_email") as mock_send_email, \
             mock.patch.object(ik, "send_telegram") as mock_send_telegram:
            result = ik.run_ikea_check_cycle()
        self.assertEqual(result, 0)
        mock_send_email.assert_not_called()
        mock_send_telegram.assert_not_called()

    def test_cycle_uses_only_telegram_when_email_disabled(self):
        with mock.patch.object(ik, "fetch_store_offers", return_value=[self._product("uuid-tg")]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email") as mock_send_email, \
             mock.patch.object(ik, "send_telegram") as mock_send_telegram:
            result = ik.run_ikea_check_cycle()
        self.assertEqual(result, 0)
        mock_send_email.assert_not_called()
        mock_send_telegram.assert_called_once()
        self.assertIn("uuid-tg", _load_seen())


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


class TestEnvFileParser(unittest.TestCase):
    """load_env_file(): przedrostek export, cudzyslowy, komentarze w linii,
    bledy z numerem linii bez wypisywania wartosci."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ikea_okazje_env_parser_")
        self.addCleanup(tmp.cleanup)
        self._tmp_dir = tmp.name
        self._path = os.path.join(self._tmp_dir, "test.env")

    def _load(self, contents, mode=0o600):
        with open(self._path, "w", encoding="utf-8") as f:
            f.write(contents)
        os.chmod(self._path, mode)
        return ik.load_env_file(self._path)

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(ik.load_env_file(os.path.join(self._tmp_dir, "brak.env")), {})

    def test_plain_values_and_full_line_comments(self):
        values = self._load("# komentarz\n\nA=1\n  B = dwa  \nbez_znaku_rownosci\n")
        self.assertEqual(values, {"A": "1", "B": "dwa"})

    def test_export_prefix_is_ignored(self):
        values = self._load("export SMTP_USER=ja@example.com\nexport\tB=2\nexportC=3\n")
        self.assertEqual(values, {"SMTP_USER": "ja@example.com", "B": "2", "exportC": "3"})

    def test_single_quoted_value_kept_verbatim(self):
        values = self._load("SMTP_PASS=' ab #cd '\n")
        self.assertEqual(values["SMTP_PASS"], " ab #cd ")

    def test_double_quoted_value_kept_verbatim(self):
        values = self._load('SMTP_PASS="x # y"\n')
        self.assertEqual(values["SMTP_PASS"], "x # y")

    def test_other_quote_type_inside_quotes_is_kept(self):
        values = self._load("A=\"it's\"\nB='say \"hi\"'\n")
        self.assertEqual(values, {"A": "it's", "B": 'say "hi"'})

    def test_comment_after_closing_quote_is_allowed(self):
        values = self._load("A='wartosc' # komentarz\nB=\"x\"#komentarz\n")
        self.assertEqual(values, {"A": "wartosc", "B": "x"})

    def test_empty_quoted_value(self):
        values = self._load("TELEGRAM_BOT_TOKEN=''\nTELEGRAM_CHAT_ID=\"\"\n")
        self.assertEqual(values, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""})

    def test_text_after_closing_quote_raises_without_value(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._load("A=1\nSMTP_PASS='tajne-haslo'reszta\n")
        message = str(ctx.exception)
        self.assertIn("linia 2", message)
        self.assertIn(self._path, message)
        self.assertNotIn("tajne-haslo", message)
        self.assertNotIn("reszta", message)

    def test_unterminated_quote_raises_with_line_number_without_value(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._load("A=1\n\nSMTP_PASS='tajne-haslo\n")
        message = str(ctx.exception)
        self.assertIn("linia 3", message)
        self.assertIn("SMTP_PASS", message)
        self.assertIn(self._path, message)
        self.assertNotIn("tajne-haslo", message)

    def test_mismatched_quotes_are_unterminated(self):
        # Stary parser zdejmowal ' i " niezaleznie, wiec 'abc" dawalo abc.
        with self.assertRaises(RuntimeError):
            self._load("A='abc\"\n")

    def test_unquoted_inline_comment_after_whitespace_is_stripped(self):
        values = self._load("MAX_PRICE=300 # zl\nB=x\t# tab\nC= # sam komentarz\n")
        self.assertEqual(values, {"MAX_PRICE": "300", "B": "x", "C": ""})

    def test_unquoted_hash_without_whitespace_is_part_of_value(self):
        values = self._load("A=abc#def\nB=#abc\n")
        self.assertEqual(values, {"A": "abc#def", "B": "#abc"})

    def test_quotes_inside_unquoted_value_are_kept(self):
        values = self._load("A=ab'cd'\n")
        self.assertEqual(values["A"], "ab'cd'")

    def test_equals_sign_inside_value_is_kept(self):
        values = self._load("A='x=y'\nB=k=v\n")
        self.assertEqual(values, {"A": "x=y", "B": "k=v"})

    def test_permission_check_still_enforced(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._load("A=1\n", mode=0o644)
        self.assertIn("chmod 600", str(ctx.exception))


class TestConfigValueValidation(unittest.TestCase):
    """parse_run_mode() i parsery liczbowe - czyste funkcje, w procesie."""

    def _assert_config_error(self, func, *args, key):
        with self.assertRaises(RuntimeError) as ctx:
            func(*args)
        message = str(ctx.exception)
        self.assertIn(key, message)
        self.assertIn(ik.CONFIG_FILE, message)
        return message

    def test_run_mode_valid_values(self):
        self.assertEqual(ik.parse_run_mode("cron"), "cron")
        self.assertEqual(ik.parse_run_mode(" Daemon "), "daemon")
        self.assertEqual(ik.parse_run_mode("CRON"), "cron")

    def test_run_mode_missing_or_empty_defaults_to_cron(self):
        self.assertEqual(ik.parse_run_mode(None), "cron")
        self.assertEqual(ik.parse_run_mode(""), "cron")
        self.assertEqual(ik.parse_run_mode("   "), "cron")

    def test_run_mode_invalid_raises_with_allowed_values(self):
        message = self._assert_config_error(ik.parse_run_mode, "deamon", key="RUN_MODE")
        self.assertIn("cron, daemon", message)

    def test_check_interval_missing_uses_default(self):
        self.assertEqual(ik.parse_check_interval_seconds(None), ik.DEFAULT_CHECK_INTERVAL_SECONDS)
        self.assertEqual(ik.parse_check_interval_seconds("  "), ik.DEFAULT_CHECK_INTERVAL_SECONDS)

    def test_check_interval_valid_values(self):
        self.assertEqual(ik.parse_check_interval_seconds("900"), 900)
        self.assertEqual(ik.parse_check_interval_seconds("300"), 300)
        self.assertEqual(ik.parse_check_interval_seconds("450.5"), 450.5)

    def test_check_interval_below_minimum_is_clamped_with_warning(self):
        self.assertEqual(ik.MIN_CHECK_INTERVAL_SECONDS, 300)
        with mock.patch.object(ik, "log") as mock_log:
            value = ik.parse_check_interval_seconds("60")
        self.assertEqual(value, 300)
        mock_log.assert_called_once()
        self.assertIn("CHECK_INTERVAL_SECONDS", mock_log.call_args.args[0])
        self.assertTrue(mock_log.call_args.kwargs.get("to_stderr"))

    def test_check_interval_invalid_values_raise(self):
        for raw in ("abc", "0", "-5", "inf", "nan", "15m"):
            with self.subTest(raw=raw):
                self._assert_config_error(
                    ik.parse_check_interval_seconds, raw, key="CHECK_INTERVAL_SECONDS"
                )

    def test_telegram_poll_interval(self):
        parse = ik.parse_positive_number
        key = "TELEGRAM_POLL_INTERVAL_SECONDS"
        self.assertEqual(parse(None, key, 15), 15)
        self.assertEqual(parse("", key, 15), 15)
        self.assertEqual(parse("5", key, 15), 5)
        for raw in ("x", "0", "-1"):
            with self.subTest(raw=raw):
                self._assert_config_error(parse, raw, key, 15, key=key)

    def test_optional_filters_missing_means_disabled(self):
        self.assertIsNone(ik.parse_optional_non_negative_number(None, "MAX_PRICE"))
        self.assertIsNone(ik.parse_optional_non_negative_number(" ", "MAX_PRICE"))

    def test_optional_filters_valid_values(self):
        parse = ik.parse_optional_non_negative_number
        self.assertEqual(parse("30", "MIN_DISCOUNT_PERCENT"), 30)
        self.assertEqual(parse("299.99", "MAX_PRICE"), 299.99)
        self.assertEqual(parse("0", "MAX_PRICE"), 0)

    def test_optional_filters_invalid_values_raise(self):
        for key, raw in (
            ("MAX_PRICE", "300zl"),
            ("MAX_PRICE", "-1"),
            ("MIN_DISCOUNT_PERCENT", "trzydziesci"),
            ("MIN_DISCOUNT_PERCENT", "-5"),
        ):
            with self.subTest(key=key, raw=raw):
                self._assert_config_error(
                    ik.parse_optional_non_negative_number, raw, key, key=key
                )


class TestConfigValidationAtStartup(unittest.TestCase):
    """Walidacja w initialize_runtime() - w osobnych procesach."""

    BASE_ENV = "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"

    def _init_and_check(self, env_contents, checks):
        home_dir = _make_isolated_home(self.BASE_ENV + env_contents)
        result = _run_python_code(
            "import ikea_okazje as ik\nik.initialize_runtime()\n" + checks + "print('OK')\n",
            home_dir,
        )
        return result

    def test_invalid_run_mode_fails_at_startup(self):
        result = _run_module_with_env(self.BASE_ENV + "RUN_MODE=deamon\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError", result.stderr)
        self.assertIn("RUN_MODE", result.stderr)
        self.assertIn("ikea-okazje.env", result.stderr)

    def test_run_mode_case_insensitive_at_startup(self):
        result = self._init_and_check(
            "RUN_MODE=Daemon # komentarz\n",
            "assert ik.RUN_MODE == 'daemon', ik.RUN_MODE\n",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_short_check_interval_clamped_with_warning_on_stderr(self):
        result = self._init_and_check(
            "CHECK_INTERVAL_SECONDS=60\n",
            "assert ik.CHECK_INTERVAL_SECONDS == 300, ik.CHECK_INTERVAL_SECONDS\n",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)
        self.assertIn("CHECK_INTERVAL_SECONDS", result.stderr)
        self.assertIn("300", result.stderr)

    def test_invalid_check_interval_fails_at_startup(self):
        result = _run_module_with_env(self.BASE_ENV + "CHECK_INTERVAL_SECONDS=0\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHECK_INTERVAL_SECONDS", result.stderr)

    def test_missing_check_interval_after_reinit_uses_default_not_previous_value(self):
        home_dir = _make_isolated_home(self.BASE_ENV + "CHECK_INTERVAL_SECONDS=900\n")
        config_path = os.path.join(home_dir, ".config", "ikea-okazje.env")
        code = f"""
import os
import ikea_okazje as ik
ik.initialize_runtime()
assert ik.CHECK_INTERVAL_SECONDS == 900, ik.CHECK_INTERVAL_SECONDS
with open({config_path!r}, "w", encoding="utf-8") as f:
    f.write({self.BASE_ENV!r})
os.chmod({config_path!r}, 0o600)
ik.initialize_runtime()
assert ik.CHECK_INTERVAL_SECONDS == ik.DEFAULT_CHECK_INTERVAL_SECONDS, ik.CHECK_INTERVAL_SECONDS
print('OK')
"""
        result = _run_python_code(code, home_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_telegram_poll_interval_default_and_invalid(self):
        result = self._init_and_check(
            "", "assert ik.TELEGRAM_POLL_INTERVAL_SECONDS == 15, ik.TELEGRAM_POLL_INTERVAL_SECONDS\n"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        result = _run_module_with_env(self.BASE_ENV + "TELEGRAM_POLL_INTERVAL_SECONDS=-1\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TELEGRAM_POLL_INTERVAL_SECONDS", result.stderr)

    def test_unparseable_filter_fails_instead_of_disabling(self):
        result = _run_module_with_env(self.BASE_ENV + "MAX_PRICE=300zl\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MAX_PRICE", result.stderr)
        self.assertIn("ikea-okazje.env", result.stderr)

        result = _run_module_with_env(self.BASE_ENV + "MIN_DISCOUNT_PERCENT=-10\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MIN_DISCOUNT_PERCENT", result.stderr)

    def test_filters_with_inline_comments_and_missing_filters(self):
        result = self._init_and_check(
            "MAX_PRICE=300 # zl\nMIN_DISCOUNT_PERCENT='30'\n",
            "assert ik.MAX_PRICE == 300, ik.MAX_PRICE\n"
            "assert ik.MIN_DISCOUNT_PERCENT == 30, ik.MIN_DISCOUNT_PERCENT\n",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

        result = self._init_and_check(
            "MAX_PRICE=\n",
            "assert ik.MAX_PRICE is None, ik.MAX_PRICE\n"
            "assert ik.MIN_DISCOUNT_PERCENT is None, ik.MIN_DISCOUNT_PERCENT\n",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_quoted_password_with_hash_reaches_smtp_settings(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=gmail\nSMTP_USER=ja@example.com\n"
            "export SMTP_PASS='ab #cd ' # haslo aplikacji\nSTORE_IDS=294\n"
        )
        result = _run_python_code(
            "import ikea_okazje as ik\nik.initialize_runtime()\n"
            "assert ik.SMTP_PASS == 'ab #cd ', repr(ik.SMTP_PASS)\nprint('OK')\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_unterminated_quote_error_does_not_print_secret(self):
        result = _run_module_with_env(
            "SMTP_MODE=gmail\nSMTP_USER=ja@example.com\nSMTP_PASS='tajne-haslo-123\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("linia 3", result.stderr)
        self.assertNotIn("tajne-haslo-123", result.stderr)
        self.assertNotIn("tajne-haslo-123", result.stdout)


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


class TestLogFlushing(unittest.TestCase):
    """log() musi wypychac wpis od razu - pod systemd stdout to pipe z
    buforowaniem blokowym. os._exit() pomija flush przy wyjsciu, wiec wpis
    widac w podprocesie tylko wtedy, gdy log() sam go wypchnal."""

    def test_stdout_line_visible_without_interpreter_flush(self):
        home_dir = _make_isolated_home(env_file_contents=None)
        result = _run_python_code(
            "import os\nimport ikea_okazje as ik\nik.log('linia-stdout')\nos._exit(0)\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("linia-stdout", result.stdout)

    def test_stderr_line_visible_without_interpreter_flush(self):
        home_dir = _make_isolated_home(env_file_contents=None)
        result = _run_python_code(
            "import os\nimport ikea_okazje as ik\n"
            "ik.log('linia-stderr', to_stderr=True)\nos._exit(0)\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("linia-stderr", result.stderr)

    def test_log_passes_flush_true_to_print(self):
        for to_stderr in (False, True):
            with mock.patch("builtins.print") as mock_print:
                ik.log("x", to_stderr=to_stderr)
            self.assertTrue(mock_print.call_args.kwargs.get("flush"))


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


class TestTelegramMessageEscaping(unittest.TestCase):
    """Escapowanie HTML w gotowych wiadomosciach Telegrama
    (group_offers_for_notification() + split_telegram_messages())."""

    def _render(self, offer):
        groups = ik.group_offers_for_notification([offer])
        return "\n".join(ik.split_telegram_messages(groups))

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
        text = self._render(o)
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
        text = self._render(o)
        self.assertNotIn("<i>uzyty</i>", text)
        self.assertNotIn("<x>PLN", text)
        self.assertNotIn("<y>123", text)
        self.assertNotIn("<z>294", text)

    def test_reservation_link_href_is_present_and_escaped(self):
        o = self._offer(
            reservation_link='https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroc%C5%82aw/123"onmouseover="x'
        )
        text = self._render(o)
        self.assertIn('href="', text)
        self.assertNotIn('123"onmouseover="x"', text)
        self.assertIn("&quot;onmouseover=&quot;x", text)

    def test_no_reservation_link_when_missing(self):
        o = self._offer(reservation_link=None)
        text = self._render(o)
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
        _save_seen({"existing-uuid"})

        with mock.patch.object(ik, "fetch_store_offers", side_effect=RuntimeError("boom")), \
             mock.patch.object(ik, "send_email") as mock_send_email, \
             mock.patch.object(ik, "send_telegram") as mock_send_telegram:
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 1)
        mock_send_email.assert_not_called()
        mock_send_telegram.assert_not_called()
        self.assertEqual(_load_seen(), {"existing-uuid"})

    def test_one_store_failing_does_not_corrupt_seen_file(self):
        ik.STORE_IDS = ["100", "200"]
        _save_seen({"existing-uuid"})

        def fake_fetch(store_id):
            if store_id == "100":
                raise RuntimeError("boom")
            return []

        with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 0)
        self.assertEqual(_load_seen(), {"existing-uuid"})


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
    (notify_status_message() jest tu zawsze zmockowane).

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
        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_not_called()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertTrue(state["pending_outage"])

    def test_second_consecutive_outage_sends_exactly_one_notification(self):
        # Wymog 2 / test 2: DRUGI kolejny pelny 403/429-cykl potwierdza -
        # dokladnie jedno powiadomienie, stan aktywny.
        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
        with mock.patch.object(ik, "notify_status_message", return_value=all_errors):
            ik.handle_access_notification_state(True, 403)  # drugi, dostawa zawodzi
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertTrue(state["pending_outage"])

    def test_one_channel_succeeds_other_fails_still_marks_alerted(self):
        # Wymog: dostawa jest uznana za sukces, jesli PRZYNAJMNIEJ JEDEN
        # aktywny kanal sie powiodl - tylko
        # CALKOWITA porazka wszystkich aktywnych kanalow nie oznacza alertu
        # jako wyslany. Wymusza dwa aktywne kanaly (e-mail + Telegram) na
        # czas testu, niezaleznie od faktycznej konfiguracji testowego .env.
        with mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            with mock.patch.object(ik, "notify_status_message", return_value=[]):
                ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
            with mock.patch.object(ik, "notify_status_message", return_value=["e-mail: boom"]):
                ik.handle_access_notification_state(True, 403)  # drugi, 1 z 2 kanalow zawodzi
        state = ik.get_access_notification_state()
        self.assertTrue(state["outage_active"])
        self.assertFalse(state["pending_outage"])

    # --- trwalosc stanu / restart procesu ---

    def test_pending_outage_survives_simulated_restart(self):
        # Wymog: restart procesu/systemd PO PIERWSZYM, ale PRZED drugim
        # kwalifikujacym cyklem nie moze "zapomniec", ze to byla juz
        # pierwsza porazka (patrz test 10).
        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
        mock_notify.assert_not_called()

        reloaded_state = ik.load_dynamic_state()
        self.assertTrue(reloaded_state["ikea_access_notification"]["pending_outage"])
        self.assertFalse(reloaded_state["ikea_access_notification"]["outage_active"])

        # "Restart": DYNAMIC_STATE w pamieci zastapiony wczytanym z dysku.
        ik.DYNAMIC_STATE.clear()
        ik.DYNAMIC_STATE.update(reloaded_state)

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify_after_restart:
            ik.handle_access_notification_state(True, 403)  # drugi, po "restarcie"
        mock_notify_after_restart.assert_called_once()
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

    def test_outage_state_survives_simulated_restart(self):
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
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
        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        # "Restart" - stan wczytany na nowo z dysku zastepuje DYNAMIC_STATE
        # w pamieci, tak jak initialize_runtime() robi to na starcie procesu.
        ik.DYNAMIC_STATE.clear()
        ik.DYNAMIC_STATE.update(ik.load_dynamic_state())

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify_after_restart:
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

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)  # pelny sukces
        mock_notify.assert_not_called()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertFalse(state["pending_outage"])

    def test_first_successful_cycle_after_delivered_outage_sends_recovery_notification(self):
        # Wymog 4 / test 5: dokladnie jedno powiadomienie o odzyskaniu, ale
        # TYLKO gdy alert o utracie byl FAKTYCZNIE dostarczony (dwa
        # potwierdzajace cykle 403/429 najpierw).
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)
        mock_notify.assert_called_once()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertIsNone(state["last_status_code"])
        self.assertFalse(state["pending_outage"])

    def test_subsequent_successful_cycles_do_not_send_duplicate_recovery(self):
        # Wymog / test 6.
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(False, None)

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)
            ik.handle_access_notification_state(False, None)
        mock_notify.assert_not_called()

    def test_successful_cycle_without_prior_outage_sends_nothing(self):
        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(False, None)
        mock_notify.assert_not_called()

    def test_new_outage_after_recovery_requires_two_cycles_again(self):
        # Nowa utrata dostepu PO odzyskaniu zaczyna od nowa cala
        # dwucyklowa sekwencje potwierdzania - jeden kwalifikujacy cykl
        # nie wystarcza.
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(False, None)

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 429)
        mock_notify.assert_not_called()
        state = ik.get_access_notification_state()
        self.assertFalse(state["outage_active"])
        self.assertTrue(state["pending_outage"])

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify_second:
            ik.handle_access_notification_state(True, 429)
        mock_notify_second.assert_called_once()
        state = ik.get_access_notification_state()
        self.assertTrue(state["outage_active"])
        self.assertEqual(state["last_status_code"], 429)

    def test_failed_recovery_delivery_does_not_reset_state(self):
        # Wymog / test: dostawa alertu odzyskania zawodzi calkowicie -
        # outage_active zostaje True, zeby kolejny pelny sukces mogl
        # sprobowac ponownie.
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)
            ik.handle_access_notification_state(True, 403)

        active_channels = (1 if ik.EMAIL_ENABLED else 0) + (1 if ik.TELEGRAM_ENABLED else 0)
        all_errors = ["e-mail: boom"] if ik.EMAIL_ENABLED else []
        if ik.TELEGRAM_ENABLED:
            all_errors.append("telegram: boom")
        self.assertEqual(len(all_errors), active_channels)

        with mock.patch.object(ik, "notify_status_message", return_value=all_errors):
            ik.handle_access_notification_state(False, None)
        self.assertTrue(ik.get_access_notification_state()["outage_active"])

    # --- cykle czesciowe/mieszane (wymog 5 / testy 7-8-9) ---

    def test_partial_cycle_resets_pending_sequence(self):
        # Wymog 5 / test 9: cykl czesciowy/mieszany PO pierwszym
        # (niepotwierdzonym) kwalifikujacym cyklu zeruje sekwencje -
        # nastepny 403/429 zaczyna od nowa (musi znow byc DWA kolejne).
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
            ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
        self.assertTrue(ik.get_access_notification_state()["pending_outage"])

        ik.reset_pending_outage_after_partial_cycle()
        self.assertFalse(ik.get_access_notification_state()["pending_outage"])

        with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
            ik.handle_access_notification_state(True, 403)  # znow "pierwszy" po resecie
        mock_notify.assert_not_called()
        self.assertTrue(ik.get_access_notification_state()["pending_outage"])

    def test_partial_cycle_clears_stale_last_status_code(self):
        # Czyszczenie: reset_pending_outage_after_partial_cycle() musi
        # wyzerowac rowniez last_status_code bloku 'ikea_access_notification'
        # (nie tylko pending_outage) - inaczej trwaly stan na dysku
        # zostawialby "widmowy" kod 403/429 z przerwanej, niepotwierdzonej
        # sekwencji, mimo ze pending_outage jest juz False.
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
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

        with mock.patch.object(ik, "notify_status_message", return_value=[]):
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
        with mock.patch.object(ik, "notify_status_message", return_value=[]):
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]):
                ik.run_ikea_check_cycle()
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            with mock.patch.object(ik, "fetch_store_offers", return_value=[]), \
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]):
                ik.run_ikea_check_cycle()  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.STORE_IDS = ["100", "200"]

            def fake_fetch(store_id):
                if store_id == "100":
                    raise ik.BlockedByServerError(403, "HTTP 403")
                raise TimeoutError("timed out")

            with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch), \
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]):
                ik.run_ikea_check_cycle()  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.STORE_IDS = ["100", "200"]

            def fake_fetch(store_id):
                if store_id == "100":
                    raise ik.BlockedByServerError(403, "HTTP 403")
                return []

            with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch), \
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
            with mock.patch.object(ik, "notify_status_message", return_value=[]):
                ik.handle_access_notification_state(True, 403)
                ik.handle_access_notification_state(True, 403)

            with mock.patch.object(ik, "fetch_store_offers", return_value=[]), \
                 mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
            with mock.patch.object(ik, "notify_status_message", return_value=[]):
                ik.handle_access_notification_state(True, 403)  # pierwszy -> pending
            self.assertTrue(ik.get_access_notification_state()["pending_outage"])

            ik.STORE_IDS = []
            with mock.patch.object(ik, "notify_status_message", return_value=[]) as mock_notify:
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
            with mock.patch.object(ik, "notify_status_message", return_value=[]):
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

        # Cron pomija odpalenia przed terminem backoffu, wiec symulujemy
        # kolejne odpalenie crona juz PO uplywie zapisanego terminu.
        state_after_first["blocking_backoff"]["next_allowed_check_at"] = time.time() - 1
        with open(dynamic_state_path, "w", encoding="utf-8") as f:
            json.dump(state_after_first, f)

        # Drugie, calkowicie NIEZALEZNE odpalenie (proces #2, "kolejny cron")
        # - potwierdza sekwencje i wysyla alert (notify_status_message() nie
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

    def test_second_cron_invocation_during_backoff_does_not_query_ikea(self):
        home_dir = _make_isolated_home(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
            "SEARCH_TERMS=stall\n"
        )
        dynamic_state_path = os.path.join(home_dir, ".ikea_okazje_dynamic.json")

        first = self._run_one_cron_invocation(home_dir)
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        with open(dynamic_state_path, "r", encoding="utf-8") as f:
            state_after_first = json.load(f)
        self.assertGreater(state_after_first["blocking_backoff"]["next_allowed_check_at"], time.time())

        code = (
            "import unittest.mock as mock\n"
            "import ikea_okazje as ik\n"
            "with mock.patch.object(ik, 'fetch_store_offers') as mock_fetch, \\\n"
            "     mock.patch.object(ik, 'notify_status_message') as mock_notify:\n"
            "    exit_code = ik.main()\n"
            "assert exit_code == 0, exit_code\n"
            "assert not mock_fetch.called, mock_fetch.call_args_list\n"
            "assert not mock_notify.called\n"
            "print('OK')\n"
        )
        second = _run_python_code(code, home_dir)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        self.assertIn("OK", second.stdout)
        self.assertIn("pomijam sprawdzanie ofert", second.stdout)

        with open(dynamic_state_path, "r", encoding="utf-8") as f:
            state_after_second = json.load(f)
        # Pominiete odpalenie nie liczy sie jako drugi zablokowany cykl.
        self.assertEqual(
            state_after_second["ikea_access_notification"],
            state_after_first["ikea_access_notification"],
        )
        self.assertEqual(state_after_second["blocking_backoff"], state_after_first["blocking_backoff"])

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
            # notify_status_message() zmockowane - ten test dotyczy WYLACZNIE
            # globalnego stanu backoffu (BackoffState), nie alertu o
            # utracie dostepu (patrz TestAccessNotificationState wyzej) -
            # bez tego mocka test probowalby nawiazac prawdziwe polaczenie SMTP.
            with mock.patch.object(
                ik, "fetch_store_offers",
                side_effect=ik.BlockedByServerError(403, "HTTP 403"),
            ), mock.patch.object(ik.time, "sleep"), \
                 mock.patch.object(ik, "notify_status_message", return_value=[]):
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
                 mock.patch.object(ik, "notify_status_message", return_value=[]):
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
        formatted = "\n".join(ik.split_telegram_messages(groups))
        self.assertNotIn("nowych sztuk", formatted)

    def test_multi_item_group_has_count_label(self):
        offers = [
            _make_offer(offer_number="1"),
            _make_offer(offer_number="2"),
            _make_offer(offer_number="3"),
        ]
        groups = ik.group_offers_for_notification(offers)
        formatted = "\n".join(ik.split_telegram_messages(groups))
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
        with mock.patch.object(ik, "telegram_send_message") as mock_send, \
             mock.patch.object(ik, "TELEGRAM_SERIES_DELAY_SECONDS", 0):
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

        with mock.patch.object(ik, "telegram_send_message", side_effect=fake_send), \
             mock.patch.object(ik, "TELEGRAM_SERIES_DELAY_SECONDS", 0):
            with self.assertRaises(RuntimeError) as ctx:
                ik.send_telegram(offers)
            self.assertIn("1/", str(ctx.exception))

    def test_error_on_first_message_raises(self):
        offers = [_make_offer()]
        with mock.patch.object(ik, "telegram_send_message", side_effect=RuntimeError("Telegram down")):
            with self.assertRaises(RuntimeError) as ctx:
                ik.send_telegram(offers)
            self.assertIn("0/", str(ctx.exception))

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
        _save_seen(set())

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
        seen = _load_seen()
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
        seen2 = _load_seen()
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
        seen = _load_seen()
        self.assertNotIn("uuid-4", seen)  # nie w seen, tylko w pending

        # Drugi cykl - Telegram odzyskuje sprawnosc i odbiera oferte.
        # Z dostawą per kanał: e-mail juz dostarczyl w cyklu 1 (jest w pending),
        # wiec TYLKO Telegram dostaje oferte w cyklu 2 - e-mail nie dostaje duplikatu.
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda offers: email_calls.append(offers)), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(email_calls), 1)  # e-mail juz dostarczyl w cyklu 1 - brak duplikatu
        self.assertEqual(len(tg_calls), 1)     # Telegram pomyslnie otrzymuje oferty
        seen2 = _load_seen()
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
        seen = _load_seen()
        self.assertNotIn("uuid-5", seen)  # nie w seen, tylko w pending

        # Drugi cykl - e-mail odzyskuje sprawnosc i odbiera oferte.
        # Z dostawą per kanał: Telegram juz dostarczyl w cyklu 1 (jest w pending),
        # wiec TYLKO e-mail dostaje oferte w cyklu 2 - Telegram nie dostaje duplikatu.
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda offers: email_calls.append(offers)), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(email_calls), 1)  # e-mail pomyslnie otrzymuje oferty
        self.assertEqual(len(tg_calls), 1)     # Telegram juz dostarczyl w cyklu 1 - brak duplikatu
        seen2 = _load_seen()
        self.assertIn("uuid-5", seen2)

    def test_telegram_message_2_fails_while_email_succeeds(self):
        # Integracyjny test: wiadomosc Telegrama podzielona na serie;
        # czesc 1 dociera, czesc 2 zawodzi. Mimo ze e-mail dotarl,
        # oferty NIE sa zapisywane jako seen (Telegram nie zakonczyl serii).
        uuids = [f"uuid-split-{i}" for i in range(150)]
        product = self._fake_api_product(uuids)
        delay_patcher = mock.patch.object(ik, "TELEGRAM_SERIES_DELAY_SECONDS", 0)
        delay_patcher.start()
        self.addCleanup(delay_patcher.stop)

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
        seen = _load_seen()
        for u in uuids:
            self.assertNotIn(u, seen)

        # Drugi cykl: Telegram dziala bezblednie dla wszystkich czesci serii.
        # Z dostawą per kanał: e-mail juz dostarczyl w cyklu 1 (jest w pending),
        # wiec TYLKO Telegram dostaje oferty w cyklu 2 - e-mail nie dostaje duplikatu.
        tg_delivered = []
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "deliver_email_message", side_effect=lambda s, b: email_delivered.append(b)), \
             mock.patch.object(ik, "telegram_send_message", side_effect=lambda msg, **kw: tg_delivered.append(msg)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(email_delivered), 1)  # e-mail juz dostarczyl w cyklu 1 - brak duplikatu
        self.assertGreater(len(tg_delivered), 1)   # Telegram dostarcza cala serie wiadomosci
        seen2 = _load_seen()
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
        _save_seen(set())

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
            seen = _load_seen()
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
            seen = _load_seen()
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


class TestTelegramTitlePreservationAndSplitting(unittest.TestCase):
    def test_split_preserves_full_product_title_across_split_messages(self):
        # Jedna grupa z dosc dluga, ale mieszczaca sie przy pojedynczej ofercie nazwa produktu;
        # tyle egzemplarzy, ze cala grupa nie miesci sie w jednej wiadomosci, lecz po podziale
        # na dwie wiadomosci pelna nazwa miesci sie w obu.
        long_title = "Stół do jadalni rozkładany dębowy model Stockholm 2026 super stan i elegancja"
        offers = [
            _make_offer(
                offer_uuid=f"stockholm-{i}",
                offer_number=f"705574{i:02d}",
                title=long_title,
                description="Stan idealny, brak widocznych śladów użytkowania.",
                reservation_link=f"https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroclaw/705574{i:02d}",
            )
            for i in range(50)
        ]
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(len(groups), 1)

        msgs = ik.split_telegram_messages(groups)
        self.assertGreaterEqual(len(msgs), 2, "Oferty powinny zostac podzielone na co najmniej 2 wiadomosci")

        escaped_title = ik.escape_html(long_title)
        for idx, msg in enumerate(msgs):
            self.assertLessEqual(len(msg), ik.TELEGRAM_SAFE_LIMIT, f"Wiadomosc #{idx+1} przekracza limit")
            self.assertNotIn("(...)", msg)
            # Asercja: w wygenerowanych wiadomosciach pojawia sie PELNA nazwa produktu
            self.assertIn(f"<b>{escaped_title}</b>", msg, f"Wiadomosc #{idx+1} nie zawiera pelnego tytulu")
            if idx > 0:
                self.assertIn("(cd.)", msg, f"Wiadomosc kontynuacji #{idx+1} powinna miec oznaczenie (cd.)")

        # Kazdy numer oferty i link do rezerwacji jest obecny
        all_text = "\n".join(msgs)
        for o in offers:
            self.assertIn(o["offer_number"], all_text)
            self.assertIn(o["reservation_link"], all_text)

    def test_extreme_long_title_truncates_longest_prefix_with_ellipsis(self):
        # Skrajna sytuacja: nazwa produktu jest tak dluga (np. 4500 znakow), ze nawet
        # z pojedyncza oferta przekracza limit — zachowany mozliwie dlugi poczatek
        # nazwy z '…', brak bledu, zachowany numer oferty i link do rezerwacji.
        extreme_title = "Stół dębowy konferencyjny z regulowaną wysokością blatu " * 80  # ~4500 znakow
        offer = _make_offer(
            offer_uuid="extreme-title-1",
            offer_number="87724799",
            title=extreme_title,
            description="Krotki opis",
            reservation_link="https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/wroclaw/87724799",
        )
        groups = ik.group_offers_for_notification([offer])
        self.assertEqual(len(groups), 1)

        msgs = ik.split_telegram_messages(groups)
        self.assertEqual(len(msgs), 1)
        msg = msgs[0]

        self.assertLessEqual(len(msg), ik.TELEGRAM_SAFE_LIMIT)
        self.assertNotIn("(...)", msg)
        self.assertIn("…", msg)
        # Nie moze zredukowac tytulu do samego wielokropka '<b>…</b>':
        self.assertNotIn("<b>…</b>", msg)
        # Zachowuje mozliwie dlugi poczatek nazwy (duza dlugosc wiadomosci i poczatek tytulu):
        self.assertIn("<b>Stół dębowy konferencyjny", msg)
        self.assertGreater(len(msg), 3500, "Wiadomosc powinna wykorzystac wiekszosc dostepnego miejsca")
        # Kompletny numer oferty i link
        self.assertIn("87724799", msg)
        self.assertIn(offer["reservation_link"], msg)
        self.assertIn("Rezerwuj", msg)
        # Poprawnosc HTML
        self.assertEqual(msg.count("<b>"), msg.count("</b>"))
        self.assertEqual(msg.count("<a "), msg.count("</a>"))
        tag_stripped = re.sub(r"<b>|</b>|<a\s+href=\"[^\"]*\">|</a>", "", msg)
        self.assertNotIn("<", tag_stripped)
        self.assertNotIn(">", tag_stripped)



class TestPerChannelDeliveryState(unittest.TestCase):
    """Testy dla stanu dostawy per kanal (klucz 'pending' w pliku stanu).

    Scenariusz z opisu bledu: gdy e-mail (SMTP) pada trwale, Telegram
    nie powinien dostawac tej samej oferty przy kazdym cyklu - po
    pierwszym sukcesie Telegrama UUID trafia do 'pending' i w kolejnych
    cyklach Telegram jest pomijany (dostarczyl), dopoki e-mail nie
    zostanie naprawiony.

    Wymagania z zadania:
    1. E-mail pada trwale - Telegram w 2. cyklu nie dostaje juz tej samej oferty.
    2. Po naprawie e-maila oferta laduje w seen.
    3. Stary plik stanu (bez 'pending') dziala poprawnie.
    4. Wylaczenie kanalu nie blokuje pending na zawsze.
    5. Znikniecie oferty z API oczyszcza pending.
    6. Zapis stanu po czesciowym sukcesie (przed wyjsciem z kodem 2).
    """

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
        _save_seen(set())   # nie-first-run

    def tearDown(self):
        ik.STORE_IDS = self._orig_store_ids
        ik.SEARCH_TERMS = self._orig_terms
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig_numbers
        ik.time.sleep = self._orig_sleep
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def _fake_product(self, uuid, offer_number="12345"):
        return {
            "title": "Stol drewniany",
            "description": "opis",
            "originalPrice": 200,
            "articleNumbers": ["12345678"],
            "currency": "PLN",
            "storeId": "294",
            "offers": [{
                "offerUuid": uuid,
                "offerNumber": offer_number,
                "price": 100,
                "productConditionTitle": "Nowy",
                "productConditionDescription": "brak",
                "reasonDiscount": "wystawowy",
                "additionalInfo": None,
            }],
            "heroImage": None,
        }

    # --- Wymog 1: e-mail pada trwale, Telegram nie dostaje duplikatu ---

    def test_email_permanent_failure_telegram_not_resent_in_cycle2(self):
        """Scenariusz z raportu bledu: SMTP pada, a Telegram dostal juz
        oferte w cyklu 1. W cyklu 2 Telegram NIE dostaje duplikatu."""
        product = self._fake_product("uuid-A")
        tg_calls = []

        # Cykl 1: e-mail pada, Telegram dostarcza
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("cert expired")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result1 = ik.run_ikea_check_cycle()

        self.assertEqual(result1, 2)   # blad (e-mail zawiodl)
        self.assertEqual(len(tg_calls), 1, "Telegram powinien dostarczyc w cyklu 1")
        seen1 = _load_seen()
        self.assertNotIn("uuid-A", seen1, "UUID nie powinien byc w seen - e-mail jeszcze nie dostarczyl")

        # Sprawdz, ze pending zapisany
        state1 = ik.load_seen_state()
        self.assertIn("uuid-A", state1["pending"], "UUID powinien byc w pending po czesciowym sukcesie")
        self.assertIn("telegram", state1["pending"]["uuid-A"], "Telegram powinien byc w pending jako dostarczony")

        # Cykl 2: e-mail pada dalej. Telegram juz dostarczyl - NIE powinien dostac duplikatu.
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("cert expired")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda offers: tg_calls.append(offers)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 2)    # dalej blad (e-mail ciągle nie działa)
        self.assertEqual(len(tg_calls), 1, "Telegram NIE powinien dostac duplikatu w cyklu 2")

    # --- Wymog 2: Po naprawie e-maila oferta laduje w seen ---

    def test_offer_moves_to_seen_after_email_repair(self):
        """Po naprawie e-maila (cykl 3) UUID przenosi sie z pending do seen."""
        product = self._fake_product("uuid-B")
        email_calls = []
        tg_calls = []

        # Cykl 1: e-mail pada, Telegram dostarcza
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp down")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            ik.run_ikea_check_cycle()

        # Cykl 2: e-mail naprawiony - dostarcza; UUID powinien trafic do seen
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda o: email_calls.append(o)), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 0)
        self.assertEqual(len(email_calls), 1, "E-mail powinien dostarczyc w cyklu naprawy")
        self.assertEqual(len(tg_calls), 1, "Telegram nie powinien dostac duplikatu po naprawie e-maila")
        seen = _load_seen()
        self.assertIn("uuid-B", seen, "UUID powinien byc w seen po naprawie e-maila")
        state = ik.load_seen_state()
        self.assertNotIn("uuid-B", state["pending"], "UUID nie powinien byc w pending po przeniesieniu do seen")

    # --- Wymog 3: Stary plik stanu (bez 'pending') dziala ---

    def test_old_state_file_without_pending_key_works(self):
        """Stary plik stanu bez klucza 'pending' jest wczytywany poprawnie."""
        # Zapisz plik TYLKO z kluczem 'seen' (stary format)
        old_state = {"seen": ["old-uuid-1", "old-uuid-2"]}
        with open(ik.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(old_state, f)

        # Nowa oferta - stary plik nie ma 'pending'
        product = self._fake_product("uuid-new")
        tg_calls = []

        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 0)
        self.assertEqual(len(tg_calls), 1)
        seen = _load_seen()
        self.assertIn("uuid-new", seen)
        self.assertIn("old-uuid-1", seen, "Stare UUID musza zostac zachowane")
        self.assertIn("old-uuid-2", seen, "Stare UUID musza zostac zachowane")

    def test_load_seen_state_old_file_returns_empty_pending(self):
        """load_seen_state() na starym pliku bez 'pending' zwraca pusty slownik."""
        old_state = {"seen": ["uuid-X", "uuid-Y"]}
        with open(ik.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(old_state, f)

        state = ik.load_seen_state()
        self.assertEqual(state["seen"], {"uuid-X", "uuid-Y"})
        self.assertEqual(state["pending"], {})

    # --- Wymog 4: Wylaczenie kanalu nie blokuje pending na zawsze ---

    def test_disabling_channel_does_not_leave_offer_in_pending_forever(self):
        """Gdy jeden kanal jest wylaczony, liczy sie tylko drugi.
        Po dostarczeniu przez jedyny aktywny kanal UUID trafia do seen."""
        product = self._fake_product("uuid-C")
        tg_calls = []

        # Cykl 1: oba kanaly wlaczone; e-mail pada, Telegram dostarcza
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            result1 = ik.run_ikea_check_cycle()

        self.assertEqual(result1, 2)

        # Cykl 2: uzytkownik WYLACZA email (disabled). Teraz tylko Telegram
        # jest aktywny i juz dostarczyl. UUID powinien trafic do seen.
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(tg_calls), 1, "Telegram nie powinien dostac duplikatu po wylaczeniu e-maila")
        seen = _load_seen()
        self.assertIn("uuid-C", seen, "UUID powinien byc w seen gdy jedyny aktywny kanal dostarczyl")

    def test_disabling_already_delivered_channel_does_not_resend(self):
        """Gdy kanal, ktory juz dostarczyl, zostaje wylaczony - oferta
        nie jest ponawiana (do zadnego innego kanalu) - trafia do seen."""
        product = self._fake_product("uuid-D")
        email_calls = []
        tg_calls = []

        # Cykl 1: oba kanaly wlaczone; Telegram dostarcza, e-mail pada
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("no SMTP")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            ik.run_ikea_check_cycle()

        # Cykl 2: uzytkownik WYLACZA Telegram. Teraz tylko email jest aktywny.
        # Dopiero email musi dostarczyc; Telegram jest juz nieistotny (wylaczony).
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik, "send_email", side_effect=lambda o: email_calls.append(o)):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 0)
        self.assertEqual(len(email_calls), 1, "Email powinien dostarczyc po wylaczeniu Telegrama")
        seen = _load_seen()
        self.assertIn("uuid-D", seen)

    # --- Wymog 5: Znikniecie oferty z API oczyszcza pending ---

    def test_offer_disappearing_from_api_clears_pending(self):
        """UUID w pending, ktorego nie ma juz w API, jest usuwany z pending."""
        product = self._fake_product("uuid-E")
        tg_calls = []

        # Cykl 1: e-mail pada, Telegram dostarcza (uuid-E trafia do pending)
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            ik.run_ikea_check_cycle()

        state_after_1 = ik.load_seen_state()
        self.assertIn("uuid-E", state_after_1["pending"])

        # Cykl 2: oferta ZNIKLA z API (puste wyniki)
        with mock.patch.object(ik, "fetch_store_offers", return_value=[]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)   # brak bledow (nic nie bylo do wyslania)
        state_after_2 = ik.load_seen_state()
        self.assertNotIn("uuid-E", state_after_2["pending"], "Zniknieta oferta powinna byc usunieta z pending")
        # Nie trafia tez do seen (nie bylo pelnej dostawy)
        self.assertNotIn("uuid-E", state_after_2["seen"])

    # --- Wymog 6: Zapis stanu po czesciowym sukcesie ---

    def test_progress_saved_before_returning_code_2(self):
        """Po czesciowym sukcesie (jeden kanal zawodzi) stan jest zapisywany
        PRZED zwroceniem kodu 2 - nastepny cykl zna juz postep."""
        product = self._fake_product("uuid-F")
        email_calls = []
        tg_calls = []

        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=lambda o: email_calls.append(o)), \
             mock.patch.object(ik, "send_telegram", side_effect=RuntimeError("tg down")):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 2)
        self.assertEqual(len(email_calls), 1)

        # Stan MUSI byc zapisany PRZED powrotem z kodem 2
        state = ik.load_seen_state()
        self.assertIn("uuid-F", state["pending"], "Postep e-maila musi byc zapisany w pending")
        self.assertIn("email", state["pending"]["uuid-F"])
        self.assertNotIn("telegram", state["pending"]["uuid-F"])
        self.assertNotIn("uuid-F", state["seen"], "UUID nie powinien byc w seen - Telegram nie dostarczyl")

    # --- Testy _enabled_channels() ---

    def test_enabled_channels_email_only(self):
        with mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False):
            self.assertEqual(ik._enabled_channels(), ["email"])

    def test_enabled_channels_telegram_only(self):
        with mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            self.assertEqual(ik._enabled_channels(), ["telegram"])

    def test_enabled_channels_both(self):
        with mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            channels = ik._enabled_channels()
            self.assertIn("email", channels)
            self.assertIn("telegram", channels)
            self.assertEqual(len(channels), 2)

    def test_enabled_channels_none(self):
        with mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False):
            self.assertEqual(ik._enabled_channels(), [])

    # --- Testy load_seen_state() i save_seen_state() ---

    def test_save_seen_state_and_load_seen_state_roundtrip(self):
        """Zapisany stan (seen + pending) jest identycznie odczytywany."""
        seen = {"u1", "u2"}
        pending = {"u3": ["email"], "u4": ["email", "telegram"]}
        ik.save_seen_state(seen, pending)

        state = ik.load_seen_state()
        self.assertEqual(state["seen"], seen)
        self.assertEqual(set(state["pending"]["u3"]), {"email"})
        self.assertEqual(set(state["pending"]["u4"]), {"email", "telegram"})

    def test_save_seen_state_empty_pending_not_written(self):
        """Pusty pending nie jest zapisywany jako klucz w pliku JSON."""
        ik.save_seen_state({"u1"}, {})
        with open(ik.STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("pending", data, "Pusty pending nie powinien byc zapisywany")

    def test_load_seen_state_nonexistent_file_returns_empty(self):
        """Brak pliku stanu zwraca pusty stan."""
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        state = ik.load_seen_state()
        self.assertEqual(state["seen"], set())
        self.assertEqual(state["pending"], {})

    # --- Scenariusz pelny: 6 razy ta sama oferta na Telegramie ---

    def test_telegram_not_spammed_when_email_fails_permanently(self):
        """Odwzorowanie scenariusza z opisu bledu: e-mail pada przy kazdym cyklu.
        Telegram dostaje oferte DOKLADNIE RAZ (cykl 1), a nie przy kazdym cyklu."""
        product = self._fake_product("uuid-spam")
        tg_calls = []
        email_failures = []

        def always_fail_email(offers):
            email_failures.append(offers)
            raise RuntimeError("SMTP cert expired - permanent")

        for cycle in range(4):
            with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
                 mock.patch.object(ik, "EMAIL_ENABLED", True), \
                 mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
                 mock.patch.object(ik, "send_email", side_effect=always_fail_email), \
                 mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls.append(o)):
                ik.run_ikea_check_cycle()

        self.assertEqual(len(tg_calls), 1,
                         f"Telegram powinien dostac oferte tylko RAZ, a nie {len(tg_calls)} razy")

    # --- Integracja: brak regresji w zachowaniu kodu wyjscia ---

    def test_all_channels_success_returns_zero(self):
        """Gdy oba kanaly dostarczaja pomyslnie, kod wyjscia to 0."""
        product = self._fake_product("uuid-ok")
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email"), \
             mock.patch.object(ik, "send_telegram"):
            result = ik.run_ikea_check_cycle()
        self.assertEqual(result, 0)
        seen = _load_seen()
        self.assertIn("uuid-ok", seen)

    def test_all_channels_fail_returns_two_and_no_seen(self):
        """Gdy oba kanaly zawodza, kod wyjscia to 2 i UUID nie jest w seen."""
        product = self._fake_product("uuid-fail")
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("email")), \
             mock.patch.object(ik, "send_telegram", side_effect=RuntimeError("tg")):
            result = ik.run_ikea_check_cycle()
        self.assertEqual(result, 2)
        seen = _load_seen()
        self.assertNotIn("uuid-fail", seen)

    def test_telegram_grouping_preserved_for_channel_offers(self):
        """Telegram jest dostarczony dopiero po wyslaniu calej serii wiadomosci -
        grupowanie i limit dlugosci pozostaja niezmienione.\""""
        # 150 ofert tego samego produktu -> wymusza wiele wiadomosci Telegrama
        offers = []
        for i in range(30):
            product = self._fake_product(f"uuid-group-{i}", f"offer-{i}")
            offers.append(product)

        tg_msg_count = {"n": 0}
        email_calls = []

        def fake_tg_send(msg, **kwargs):
            tg_msg_count["n"] += 1

        with mock.patch.object(ik, "fetch_store_offers", return_value=offers), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "deliver_email_message", side_effect=lambda s, b: email_calls.append(b)), \
             mock.patch.object(ik, "telegram_send_message", side_effect=fake_tg_send):
            result = ik.run_ikea_check_cycle()

        self.assertEqual(result, 0)
        self.assertEqual(len(email_calls), 1, "E-mail wyslany dokladnie raz")
        self.assertGreaterEqual(tg_msg_count["n"], 1, "Telegram wyslal przynajmniej jedna wiadomosc")

    # --- Regresja: pruning pending tylko przy pelnym pobieraniu sklepow ---

    def test_pending_not_pruned_when_store_fetch_fails(self):
        """Regresja: UUID w pending z niedostepnego sklepu NIE moze byc usuniety
        z pending podczas czesciowego cyklu pobierania.

        Scenariusz: dwa sklepy (A=294, B=999). W cyklu 1 oba sklepy dzialaja -
        e-mail pada, Telegram dostarcza oferty sklepu A (trafiaja do pending).
        W cyklu 2 sklep A jest niedostepny (tylko sklep B odpowiada). UUID-y
        z pending, ktore naleza do sklepu A, NIE moga byc usuwane z pending
        tylko dlatego, ze sklepu A tym razem nie udalo sie pobrac - oferta
        moze wciaz istniec w API.\""""
        uuid_sklep_a = "uuid-store-a"
        product_a = self._fake_product(uuid_sklep_a, "offer-a")
        product_a["storeId"] = "294"

        # Ustaw dwa sklepy
        ik.STORE_IDS = ["294", "999"]

        # Cykl 1: oba sklepy dzialaja; e-mail pada, Telegram dostarcza oferty sklepu A
        def fake_fetch_cycle1(store_id):
            if store_id == "294":
                return [product_a]
            return []   # sklep 999 - brak ofert pasujacych

        with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch_cycle1), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp down")), \
             mock.patch.object(ik, "send_telegram"):
            result1 = ik.run_ikea_check_cycle()

        self.assertEqual(result1, 2)
        state_after_1 = ik.load_seen_state()
        self.assertIn(uuid_sklep_a, state_after_1["pending"],
                      "UUID ze sklepu A musi byc w pending po czesciowym sukcesie")

        # Cykl 2: sklep 294 (A) pada - store_errors niepuste.
        # Dodatkowo, wylaczamy email (SMTP_MODE=disabled) - co w normalnych
        # warunkach awansowaloby oferte wylacznie-telegramowa do seen,
        # ALE poniewaz sklep A jest niedostepny, nie promujemy w ciemno.
        # UUID-y z pending nalezace do sklepu A NIE moga byc usuwane ani promowane.
        def fake_fetch_cycle2(store_id):
            if store_id == "294":
                raise RuntimeError("sklep 294 tymczasowo niedostepny")
            return []   # sklep 999 - brak ofert

        tg_calls_2 = []
        with mock.patch.object(ik, "fetch_store_offers", side_effect=fake_fetch_cycle2), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp down")), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls_2.append(o)):
            result2 = ik.run_ikea_check_cycle()

        # Cykl kontynuuje ze sklepem 999 (kod 0 gdy e-mail jest juz wylaczony),
        # ale UUID ze sklepu A MUSI przetrwac w pending bez zmian
        self.assertEqual(result2, 0)
        state_after_2 = ik.load_seen_state()
        self.assertIn(uuid_sklep_a, state_after_2["pending"],
                      "UUID ze sklepu A NIE moze byc usuniety z pending gdy sklep byl niedostepny")
        self.assertNotIn(uuid_sklep_a, state_after_2["seen"],
                         "UUID ze sklepu A NIE moze zostac awansowany do seen, skoro sklep padl")
        # Telegram nie powinien dostac duplikatu (nadal jest w pending jako dostarczony)
        self.assertEqual(len(tg_calls_2), 0,
                         "Telegram NIE powinien ponawiac UUID ktory juz dostarczyl")


    def test_state_bug_promotion_without_new_offers(self):
        """Regresja: Jeśli oferta jest w pending i wyłączymy niedziałający kanał,
        to w kolejnym cyklu oferta ta musi zostać w pełni awansowana do seen
        (i usunięta z pending), NAWET jeśli w tym cyklu nie ma żadnych nowych ofert
        do wysłania."""
        product = self._fake_product("uuid-bug", "oferta")

        # Cykl 1: email i telegram włączone. Email pada, Telegram dostarcza.
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email", side_effect=RuntimeError("email fail")), \
             mock.patch.object(ik, "send_telegram"):
            result1 = ik.run_ikea_check_cycle()

        self.assertEqual(result1, 2)
        state1 = ik.load_seen_state()
        self.assertNotIn("uuid-bug", state1["seen"])
        self.assertEqual(state1["pending"].get("uuid-bug"), ["telegram"])

        # Cykl 2: użytkownik wyłącza email (SMTP_MODE=disabled).
        # Telegram to jedyny aktywny kanał i już dostarczył tę ofertę, więc nie ma co wysyłać.
        # Spodziewany wynik: uuid-bug trafia do seen, ORAZ znika z pending!
        tg_calls_2 = []
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email"), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls_2.append(o)):
            result2 = ik.run_ikea_check_cycle()

        self.assertEqual(result2, 0)
        self.assertEqual(len(tg_calls_2), 0, "Telegram nie powinien dostać duplikatu")

        state2 = ik.load_seen_state()
        self.assertIn("uuid-bug", state2["seen"], "UUID powinno trafić do seen po wyłączeniu blokującego kanału")
        self.assertNotIn("uuid-bug", state2["pending"], "UUID powinno ZNIKNĄĆ z pending po awansie do seen")

        # Cykl 3: upewnijmy się, że w kolejnym cyklu nadal nic się nie wysyła i stan pozostaje stabilny
        with mock.patch.object(ik, "fetch_store_offers", return_value=[product]), \
             mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "send_email"), \
             mock.patch.object(ik, "send_telegram", side_effect=lambda o: tg_calls_2.append(o)):
            result3 = ik.run_ikea_check_cycle()

        self.assertEqual(result3, 0)
        self.assertEqual(len(tg_calls_2), 0)
        state3 = ik.load_seen_state()
        self.assertIn("uuid-bug", state3["seen"])
        self.assertNotIn("uuid-bug", state3["pending"])


def _api_product(uuid="health-uuid", title="Stol drewniany"):
    """Produkt w formacie API (zgodny z kontrola formatu w fetch_store_offers())."""
    return {
        "title": title,
        "description": "opis",
        "originalPrice": 200,
        "articleNumbers": ["12345678"],
        "currency": "PLN",
        "storeId": "294",
        "heroImage": None,
        "offers": [{"offerUuid": uuid, "offerNumber": "1", "price": 100}],
    }


def _fetch_ok(store_id):
    return []


def _fetch_partial_404(store_id):
    if store_id == "100":
        raise RuntimeError("HTTP 404 (sklep 100, strona 0): not found")
    return []


def _fetch_all_404(store_id):
    raise RuntimeError(f"HTTP 404 (sklep {store_id}, strona 0): not found")


def _fetch_all_blocked(store_id):
    raise ik.BlockedByServerError(403, f"HTTP 403 (sklep {store_id}, strona 0): blocked")


class TestHealthAlert(unittest.TestCase):
    """Blok 'health' i alert o dluzszej awarii monitora: klasyfikacja
    wyniku cyklu, prog HEALTH_ALERT_AFTER_CYCLES, powiadomienie o powrocie,
    ponawianie po calkowitej porazce dostawy, migracja starego pliku.
    notify_status_message() jest zawsze zmockowane (bez SMTP/Telegrama)."""

    def setUp(self):
        self._orig = {
            "store_ids": list(ik.STORE_IDS),
            "terms": list(ik.SEARCH_TERMS),
            "numbers": list(ik.SEARCH_ARTICLE_NUMBERS),
            "threshold": ik.HEALTH_ALERT_AFTER_CYCLES,
            "health": ik.get_health_state(),
            "access": dict(ik.get_access_notification_state()),
            "backoff": ik.get_blocking_backoff_state().to_dict(),
        }
        ik.STORE_IDS = ["100", "200"]
        ik.SEARCH_TERMS = ["stol"]
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = []
        ik.HEALTH_ALERT_AFTER_CYCLES = 3
        ik.set_health_state(ik.default_health_state())
        ik.set_access_notification_state(False, None)
        ik.set_blocking_backoff_state(ik.BackoffState())
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)
        _save_seen(set())  # nie-first-run

        for patcher in (
            mock.patch.object(ik.time, "sleep"),
            mock.patch.object(ik, "EMAIL_ENABLED", True),
            mock.patch.object(ik, "TELEGRAM_ENABLED", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        notify_patcher = mock.patch.object(ik, "notify_status_message", return_value=[])
        self.notify = notify_patcher.start()
        self.addCleanup(notify_patcher.stop)

    def tearDown(self):
        ik.STORE_IDS = self._orig["store_ids"]
        ik.SEARCH_TERMS = self._orig["terms"]
        ik.refresh_normalized_terms()
        ik.SEARCH_ARTICLE_NUMBERS = self._orig["numbers"]
        ik.HEALTH_ALERT_AFTER_CYCLES = self._orig["threshold"]
        ik.set_health_state(self._orig["health"])
        access = self._orig["access"]
        ik.set_access_notification_state(
            access.get("outage_active", False),
            access.get("last_status_code"),
            pending_outage=access.get("pending_outage", False),
        )
        ik.set_blocking_backoff_state(ik.BackoffState.from_dict(self._orig["backoff"]))
        if os.path.exists(ik.STATE_FILE):
            os.remove(ik.STATE_FILE)

    def _run(self, fetch):
        with mock.patch.object(ik, "fetch_store_offers", side_effect=fetch):
            return ik.run_ikea_check_cycle()

    def _preset_health(self):
        preset = {
            "consecutive_failed_cycles": 2,
            "alert_active": False,
            "last_error": "poprzedni blad",
            "last_successful_check_at": 123.0,
        }
        ik.set_health_state(preset)
        return preset

    def _health_subjects(self):
        return [call.args[0] for call in self.notify.call_args_list]

    # --- klasyfikacja: SKIPPED (blok 'health' bez zmian) ---

    def test_skipped_when_no_search_criteria(self):
        preset = self._preset_health()
        ik.SEARCH_TERMS = []
        ik.refresh_normalized_terms()
        with mock.patch.object(ik, "fetch_all_offers") as mock_fetch:
            result = ik.run_ikea_check_cycle()
        self.assertEqual(result, 0)
        mock_fetch.assert_not_called()
        self.assertEqual(ik.get_health_state(), preset)

    def test_skipped_when_no_store_ids(self):
        preset = self._preset_health()
        ik.STORE_IDS = []
        result = self._run(_fetch_ok)
        self.assertEqual(result, 0)
        self.assertEqual(ik.get_health_state(), preset)

    def test_skipped_when_no_store_ids_even_with_seen_state_error(self):
        preset = self._preset_health()
        ik.STORE_IDS = []
        with open(ik.STATE_FILE, "w", encoding="utf-8") as f:
            f.write("{uszkodzony")
        result = self._run(_fetch_ok)
        self.assertEqual(result, 1)
        self.assertEqual(ik.get_health_state(), preset)

    def test_skipped_when_all_stores_blocked_by_403_429(self):
        preset = self._preset_health()
        ik.HEALTH_ALERT_AFTER_CYCLES = 1
        result = self._run(_fetch_all_blocked)
        self.assertEqual(result, 1)
        self.assertEqual(ik.get_health_state(), preset)
        self.assertNotIn(ik.HEALTH_ALERT_EMAIL_SUBJECT, self._health_subjects())

    # --- klasyfikacja: HEALTHY ---

    def test_healthy_full_success_resets_counter_and_records_time(self):
        self._preset_health()
        before = time.time()
        result = self._run(_fetch_ok)
        self.assertEqual(result, 0)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 0)
        self.assertIsNone(health["last_error"])
        self.assertGreaterEqual(health["last_successful_check_at"], before)
        self.notify.assert_not_called()

    def test_healthy_on_first_run(self):
        os.remove(ik.STATE_FILE)
        self._preset_health()
        result = self._run(_fetch_ok)
        self.assertEqual(result, 0)
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 0)

    def test_channel_delivery_failure_does_not_make_cycle_unhealthy(self):
        self._preset_health()
        with mock.patch.object(ik, "send_email", side_effect=RuntimeError("smtp down")):
            result = self._run(lambda store_id: [_api_product()] if store_id == "100" else [])
        self.assertEqual(result, 2)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 0)
        self.assertIsNone(health["last_error"])

    # --- klasyfikacja: UNHEALTHY ---

    def test_unhealthy_partial_store_error(self):
        result = self._run(_fetch_partial_404)
        self.assertEqual(result, 0)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 1)
        self.assertIn("sklep 100", health["last_error"])
        self.assertIn("HTTP 404", health["last_error"])

    def test_unhealthy_partial_403_with_other_store_ok(self):
        def fetch(store_id):
            if store_id == "100":
                raise ik.BlockedByServerError(403, "HTTP 403 (sklep 100, strona 0): blocked")
            return []

        result = self._run(fetch)
        self.assertEqual(result, 0)
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 1)

    def test_unhealthy_all_stores_non_blocking_error(self):
        result = self._run(_fetch_all_404)
        self.assertEqual(result, 1)
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 1)

    def test_unhealthy_mixed_403_and_timeout(self):
        def fetch(store_id):
            if store_id == "100":
                raise ik.BlockedByServerError(403, "HTTP 403")
            raise TimeoutError()

        result = self._run(fetch)
        self.assertEqual(result, 1)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 1)
        self.assertIn("sklep 200: TimeoutError", health["last_error"])

    def test_unhealthy_when_fetch_all_offers_raises(self):
        with mock.patch.object(ik, "fetch_all_offers", side_effect=RuntimeError("boom")):
            result = ik.run_ikea_check_cycle()
        self.assertEqual(result, 1)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 1)
        self.assertIn("blad zapytania do API", health["last_error"])

    def test_unhealthy_on_corrupted_seen_state(self):
        with open(ik.STATE_FILE, "w", encoding="utf-8") as f:
            f.write("{uszkodzony")
        result = self._run(_fetch_ok)
        self.assertEqual(result, 1)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 1)
        self.assertTrue(health["last_error"].startswith("plik stanu (odczyt)"), health["last_error"])

    def test_unhealthy_on_seen_state_with_wrong_structure(self):
        with open(ik.STATE_FILE, "w", encoding="utf-8") as f:
            f.write("[]")
        result = self._run(_fetch_ok)
        self.assertEqual(result, 1)
        self.assertIn("plik stanu (odczyt)", ik.get_health_state()["last_error"])

    def test_unhealthy_on_seen_state_write_error(self):
        with mock.patch.object(ik, "save_seen_state", side_effect=OSError("No space left on device")):
            result = self._run(_fetch_ok)
        self.assertEqual(result, 3)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 1)
        self.assertIn("plik stanu (zapis)", health["last_error"])
        self.assertIn("No space left", health["last_error"])

    def test_unhealthy_on_first_run_seen_state_write_error(self):
        os.remove(ik.STATE_FILE)
        with mock.patch.object(ik, "save_seen_state", side_effect=OSError("read-only")):
            result = self._run(_fetch_ok)
        self.assertEqual(result, 3)
        self.assertIn("plik stanu (zapis)", ik.get_health_state()["last_error"])

    def test_partial_store_error_and_seen_state_error_are_combined(self):
        with mock.patch.object(ik, "save_seen_state", side_effect=OSError("read-only")):
            result = self._run(_fetch_partial_404)
        self.assertEqual(result, 3)
        last_error = ik.get_health_state()["last_error"]
        self.assertIn("sklep 100", last_error)
        self.assertIn("plik stanu (zapis)", last_error)

    def test_unexpected_exception_is_caught_and_counted(self):
        with mock.patch.object(ik, "flatten_matching_offers", side_effect=KeyError("brak")), \
             mock.patch.object(ik, "log") as mock_log:
            result = self._run(_fetch_ok)
        self.assertEqual(result, 1)
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 1)
        self.assertIn("KeyError", health["last_error"])
        messages = " ".join(str(call.args[0]) for call in mock_log.call_args_list)
        self.assertIn("Nieoczekiwany blad", messages)

    def test_health_update_failure_does_not_break_cycle(self):
        with mock.patch.object(ik, "update_health_state", side_effect=OSError("disk")):
            result = self._run(_fetch_ok)
        self.assertEqual(result, 0)

    def test_daemon_loop_survives_unexpected_cycle_exception(self):
        calls = {"n": 0}

        def failing_cycle_body():
            calls["n"] += 1
            if calls["n"] >= 2:
                ik.SHUTDOWN_EVENT.set()
            raise RuntimeError("nieoczekiwany")

        ik.SHUTDOWN_EVENT.clear()
        self.addCleanup(ik.SHUTDOWN_EVENT.clear)
        with mock.patch.object(ik, "_run_check_cycle", side_effect=failing_cycle_body), \
             mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 0), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", return_value=False):
            result = ik.run_daemon()
        self.assertEqual(result, 0)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 2)

    def test_last_error_is_sanitized_and_truncated(self):
        ik.TELEGRAM_BOT_TOKEN, orig_token = "123456:SEKRETNY-TOKEN", ik.TELEGRAM_BOT_TOKEN
        self.addCleanup(setattr, ik, "TELEGRAM_BOT_TOKEN", orig_token)

        def fetch(store_id):
            raise RuntimeError(
                "HTTP 404 (sklep 100, strona 0): <html>\n https://web-api.ikea.com/x?y=1 "
                "token 123456:SEKRETNY-TOKEN " + "x" * 500
            )

        self._run(fetch)
        last_error = ik.get_health_state()["last_error"]
        self.assertLessEqual(len(last_error), ik.HEALTH_LAST_ERROR_MAX_CHARS)
        self.assertNotIn("https://", last_error)
        self.assertNotIn("SEKRETNY-TOKEN", last_error)
        self.assertNotIn("\n", last_error)
        self.assertTrue(last_error.endswith("..."))

    # --- prog i jednorazowosc alertu ---

    def test_alert_sent_exactly_once_at_threshold(self):
        for _ in range(2):
            self._run(_fetch_partial_404)
        self.notify.assert_not_called()
        self.assertFalse(ik.get_health_state()["alert_active"])

        self._run(_fetch_partial_404)
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.args[0], ik.HEALTH_ALERT_EMAIL_SUBJECT)
        self.assertTrue(ik.get_health_state()["alert_active"])

        for _ in range(3):
            self._run(_fetch_all_404)
        self.notify.assert_called_once()
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 6)

    def test_alert_message_content(self):
        ik.set_health_state({
            "consecutive_failed_cycles": 2,
            "alert_active": False,
            "last_error": None,
            "last_successful_check_at": None,
        })
        self._run(_fetch_partial_404)
        subject, body, telegram_text = self.notify.call_args.args
        self.assertEqual(subject, "IKEA Okazje: monitor nie dziala poprawnie")
        self.assertIn("Kolejne nieudane cykle: 3", body)
        self.assertIn("Ostatnie udane sprawdzenie: nigdy", body)
        self.assertIn("sklep 100: HTTP 404", body)
        self.assertIn("journalctl -u ikea-okazje", body)
        self.assertIn("sklep 100: HTTP 404", telegram_text)

    def test_alert_message_shows_last_success_in_local_time(self):
        last_ok = time.time() - 3600
        message = ik.build_health_alert_message({
            "consecutive_failed_cycles": 3,
            "alert_active": False,
            "last_error": "plik stanu (odczyt): boom",
            "last_successful_check_at": last_ok,
        })
        expected = ik.datetime.fromtimestamp(last_ok).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIn(f"Ostatnie udane sprawdzenie: {expected}", message)
        self.assertIn("plik stanu (odczyt): boom", message)

    def test_alert_telegram_text_is_html_escaped(self):
        ik.HEALTH_ALERT_AFTER_CYCLES = 1

        def fetch(store_id):
            raise RuntimeError("HTTP 404: <html><body>Not Found & gone</body></html>")

        self._run(fetch)
        telegram_text = self.notify.call_args.args[2]
        self.assertNotIn("<html>", telegram_text)
        self.assertIn("&lt;html&gt;", telegram_text)
        self.assertIn("&amp;", telegram_text)

    def test_skipped_cycles_do_not_reset_counter(self):
        self._run(_fetch_partial_404)
        self._run(_fetch_partial_404)
        self._run(_fetch_all_blocked)
        self.notify.assert_not_called()
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 2)
        self._run(_fetch_partial_404)
        self.assertIn(ik.HEALTH_ALERT_EMAIL_SUBJECT, self._health_subjects())

    def test_healthy_cycle_resets_counter_before_threshold(self):
        self._run(_fetch_partial_404)
        self._run(_fetch_partial_404)
        self._run(_fetch_ok)
        self._run(_fetch_partial_404)
        self._run(_fetch_partial_404)
        self.notify.assert_not_called()
        self.assertEqual(ik.get_health_state()["consecutive_failed_cycles"], 2)

    def test_threshold_zero_tracks_state_without_alert(self):
        ik.HEALTH_ALERT_AFTER_CYCLES = 0
        for _ in range(5):
            self._run(_fetch_partial_404)
        self.notify.assert_not_called()
        health = ik.get_health_state()
        self.assertEqual(health["consecutive_failed_cycles"], 5)
        self.assertFalse(health["alert_active"])
        self.assertIn("sklep 100", health["last_error"])

    # --- powiadomienie o powrocie ---

    def test_recovery_message_sent_exactly_once(self):
        for _ in range(3):
            self._run(_fetch_partial_404)
        self.notify.reset_mock()

        self._run(_fetch_ok)
        self.notify.assert_called_once()
        subject, body, telegram_text = self.notify.call_args.args
        self.assertEqual(subject, ik.HEALTH_RECOVERY_EMAIL_SUBJECT)
        self.assertIn("znow poprawnie", body)
        self.assertFalse(ik.get_health_state()["alert_active"])

        self._run(_fetch_ok)
        self.notify.assert_called_once()

    def test_no_recovery_message_without_prior_alert(self):
        self._run(_fetch_partial_404)
        self._run(_fetch_ok)
        self.notify.assert_not_called()

    def test_new_failure_series_after_recovery_alerts_again(self):
        for _ in range(3):
            self._run(_fetch_partial_404)
        self._run(_fetch_ok)
        self.notify.reset_mock()
        for _ in range(3):
            self._run(_fetch_partial_404)
        self.assertEqual(self._health_subjects(), [ik.HEALTH_ALERT_EMAIL_SUBJECT])

    # --- calkowita porazka dostawy ---

    def test_alert_delivery_total_failure_retried_next_unhealthy_cycle(self):
        for _ in range(2):
            self._run(_fetch_partial_404)
        self.notify.return_value = ["e-mail: smtp down"]
        self._run(_fetch_partial_404)
        self.assertEqual(self.notify.call_count, 1)
        self.assertFalse(ik.get_health_state()["alert_active"])

        self.notify.return_value = []
        self._run(_fetch_partial_404)
        self.assertEqual(self.notify.call_count, 2)
        self.assertTrue(ik.get_health_state()["alert_active"])

        self._run(_fetch_partial_404)
        self.assertEqual(self.notify.call_count, 2)

    def test_alert_partial_delivery_failure_counts_as_delivered(self):
        ik.HEALTH_ALERT_AFTER_CYCLES = 1
        self.notify.return_value = ["e-mail: smtp down"]
        with mock.patch.object(ik, "TELEGRAM_ENABLED", True):
            self._run(_fetch_partial_404)
        self.assertTrue(ik.get_health_state()["alert_active"])

    def test_recovery_delivery_total_failure_retried_next_healthy_cycle(self):
        for _ in range(3):
            self._run(_fetch_partial_404)
        self.notify.reset_mock()

        self.notify.return_value = ["e-mail: smtp down"]
        self._run(_fetch_ok)
        self.assertEqual(self.notify.call_count, 1)
        health = ik.get_health_state()
        self.assertTrue(health["alert_active"])
        self.assertEqual(health["consecutive_failed_cycles"], 0)

        self.notify.return_value = []
        self._run(_fetch_ok)
        self.assertEqual(self.notify.call_count, 2)
        self.assertFalse(ik.get_health_state()["alert_active"])

    # --- zapis i migracja stanu ---

    def test_update_health_state_saves_once_per_cycle(self):
        ik.set_health_state({
            "consecutive_failed_cycles": 2,
            "alert_active": False,
            "last_error": None,
            "last_successful_check_at": None,
        })
        with mock.patch.object(ik, "save_dynamic_state") as mock_save:
            ik.update_health_state(ik.CYCLE_UNHEALTHY, "sklep 100: HTTP 404")
        mock_save.assert_called_once()
        self.notify.assert_called_once()

        with mock.patch.object(ik, "save_dynamic_state") as mock_save:
            ik.update_health_state(ik.CYCLE_HEALTHY)
        mock_save.assert_called_once()

        with mock.patch.object(ik, "save_dynamic_state") as mock_save:
            ik.update_health_state(ik.CYCLE_SKIPPED)
        mock_save.assert_not_called()

    def test_health_state_persisted_and_survives_reload(self):
        for _ in range(3):
            self._run(_fetch_partial_404)
        reloaded = ik.load_dynamic_state()
        self.assertEqual(reloaded["health"]["consecutive_failed_cycles"], 3)
        self.assertTrue(reloaded["health"]["alert_active"])

        # "Restart": stan z dysku zastepuje pamiec - bez duplikatu alertu.
        ik.DYNAMIC_STATE.clear()
        ik.DYNAMIC_STATE.update(reloaded)
        self.notify.reset_mock()
        self._run(_fetch_partial_404)
        self.notify.assert_not_called()

    def test_old_dynamic_state_file_without_health_migrates(self):
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("health", None)
        with open(ik.DYNAMIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reloaded = ik.load_dynamic_state()
        self.assertEqual(reloaded["health"], ik.default_health_state())
        self.assertEqual(reloaded["store_ids"], data["store_ids"])
        self.assertEqual(reloaded["blocking_backoff"], data["blocking_backoff"])
        with open(ik.DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            persisted = json.load(f)
        self.assertEqual(persisted["health"], ik.default_health_state())

    def test_invalid_health_values_are_tolerated(self):
        migrated = ik.migrate_health_state({
            "consecutive_failed_cycles": "abc",
            "alert_active": "false",
            "last_error": 5,
            "last_successful_check_at": "wczoraj",
        })
        self.assertEqual(migrated, ik.default_health_state())
        self.assertEqual(ik.migrate_health_state(None), ik.default_health_state())
        self.assertEqual(ik.migrate_health_state([1, 2]), ik.default_health_state())
        self.assertEqual(
            ik.migrate_health_state({"consecutive_failed_cycles": -4})["consecutive_failed_cycles"], 0
        )
        valid = {
            "consecutive_failed_cycles": 4,
            "alert_active": True,
            "last_error": "x",
            "last_successful_check_at": 1700000000.5,
        }
        self.assertEqual(ik.migrate_health_state(valid), valid)


class TestHealthAlertConfig(unittest.TestCase):
    """HEALTH_ALERT_AFTER_CYCLES: liczba calkowita >= 0, domyslnie 3."""

    def test_parse_values(self):
        parse = ik.parse_non_negative_int
        key = "HEALTH_ALERT_AFTER_CYCLES"
        self.assertEqual(parse(None, key, 3), 3)
        self.assertEqual(parse(" ", key, 3), 3)
        self.assertEqual(parse("0", key, 3), 0)
        self.assertEqual(parse(" 5 ", key, 3), 5)

    def test_parse_invalid_values_raise(self):
        for raw in ("-1", "abc", "2.5"):
            with self.subTest(raw=raw):
                with self.assertRaises(RuntimeError) as ctx:
                    ik.parse_non_negative_int(raw, "HEALTH_ALERT_AFTER_CYCLES", 3)
                self.assertIn("HEALTH_ALERT_AFTER_CYCLES", str(ctx.exception))
                self.assertIn(ik.CONFIG_FILE, str(ctx.exception))

    def test_default_threshold_at_startup(self):
        self.assertEqual(ik.DEFAULT_HEALTH_ALERT_AFTER_CYCLES, 3)
        home_dir = _make_isolated_home("SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n")
        result = _run_python_code(
            "import ikea_okazje as ik\nik.initialize_runtime()\n"
            "assert ik.HEALTH_ALERT_AFTER_CYCLES == 3, ik.HEALTH_ALERT_AFTER_CYCLES\n"
            "assert ik.DYNAMIC_STATE['health'] == ik.default_health_state()\nprint('OK')\n",
            home_dir,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_invalid_threshold_fails_at_startup(self):
        result = _run_module_with_env(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nHEALTH_ALERT_AFTER_CYCLES=trzy\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HEALTH_ALERT_AFTER_CYCLES", result.stderr)


def _api_page(content, last=False, total_pages=None):
    page = {"content": content, "last": last}
    if total_pages is not None:
        page["totalPages"] = total_pages
    return page


class TestApiSchemaSanityCheck(unittest.TestCase):
    """fetch_store_offers(): zmiana formatu odpowiedzi API jest bledem
    sklepu, a dojscie do MAX_PAGES daje ostrzezenie."""

    def setUp(self):
        dump_patcher = mock.patch.object(ik, "dump_raw")
        self.dump_raw = dump_patcher.start()
        self.addCleanup(dump_patcher.stop)

    def test_valid_page_passes(self):
        page = _api_page([_api_product("a"), _api_product("b")], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page):
            content = ik.fetch_store_offers("294")
        self.assertEqual([p["offers"][0]["offerUuid"] for p in content], ["a", "b"])

    def test_empty_content_passes(self):
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=_api_page([], last=True)):
            self.assertEqual(ik.fetch_store_offers("294"), [])

    def test_page_with_at_least_one_valid_product_passes(self):
        page = _api_page([{"promo": True}, _api_product("a")], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page):
            self.assertEqual(len(ik.fetch_store_offers("294")), 2)

    def test_renamed_fields_raise_with_store_page_and_keys(self):
        renamed = {"name": "Stol", "items": [], **{f"pole{i}": i for i in range(15)}}
        page = _api_page([renamed], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page):
            with self.assertRaises(RuntimeError) as ctx:
                ik.fetch_store_offers("294")
        message = str(ctx.exception)
        self.assertIn("Nieoczekiwany format odpowiedzi API", message)
        self.assertIn("sklep 294", message)
        self.assertIn("strona 0", message)
        self.assertIn("'name'", message)
        self.assertIn("'items'", message)
        self.assertIn("'pole7'", message)
        self.assertNotIn("'pole8'", message)  # max 10 kluczy

    def test_offers_not_a_list_raises(self):
        page = _api_page([{"title": "Stol", "offers": {"a": 1}}], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page):
            with self.assertRaises(RuntimeError):
                ik.fetch_store_offers("294")

    def test_non_dict_elements_raise_with_type_name(self):
        page = _api_page(["tekst", "inny"], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page):
            with self.assertRaises(RuntimeError) as ctx:
                ik.fetch_store_offers("294")
        self.assertIn("str", str(ctx.exception))

    def test_schema_error_on_later_page_names_that_page(self):
        pages = [
            _api_page([_api_product(f"u{i}") for i in range(int(ik.PAGE_SIZE))], total_pages=2),
            _api_page([{"name": "x"}], last=True),
        ]
        with mock.patch.object(ik, "fetch_page_with_retry", side_effect=pages):
            with self.assertRaises(RuntimeError) as ctx:
                ik.fetch_store_offers("294")
        self.assertIn("strona 1", str(ctx.exception))

    def test_raw_dump_written_before_schema_error(self):
        page = _api_page([{"name": "x"}], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page):
            with self.assertRaises(RuntimeError):
                ik.fetch_store_offers("294")
        self.dump_raw.assert_called_once()

    def test_schema_error_becomes_store_error_in_cycle(self):
        page = _api_page([{"name": "x"}], last=True)
        with mock.patch.object(ik, "fetch_page_with_retry", return_value=page), \
             mock.patch.object(ik, "STORE_IDS", ["294"]), \
             mock.patch.object(ik.time, "sleep"):
            content, store_errors = ik.fetch_all_offers()
        self.assertEqual(content, [])
        self.assertIn("Nieoczekiwany format", str(store_errors["294"]))

    def _full_page(self, **kwargs):
        return _api_page([_api_product(f"u{i}") for i in range(int(ik.PAGE_SIZE))], **kwargs)

    def test_warning_when_max_pages_reached_with_more_pages(self):
        with mock.patch.object(ik, "MAX_PAGES", 2), \
             mock.patch.object(ik, "fetch_page_with_retry", return_value=self._full_page(total_pages=5)) as mock_fetch, \
             mock.patch.object(ik, "log") as mock_log:
            content = ik.fetch_store_offers("294")
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(len(content), 2 * int(ik.PAGE_SIZE))
        mock_log.assert_called_once()
        self.assertIn("MAX_PAGES", mock_log.call_args.args[0])
        self.assertIn("sklep 294", mock_log.call_args.args[0])
        self.assertTrue(mock_log.call_args.kwargs.get("to_stderr"))

    def test_warning_when_max_pages_reached_without_total_pages(self):
        with mock.patch.object(ik, "MAX_PAGES", 2), \
             mock.patch.object(ik, "fetch_page_with_retry", return_value=self._full_page()), \
             mock.patch.object(ik, "log") as mock_log:
            ik.fetch_store_offers("294")
        mock_log.assert_called_once()

    def test_no_warning_when_last_page_within_limit(self):
        pages = [self._full_page(total_pages=2), self._full_page(total_pages=2)]
        with mock.patch.object(ik, "MAX_PAGES", 2), \
             mock.patch.object(ik, "fetch_page_with_retry", side_effect=pages), \
             mock.patch.object(ik, "log") as mock_log:
            ik.fetch_store_offers("294")
        mock_log.assert_not_called()

    def test_no_warning_when_last_flag_set_on_final_page(self):
        pages = [self._full_page(), self._full_page(last=True)]
        with mock.patch.object(ik, "MAX_PAGES", 2), \
             mock.patch.object(ik, "fetch_page_with_retry", side_effect=pages), \
             mock.patch.object(ik, "log") as mock_log:
            ik.fetch_store_offers("294")
        mock_log.assert_not_called()


class TestTelegramCommandParsing(unittest.TestCase):
    """handle_telegram_updates(): komendy z @nazwa_bota i pomijanie
    edytowanych wiadomosci. API Telegrama i plik offsetu sa zmockowane."""

    CHAT_ID = "4242"

    def _message(self, update_id, text, key="message", chat_id=None):
        chat = int(chat_id or self.CHAT_ID)
        return {"update_id": update_id, key: {"chat": {"id": chat}, "text": text}}

    def _handle(self, updates, offset=100):
        with mock.patch.object(ik, "TELEGRAM_CHAT_ID", self.CHAT_ID), \
             mock.patch.object(ik, "load_telegram_offset", return_value=offset), \
             mock.patch.object(ik, "telegram_get_updates", return_value=updates), \
             mock.patch.object(ik, "save_telegram_offset") as save_offset, \
             mock.patch.object(ik, "handle_command", return_value="ok") as handle_command, \
             mock.patch.object(ik, "telegram_send_message") as send:
            ik.handle_telegram_updates()
        return handle_command, send, save_offset

    def test_bot_name_suffix_is_stripped(self):
        handle_command, send, _ = self._handle([self._message(100, "/status@MojBot")])
        handle_command.assert_called_once_with("/status", "")
        send.assert_called_once()

    def test_bot_name_suffix_with_argument_and_upper_case_command(self):
        handle_command, _, _ = self._handle([self._message(100, "/DODAJ@Moj_Bot  stall nowy")])
        handle_command.assert_called_once_with("/dodaj", "stall nowy")

    def test_plain_command_unchanged(self):
        handle_command, _, _ = self._handle([self._message(100, "/usun stall")])
        handle_command.assert_called_once_with("/usun", "stall")

    def test_edited_message_is_ignored_but_offset_advances(self):
        handle_command, send, save_offset = self._handle(
            [self._message(105, "/usunsklep 294", key="edited_message")]
        )
        handle_command.assert_not_called()
        send.assert_not_called()
        save_offset.assert_called_once_with(106)

    def test_only_new_messages_executed_in_mixed_batch(self):
        updates = [
            self._message(100, "/status"),
            self._message(101, "/dodaj stall", key="edited_message"),
            self._message(102, "/pomoc@MojBot"),
        ]
        handle_command, _, save_offset = self._handle(updates)
        self.assertEqual(
            [call.args for call in handle_command.call_args_list],
            [("/status", ""), ("/pomoc", "")],
        )
        save_offset.assert_called_once_with(103)

    def test_bot_name_suffix_from_other_chat_still_ignored(self):
        handle_command, send, save_offset = self._handle(
            [self._message(100, "/status@MojBot", chat_id="999")]
        )
        handle_command.assert_not_called()
        send.assert_not_called()
        save_offset.assert_called_once_with(101)


_TEST_BOT_TOKEN = "123456:SEKRETNY-TOKEN"


class _FakeTelegramResponse:
    """Odpowiedz urlopen() jako context manager."""

    def __init__(self, payload=None, raw=None):
        self._body = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _telegram_ok(result=True):
    return _FakeTelegramResponse({"ok": True, "result": result})


def _telegram_http_error(code, description=None, retry_after=None, raw=None, method="sendMessage"):
    """HTTPError jak z urllib - z URL zawierajacym token w .url i str()."""
    if raw is None:
        body = {"ok": False, "error_code": code}
        if description is not None:
            body["description"] = description
        if retry_after is not None:
            body["parameters"] = {"retry_after": retry_after}
        raw = json.dumps(body).encode("utf-8")
    url = f"https://api.telegram.org/bot{_TEST_BOT_TOKEN}/{method}"
    return ik.urllib.error.HTTPError(url, code, "Error", {}, io.BytesIO(raw))


class TestTelegramApiErrors(unittest.TestCase):
    """telegram_send_message()/telegram_get_updates(): opis bledu od
    Telegrama, 429 z jednym ponowieniem, 409, bledy sieci - bez URL i
    tokenu w komunikacie. urlopen i SHUTDOWN_EVENT.wait sa zmockowane."""

    def setUp(self):
        for patcher in (
            mock.patch.object(ik, "TELEGRAM_BOT_TOKEN", _TEST_BOT_TOKEN),
            mock.patch.object(ik, "TELEGRAM_CHAT_ID", "4242"),
            mock.patch.object(ik, "log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        wait_patcher = mock.patch.object(ik.SHUTDOWN_EVENT, "wait", return_value=False)
        self.wait = wait_patcher.start()
        self.addCleanup(wait_patcher.stop)

    def _urlopen(self, *effects):
        patcher = mock.patch.object(ik.urllib.request, "urlopen", side_effect=list(effects))
        urlopen = patcher.start()
        self.addCleanup(patcher.stop)
        return urlopen

    def _assert_no_secret(self, text):
        self.assertNotIn("SEKRETNY-TOKEN", text)
        self.assertNotIn("api.telegram.org", text)
        self.assertNotIn("https://", text)

    def test_send_message_posts_json_with_token_in_url(self):
        urlopen = self._urlopen(_telegram_ok())
        ik.telegram_send_message("<b>hej</b>", chat_id=7)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"https://api.telegram.org/bot{_TEST_BOT_TOKEN}/sendMessage")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], 7)
        self.assertEqual(payload["text"], "<b>hej</b>")
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], ik.REQUEST_TIMEOUT)

    def test_bad_request_reports_code_and_description_without_url(self):
        self._urlopen(_telegram_http_error(400, "Bad Request: can't parse entities"))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("<b>zle")
        message = str(ctx.exception)
        self.assertIn("HTTP 400", message)
        self.assertIn("Bad Request: can't parse entities", message)
        self._assert_no_secret(message)
        self.assertIsNone(ctx.exception.__cause__)
        self.wait.assert_not_called()

    def test_error_without_json_body_reports_code(self):
        self._urlopen(_telegram_http_error(502, raw=b"<html>Bad Gateway</html>"))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        self.assertIn("HTTP 502", str(ctx.exception))
        self._assert_no_secret(str(ctx.exception))

    def test_description_with_token_is_masked(self):
        self._urlopen(_telegram_http_error(401, f"Unauthorized {_TEST_BOT_TOKEN}"))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        self.assertIn("HTTP 401", str(ctx.exception))
        self._assert_no_secret(str(ctx.exception))

    def test_network_error_without_url(self):
        self._urlopen(ik.urllib.error.URLError(OSError(-2, "Name or service not known")))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        message = str(ctx.exception)
        self.assertIn("Blad polaczenia z Telegram API", message)
        self.assertIn("Name or service not known", message)
        self._assert_no_secret(message)

    def test_timeout_is_reported_as_network_error(self):
        self._urlopen(TimeoutError("The read operation timed out"))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        self.assertIn("timed out", str(ctx.exception))

    def test_ok_false_in_200_response_raises(self):
        self._urlopen(_FakeTelegramResponse({"ok": False, "description": "dziwne"}))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        self.assertIn("dziwne", str(ctx.exception))

    def test_429_waits_retry_after_and_retries_once(self):
        urlopen = self._urlopen(
            _telegram_http_error(429, "Too Many Requests: retry after 7", retry_after=7),
            _telegram_ok(),
        )
        ik.telegram_send_message("x")
        self.assertEqual(urlopen.call_count, 2)
        self.wait.assert_called_once_with(7.0)
        first = json.loads(urlopen.call_args_list[0].args[0].data.decode("utf-8"))
        second = json.loads(urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertEqual(first, second)

    def test_429_without_retry_after_waits_default(self):
        self._urlopen(_telegram_http_error(429, "Too Many Requests"), _telegram_ok())
        ik.telegram_send_message("x")
        self.wait.assert_called_once_with(ik.TELEGRAM_RATE_LIMIT_DEFAULT_WAIT_SECONDS)

    def test_429_wait_is_capped(self):
        self._urlopen(_telegram_http_error(429, "Too Many Requests", retry_after=600), _telegram_ok())
        ik.telegram_send_message("x")
        self.wait.assert_called_once_with(ik.TELEGRAM_RATE_LIMIT_MAX_WAIT_SECONDS)

    def test_second_429_raises_after_single_retry(self):
        urlopen = self._urlopen(
            _telegram_http_error(429, "Too Many Requests: retry after 3", retry_after=3),
            _telegram_http_error(429, "Too Many Requests: retry after 3", retry_after=3),
        )
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        self.assertEqual(urlopen.call_count, 2)
        self.wait.assert_called_once_with(3.0)
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertIn("Too Many Requests", str(ctx.exception))
        self._assert_no_secret(str(ctx.exception))

    def test_429_not_retried_when_shutdown_requested_during_wait(self):
        self.wait.return_value = True
        urlopen = self._urlopen(_telegram_http_error(429, "Too Many Requests", retry_after=3))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_send_message("x")
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("HTTP 429", str(ctx.exception))

    def test_get_updates_returns_result_list(self):
        updates = [{"update_id": 5, "message": {"text": "/status"}}]
        urlopen = self._urlopen(_telegram_ok(updates))
        self.assertEqual(ik.telegram_get_updates(5), updates)
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/getUpdates"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["offset"], 5)

    def test_get_updates_error_has_description_without_url(self):
        self._urlopen(_telegram_http_error(401, "Unauthorized", method="getUpdates"))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_get_updates(0)
        message = str(ctx.exception)
        self.assertIn("HTTP 401", message)
        self.assertIn("Unauthorized", message)
        self._assert_no_secret(message)

    def test_get_updates_409_hints_second_instance_or_webhook(self):
        self._urlopen(_telegram_http_error(
            409,
            "Conflict: terminated by other getUpdates request; make sure that "
            "only one bot instance is running",
            method="getUpdates",
        ))
        with self.assertRaises(RuntimeError) as ctx:
            ik.telegram_get_updates(0)
        message = str(ctx.exception)
        self.assertIn("HTTP 409", message)
        self.assertIn("Conflict", message)
        self.assertIn("druga instancja", message)
        self.assertIn("webhook", message)
        self._assert_no_secret(message)

    def test_get_updates_429_is_not_retried(self):
        urlopen = self._urlopen(_telegram_http_error(429, "Too Many Requests", retry_after=3, method="getUpdates"))
        with self.assertRaises(RuntimeError):
            ik.telegram_get_updates(0)
        self.assertEqual(urlopen.call_count, 1)
        self.wait.assert_not_called()


class TestTelegramSeriesPacing(unittest.TestCase):
    """send_telegram(): przerwa TELEGRAM_SERIES_DELAY_SECONDS miedzy
    wiadomosciami serii (przez SHUTDOWN_EVENT.wait), bez przerwy przed
    pierwsza i po ostatniej."""

    def _series_offers(self):
        offers = [
            _make_offer(offer_uuid=str(i), offer_number=str(i), description="długi " * 100)
            for i in range(150)
        ]
        groups = ik.group_offers_for_notification(offers)
        count = len(ik.split_telegram_messages(groups))
        self.assertGreaterEqual(count, 3)
        return offers, count

    def _run(self, offers, send_side_effect=None):
        events = []

        def fake_send(text, **kwargs):
            events.append("send")
            if send_side_effect:
                send_side_effect(len([e for e in events if e == "send"]))

        def fake_wait(timeout):
            events.append(("wait", timeout))
            return False

        with mock.patch.object(ik, "telegram_send_message", side_effect=fake_send), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=fake_wait):
            ik.send_telegram(offers)
        return events

    def test_delay_between_messages_only(self):
        offers, count = self._series_offers()
        events = self._run(offers)
        expected = ["send"]
        for _ in range(count - 1):
            expected += [("wait", ik.TELEGRAM_SERIES_DELAY_SECONDS), "send"]
        self.assertEqual(events, expected)
        self.assertEqual(ik.TELEGRAM_SERIES_DELAY_SECONDS, 1.0)

    def test_single_message_has_no_delay(self):
        self.assertEqual(self._run([_make_offer()]), ["send"])

    def test_failure_mid_series_keeps_progress_report(self):
        offers, count = self._series_offers()

        def fail_second(send_number):
            if send_number == 2:
                raise RuntimeError("Telegram API (sendMessage) zwrocilo HTTP 400: Bad Request")

        with self.assertRaises(RuntimeError) as ctx:
            self._run(offers, send_side_effect=fail_second)
        message = str(ctx.exception)
        self.assertIn(f"wyslano 1/{count}", message)
        self.assertIn("#2", message)
        self.assertIn("HTTP 400", message)


def _image(n):
    return f"https://www.ikea.com/pl/pl/images/products/produkt-{n}.jpg"


class TestTelegramLinkPreview(unittest.TestCase):
    """Podglad linku: zdjecie pierwszej grupy w kazdej wiadomosci z ofertami
    (link_preview_options), bez podgladu w odpowiedziach na komendy i w
    komunikatach o stanie monitora."""

    def setUp(self):
        for patcher in (
            mock.patch.object(ik, "TELEGRAM_BOT_TOKEN", _TEST_BOT_TOKEN),
            mock.patch.object(ik, "TELEGRAM_CHAT_ID", "4242"),
            mock.patch.object(ik, "TELEGRAM_SERIES_DELAY_SECONDS", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _payload_for(self, **kwargs):
        with mock.patch.object(ik.urllib.request, "urlopen", return_value=_telegram_ok()) as urlopen:
            ik.telegram_send_message("tekst", **kwargs)
        return json.loads(urlopen.call_args.args[0].data.decode("utf-8"))

    def _first_title_number(self, text):
        return int(re.search(r"<b>Produkt (\d+)</b>", text).group(1))

    # --- wybor adresu zdjecia ---

    def test_preview_image_url_accepts_only_https_strings(self):
        url = _image(1)
        self.assertEqual(ik.telegram_preview_image_url({"hero_image": url}), url)
        for value in (None, "", "http://www.ikea.com/x.jpg", "/images/x.jpg",
                      "//www.ikea.com/x.jpg", 123, ["https://x"]):
            with self.subTest(value=value):
                self.assertIsNone(ik.telegram_preview_image_url({"hero_image": value}))
        self.assertIsNone(ik.telegram_preview_image_url({}))

    def test_api_hero_image_reaches_offer(self):
        product = {
            "title": "Stol", "description": "opis", "originalPrice": 200,
            "articleNumbers": ["12345678"], "currency": "PLN", "storeId": "294",
            "heroImage": _image(7),
            "offers": [{"offerUuid": "u1", "offerNumber": "1", "price": 100}],
        }
        with mock.patch.object(ik, "NORMALIZED_TERMS", ["stol"]), \
             mock.patch.object(ik, "NORMALIZED_EXCLUDE", []), \
             mock.patch.object(ik, "MIN_DISCOUNT_PERCENT", None), \
             mock.patch.object(ik, "MAX_PRICE", None):
            offers = ik.flatten_matching_offers([product])
        groups = ik.group_offers_for_notification(offers)
        self.assertEqual(ik.split_telegram_messages_with_previews(groups)[0][1], _image(7))

    # --- splitter ---

    def test_texts_identical_to_split_telegram_messages(self):
        fixtures = [
            [_make_offer(offer_uuid=str(i), offer_number=str(i), description="długi " * 100,
                         hero_image=_image(1)) for i in range(150)],
            [_make_offer(offer_uuid=str(i), title=f"Produkt {i}", hero_image=_image(i),
                         description="opis " * 200) for i in range(12)],
            [_make_offer()],
        ]
        for offers in fixtures:
            groups = ik.group_offers_for_notification(offers)
            pairs = ik.split_telegram_messages_with_previews(groups)
            self.assertEqual([text for text, _ in pairs], ik.split_telegram_messages(groups))
        self.assertEqual(ik.split_telegram_messages_with_previews([]), [])

    def test_single_message_uses_first_group_image(self):
        groups = ik.group_offers_for_notification([
            _make_offer(offer_uuid="a", title="Produkt 0", hero_image=_image(0)),
            _make_offer(offer_uuid="b", title="Produkt 1", hero_image=_image(1)),
        ])
        pairs = ik.split_telegram_messages_with_previews(groups)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][1], _image(0))

    def test_first_group_without_image_gives_no_preview(self):
        groups = ik.group_offers_for_notification([
            _make_offer(offer_uuid="a", title="Produkt 0", hero_image=None),
            _make_offer(offer_uuid="b", title="Produkt 1", hero_image=_image(1)),
        ])
        pairs = ik.split_telegram_messages_with_previews(groups)
        self.assertEqual(len(pairs), 1)
        self.assertIsNone(pairs[0][1])

    def test_each_message_gets_image_of_its_first_group(self):
        offers = [
            _make_offer(offer_uuid=str(i), title=f"Produkt {i}", hero_image=_image(i),
                        description="opis " * 200)
            for i in range(12)
        ]
        pairs = ik.split_telegram_messages_with_previews(ik.group_offers_for_notification(offers))
        self.assertGreater(len(pairs), 2)
        for text, preview in pairs:
            self.assertEqual(preview, _image(self._first_title_number(text)))

    def test_split_group_repeats_its_image_in_continuations(self):
        offers = [
            _make_offer(offer_uuid="x", title="Produkt 99", hero_image=_image(99)),
        ] + [
            _make_offer(offer_uuid=str(i), offer_number=f"8772470{i}", title="Produkt 5",
                        description="długi " * 100, hero_image=_image(5))
            for i in range(150)
        ]
        pairs = ik.split_telegram_messages_with_previews(ik.group_offers_for_notification(offers))
        self.assertGreater(len(pairs), 2)
        self.assertEqual(pairs[0][1], _image(99))
        continuation = [preview for text, preview in pairs if "(cd.)" in text]
        self.assertTrue(continuation)
        self.assertEqual(set(continuation), {_image(5)})

    # --- wysylka ---

    def test_send_telegram_passes_preview_per_message(self):
        offers = [
            _make_offer(offer_uuid=str(i), title=f"Produkt {i}",
                        hero_image=_image(i) if i % 2 == 0 else None,
                        description="opis " * 200)
            for i in range(12)
        ]
        expected = ik.split_telegram_messages_with_previews(ik.group_offers_for_notification(offers))
        with mock.patch.object(ik, "telegram_send_message") as send:
            ik.send_telegram(offers)
        sent = [(call.args[0], call.kwargs.get("preview_url")) for call in send.call_args_list]
        self.assertEqual(sent, expected)
        self.assertIn(None, [preview for _, preview in sent])

    def test_payload_with_preview_url(self):
        payload = self._payload_for(preview_url=_image(3))
        self.assertEqual(
            payload["link_preview_options"], {"url": _image(3), "prefer_large_media": True}
        )
        self.assertNotIn("disable_web_page_preview", payload)

    def test_payload_without_preview_keeps_default_link_preview(self):
        payload = self._payload_for()
        self.assertNotIn("link_preview_options", payload)
        self.assertNotIn("disable_web_page_preview", payload)

    def test_payload_with_disabled_preview(self):
        payload = self._payload_for(disable_preview=True, preview_url=_image(3))
        self.assertEqual(payload["link_preview_options"], {"is_disabled": True})

    def test_command_reply_has_preview_disabled(self):
        update = {"update_id": 10, "message": {"chat": {"id": 4242}, "text": "/pomoc"}}
        with mock.patch.object(ik, "load_telegram_offset", return_value=10), \
             mock.patch.object(ik, "telegram_get_updates", return_value=[update]), \
             mock.patch.object(ik, "save_telegram_offset"), \
             mock.patch.object(ik, "telegram_send_message") as send:
            ik.handle_telegram_updates()
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs.get("disable_preview"))
        self.assertEqual(str(send.call_args.kwargs.get("chat_id")), "4242")

    def test_status_message_has_preview_disabled(self):
        with mock.patch.object(ik, "EMAIL_ENABLED", False), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "telegram_send_message") as send:
            errors = ik.notify_status_message("temat", "tresc", "tresc telegram")
        self.assertEqual(errors, [])
        send.assert_called_once_with("tresc telegram", disable_preview=True)


class TestTelegramLongPollConfig(unittest.TestCase):
    """TELEGRAM_LONG_POLL_SECONDS: liczba calkowita 0..50, domyslnie 25."""

    def _parse(self, raw):
        return ik.parse_non_negative_int(
            raw, "TELEGRAM_LONG_POLL_SECONDS", ik.DEFAULT_TELEGRAM_LONG_POLL_SECONDS,
            max_value=ik.MAX_TELEGRAM_LONG_POLL_SECONDS,
        )

    def test_valid_values(self):
        self.assertEqual(ik.DEFAULT_TELEGRAM_LONG_POLL_SECONDS, 25)
        self.assertEqual(self._parse(None), 25)
        self.assertEqual(self._parse(" "), 25)
        self.assertEqual(self._parse("0"), 0)
        self.assertEqual(self._parse(" 50 "), 50)

    def test_invalid_values_raise(self):
        for raw in ("51", "-1", "abc", "2.5", "1e3"):
            with self.subTest(raw=raw):
                with self.assertRaises(RuntimeError) as ctx:
                    self._parse(raw)
                message = str(ctx.exception)
                self.assertIn("TELEGRAM_LONG_POLL_SECONDS", message)
                self.assertIn(ik.CONFIG_FILE, message)
                self.assertIn("od 0 do 50", message)

    def test_startup_default_and_override(self):
        base = "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nSTORE_IDS=294\n"
        result = _run_python_code(
            "import ikea_okazje as ik\nik.initialize_runtime()\n"
            "assert ik.TELEGRAM_LONG_POLL_SECONDS == 25, ik.TELEGRAM_LONG_POLL_SECONDS\n"
            "import os\n"
            "with open(ik.CONFIG_FILE, 'a', encoding='utf-8') as f:\n"
            "    f.write('TELEGRAM_LONG_POLL_SECONDS=0\\n')\n"
            "ik.initialize_runtime()\n"
            "assert ik.TELEGRAM_LONG_POLL_SECONDS == 0, ik.TELEGRAM_LONG_POLL_SECONDS\n"
            "print('OK')\n",
            _make_isolated_home(base),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK", result.stdout)

    def test_invalid_value_fails_at_startup(self):
        result = _run_module_with_env(
            "SMTP_MODE=exim\nEMAIL_TO=test@example.com\nTELEGRAM_LONG_POLL_SECONDS=60\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TELEGRAM_LONG_POLL_SECONDS", result.stderr)


class TestTelegramLongPollRequests(unittest.TestCase):
    """getUpdates z timeoutem long pollingu i przekazanie go z
    handle_telegram_updates(); urlopen zmockowany."""

    def setUp(self):
        patcher = mock.patch.object(ik, "TELEGRAM_BOT_TOKEN", _TEST_BOT_TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get_updates(self, *args, **kwargs):
        with mock.patch.object(ik.urllib.request, "urlopen", return_value=_telegram_ok([])) as urlopen:
            ik.telegram_get_updates(*args, **kwargs)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        return payload, urlopen.call_args.kwargs["timeout"]

    def test_short_poll_by_default(self):
        payload, http_timeout = self._get_updates(3)
        self.assertEqual(payload, {"offset": 3, "timeout": 0})
        self.assertEqual(http_timeout, ik.REQUEST_TIMEOUT)

    def test_long_poll_timeout_passed_to_api_and_http(self):
        payload, http_timeout = self._get_updates(3, timeout=25)
        self.assertEqual(payload, {"offset": 3, "timeout": 25})
        self.assertEqual(http_timeout, 25 + ik.REQUEST_TIMEOUT)

    def test_handle_updates_passes_long_poll_seconds(self):
        for kwargs, expected in (({}, 0), ({"long_poll_seconds": 7}, 7)):
            with self.subTest(kwargs=kwargs):
                with mock.patch.object(ik, "load_telegram_offset", return_value=11), \
                     mock.patch.object(ik, "telegram_get_updates", return_value=[]) as get_updates, \
                     mock.patch.object(ik, "save_telegram_offset") as save_offset:
                    ik.handle_telegram_updates(**kwargs)
                get_updates.assert_called_once_with(11, timeout=expected)
                save_offset.assert_not_called()

    def test_cron_mode_uses_short_poll(self):
        with mock.patch.object(ik, "initialize_runtime"), \
             mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
             mock.patch.object(ik, "blocking_backoff_active", return_value=None), \
             mock.patch.object(ik, "run_ikea_check_cycle", return_value=0), \
             mock.patch.object(ik, "TELEGRAM_ENABLED", True), \
             mock.patch.object(ik, "TELEGRAM_LONG_POLL_SECONDS", 25), \
             mock.patch.object(ik, "RUN_MODE", "cron"), \
             mock.patch.object(ik, "load_telegram_offset", return_value=0), \
             mock.patch.object(ik, "telegram_get_updates", return_value=[]) as get_updates:
            ik.main()
        get_updates.assert_called_once_with(0, timeout=0)

    def test_long_poll_timeout_bounded_by_next_check(self):
        with mock.patch.object(ik, "TELEGRAM_LONG_POLL_SECONDS", 25):
            self.assertEqual(ik.telegram_long_poll_timeout(1000), 25)
            self.assertEqual(ik.telegram_long_poll_timeout(25), 25)
            self.assertEqual(ik.telegram_long_poll_timeout(10.2), 11)
            self.assertEqual(ik.telegram_long_poll_timeout(0.3), 1)
            self.assertEqual(ik.telegram_long_poll_timeout(0), 0)
            self.assertEqual(ik.telegram_long_poll_timeout(-5), 0)


class TestDaemonLongPolling(unittest.TestCase):
    """Petla run_daemon() z long pollingiem: zegar monotoniczny, Telegram,
    cykl IKEA i SHUTDOWN_EVENT.wait sa zmockowane (bez realnego czekania)."""

    def setUp(self):
        self._orig_backoff = ik.get_blocking_backoff_state().to_dict()
        ik.set_blocking_backoff_state(ik.BackoffState())
        ik.SHUTDOWN_EVENT.clear()
        self.clock = {"t": 1000.0}
        self.polls = []
        self.waits = []
        self.cycles = []
        for patcher in (
            mock.patch.object(ik, "install_shutdown_signal_handlers"),
            mock.patch.object(ik, "TELEGRAM_ENABLED", True),
            mock.patch.object(ik, "TELEGRAM_LONG_POLL_SECONDS", 25),
            mock.patch.object(ik, "TELEGRAM_POLL_INTERVAL_SECONDS", 15),
            mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 90),
            mock.patch.object(ik, "CHECK_INTERVAL_JITTER_PERCENT", 0),
            mock.patch.object(ik.time, "monotonic", side_effect=lambda: self.clock["t"]),
            mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=self._fake_wait),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        ik.set_blocking_backoff_state(ik.BackoffState.from_dict(self._orig_backoff))
        ik.SHUTDOWN_EVENT.clear()

    def _fake_wait(self, timeout):
        self.waits.append(timeout)
        self.clock["t"] += timeout
        if len(self.waits) > 50:
            ik.SHUTDOWN_EVENT.set()  # bezpiecznik testu
        return ik.SHUTDOWN_EVENT.is_set()

    def _poll_taking(self, seconds_fn):
        def fake_poll(long_poll_seconds=0):
            self.polls.append(long_poll_seconds)
            self.clock["t"] += seconds_fn(long_poll_seconds)
        return fake_poll

    def _cycle_then_stop(self):
        self.cycles.append(self.clock["t"])
        ik.SHUTDOWN_EVENT.set()
        return 0

    def _run(self, poll, cycle=None):
        with mock.patch.object(ik, "handle_telegram_updates", side_effect=poll), \
             mock.patch.object(ik, "run_ikea_check_cycle", side_effect=cycle or self._cycle_then_stop), \
             mock.patch.object(ik, "log") as mock_log:
            result = ik.run_daemon()
        self.assertEqual(result, 0)
        return " ".join(str(call.args[0]) for call in mock_log.call_args_list)

    def test_poll_timeout_bounded_by_next_ikea_check(self):
        self._run(self._poll_taking(lambda timeout: timeout))
        self.assertEqual(self.polls, [25, 25, 25, 15])
        self.assertEqual(self.cycles, [1090.0])
        # Po udanym long pollu bez pelnego TELEGRAM_POLL_INTERVAL_SECONDS.
        self.assertEqual(self.waits, [0.0] * 4)

    def test_quick_poll_response_waits_short_minimum(self):
        def poll(long_poll_seconds=0):
            self.polls.append(long_poll_seconds)
            self.clock["t"] += 0.25
            if len(self.polls) == 3:
                ik.SHUTDOWN_EVENT.set()

        self._run(poll)
        self.assertEqual(self.polls, [25, 25, 25])
        self.assertEqual(len(self.waits), 2)
        for timeout in self.waits:
            self.assertAlmostEqual(timeout, ik.TELEGRAM_LONG_POLL_MIN_INTERVAL_SECONDS - 0.25)
        self.assertEqual(self.cycles, [])

    def test_due_check_polls_with_zero_timeout_then_short_wait(self):
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=1, last_status_code=403, next_allowed_check_at=time.time() - 10,
        ))
        self._run(self._poll_taking(lambda timeout: 0))
        self.assertEqual(self.polls, [0])
        self.assertEqual(self.cycles, [1000.0])
        self.assertEqual(self.waits, [ik.TELEGRAM_LONG_POLL_MIN_INTERVAL_SECONDS])

    def test_telegram_error_waits_poll_interval(self):
        def failing_poll(long_poll_seconds=0):
            self.polls.append(long_poll_seconds)
            if len(self.polls) == 2:
                ik.SHUTDOWN_EVENT.set()
            raise RuntimeError("Telegram API (getUpdates) zwrocilo HTTP 409: Conflict")

        messages = self._run(failing_poll)
        self.assertEqual(self.waits, [15])
        self.assertIn("HTTP 409", messages)

    def test_long_poll_disabled_keeps_short_polling(self):
        with mock.patch.object(ik, "TELEGRAM_LONG_POLL_SECONDS", 0):
            messages = self._run(self._poll_taking(lambda timeout: 0.1))
        self.assertTrue(self.polls)
        self.assertEqual(set(self.polls), {0})
        self.assertEqual(set(self.waits), {15})
        self.assertEqual(len(self.cycles), 1)
        self.assertIn("Telegram co 15s", messages)
        self.assertNotIn("long polling", messages)

    def test_telegram_disabled_keeps_poll_interval_wait(self):
        with mock.patch.object(ik, "TELEGRAM_ENABLED", False):
            messages = self._run(self._poll_taking(lambda timeout: 0))
        self.assertEqual(self.polls, [])
        self.assertEqual(set(self.waits), {15})
        self.assertEqual(len(self.cycles), 1)
        self.assertIn("Telegram wylaczony", messages)

    def test_startup_log_mentions_long_polling(self):
        messages = self._run(self._poll_taking(lambda timeout: timeout))
        self.assertIn("long polling do 25s", messages)

    def test_shutdown_during_long_poll_skips_due_cycle(self):
        ik.set_blocking_backoff_state(ik.BackoffState(
            failure_count=1, last_status_code=403, next_allowed_check_at=time.time() + 5,
        ))

        def poll_interrupted(long_poll_seconds=0):
            self.polls.append(long_poll_seconds)
            self.clock["t"] += long_poll_seconds
            ik.SHUTDOWN_EVENT.set()  # SIGTERM w trakcie long pollu

        messages = self._run(poll_interrupted)
        self.assertEqual(len(self.polls), 1)
        self.assertLessEqual(self.polls[0], 6)
        self.assertEqual(self.cycles, [])
        self.assertEqual(self.waits, [])
        self.assertIn("Zatrzymywanie", messages)


class TestBackoffStateFromDictHardening(unittest.TestCase):
    """BackoffState.from_dict(): uszkodzone pola trwalego backoffu ->
    None/0 zamiast wyjatku w daemonie albo w sprawdzeniu backoffu cron."""

    CORRUPTED = ("jutro", "1700000000", float("nan"), float("inf"), float("-inf"),
                 True, False, [1], {"a": 1}, 10 ** 400)

    def setUp(self):
        self._orig_backoff = ik.get_blocking_backoff_state().to_dict()
        ik.SHUTDOWN_EVENT.clear()

    def tearDown(self):
        ik.set_blocking_backoff_state(ik.BackoffState.from_dict(self._orig_backoff))
        ik.SHUTDOWN_EVENT.clear()

    def test_valid_numbers_are_kept_with_types(self):
        state = ik.BackoffState.from_dict({
            "failure_count": 2,
            "last_status_code": 429.0,
            "next_allowed_check_at": 1700000000,
            "retry_after_seconds": 30,
        })
        self.assertEqual(state, ik.BackoffState(2, 429, 1700000000.0, 30.0))
        self.assertIsInstance(state.last_status_code, int)
        self.assertIsInstance(state.next_allowed_check_at, float)
        self.assertIsInstance(state.retry_after_seconds, float)
        self.assertEqual(ik.BackoffState.from_dict(state.to_dict()), state)

    def test_corrupted_values_become_none(self):
        for value in self.CORRUPTED:
            with self.subTest(value=value):
                state = ik.BackoffState.from_dict({
                    "failure_count": 1,
                    "last_status_code": value,
                    "next_allowed_check_at": value,
                    "retry_after_seconds": value,
                })
                self.assertEqual(state.failure_count, 1)
                self.assertIsNone(state.last_status_code)
                self.assertIsNone(state.next_allowed_check_at)
                self.assertIsNone(state.retry_after_seconds)

    def test_missing_fields_and_non_dict(self):
        self.assertEqual(ik.BackoffState.from_dict({}), ik.BackoffState())
        self.assertEqual(ik.BackoffState.from_dict(None), ik.BackoffState())
        self.assertEqual(ik.BackoffState.from_dict("x"), ik.BackoffState())

    def test_json_nan_and_infinity_from_file(self):
        raw = json.loads(
            '{"failure_count": Infinity, "last_status_code": NaN, '
            '"next_allowed_check_at": Infinity, "retry_after_seconds": -Infinity}'
        )
        self.assertEqual(ik.BackoffState.from_dict(raw), ik.BackoffState())
        for value in (float("nan"), "abc", -3, None):
            with self.subTest(value=value):
                self.assertEqual(ik.BackoffState.from_dict({"failure_count": value}).failure_count, 0)

    def test_cron_backoff_check_tolerates_corrupted_deadline(self):
        for deadline in ("2026-01-01 12:00", float("nan"), [1]):
            with self.subTest(deadline=deadline):
                ik.DYNAMIC_STATE["blocking_backoff"] = {
                    "failure_count": 3, "last_status_code": "x",
                    "next_allowed_check_at": deadline, "retry_after_seconds": "y",
                }
                self.assertIsNone(ik.blocking_backoff_active())
                with mock.patch.object(ik, "initialize_runtime"), \
                     mock.patch.object(ik, "warn_if_systemd_without_daemon_mode"), \
                     mock.patch.object(ik, "run_ikea_check_cycle", return_value=0) as cycle, \
                     mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
                     mock.patch.object(ik, "RUN_MODE", "cron"):
                    self.assertEqual(ik.main(), 0)
                cycle.assert_called_once()

    def test_daemon_start_tolerates_corrupted_deadline(self):
        ik.DYNAMIC_STATE["blocking_backoff"] = {
            "failure_count": 2, "last_status_code": 429, "next_allowed_check_at": "jutro",
        }

        def stop_on_wait(timeout):
            ik.SHUTDOWN_EVENT.set()
            return True

        with mock.patch.object(ik, "install_shutdown_signal_handlers"), \
             mock.patch.object(ik, "run_ikea_check_cycle") as cycle, \
             mock.patch.object(ik, "TELEGRAM_ENABLED", False), \
             mock.patch.object(ik.SHUTDOWN_EVENT, "wait", side_effect=stop_on_wait):
            self.assertEqual(ik.run_daemon(), 0)
        cycle.assert_not_called()

    def test_large_failure_count_does_not_overflow(self):
        # Ulamkowy interwal z .env daje float w bazie - bez limitu wykladnika
        # float * 2**2000 konczyl sie OverflowError.
        patcher = mock.patch.object(ik, "CHECK_INTERVAL_SECONDS", 2700.5)
        patcher.start()
        self.addCleanup(patcher.stop)
        cap = ik.effective_backoff_cap_seconds()
        for failure_count in (2000, 10 ** 6):
            with self.subTest(failure_count=failure_count):
                self.assertEqual(ik.compute_local_backoff_delay(failure_count), cap)
        delay = ik.compute_next_allowed_check_delay(5000)
        self.assertGreaterEqual(delay, cap)
        self.assertLessEqual(delay, cap * (1 + ik.BACKOFF_JITTER_PERCENT / 100))
        for failure_count in range(1, 40):
            expected = min(
                ik.effective_backoff_base_seconds() * 2 ** (failure_count - 1), cap
            )
            self.assertEqual(ik.compute_local_backoff_delay(failure_count), expected)


if __name__ == "__main__":
    unittest.main()
