#!/usr/bin/env python3
"""
ikea_okazje.py

Sprawdza dzial "Okazje na okraglo" (second-hand / buy-from-ikea) w
wybranych sklepach IKEA i wysyla powiadomienie (e-mail i/lub Telegram),
gdy pojawi sie szukany produkt.

Komendy Telegrama (tylko z czatu TELEGRAM_CHAT_ID):
    /dodaj <slowo>     - dodaj slowo kluczowe
    /usun <slowo>      - usun slowo kluczowe
    /numer <nr>        - dodaj numer artykulu
    /usunnumer <nr>    - usun numer artykulu
    /sklepy            - pokaz aktywne i dostepne sklepy
    /dodajsklep <ID>   - dodaj sklep do monitoringu
    /usunsklep <ID>    - usun sklep z monitoringu
    /status            - pokaz monitorowane slowa/numery/sklepy
    /pomoc             - lista komend

Tryby pracy (RUN_MODE):
    "cron"   (domyslny) - jedno przejscie i wyjscie; w trakcie backoffu
               po 403/429 przejscie konczy sie bez zapytania do IKEA.
    "daemon" - petla w tle (np. systemd): Telegram co
               TELEGRAM_POLL_INTERVAL_SECONDS, oferty co
               CHECK_INTERVAL_SECONDS; lagodne zakonczenie po SIGTERM/SIGINT.

Konfiguracja uzytkownika jest w ~/.config/ikea-okazje.env (patrz
.env.example), nie w kodzie - aktualizacja skryptu jej nie nadpisze.

Import modulu nie ma efektow ubocznych (nie czyta .env, nie dotyka plikow
stanu); start aplikacji robi initialize_runtime(), wolana z main().

Wymagania: pip install curl_cffi
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
import smtplib
import ssl
import sys
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from curl_cffi import requests

# ---------------- KONFIGURACJA TECHNICZNA (nie zmieniaj tego przez .env) ----------------
API_URL = "https://web-api.ikea.com/circular/circular-asis/offers/grouped/search"

PAGE_SIZE = "64"   # API ogranicza max rozmiar strony do 64
MAX_PAGES = 20      # zabezpieczenie przed niekonczaca sie paginacja

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
# W obrebie jednego zapytania ponawiamy tylko przejsciowe 5xx. 403/429 to
# blokada/rate limit: bez ponawiania w miejscu, od razu BlockedByServerError
# i globalny backoff.
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
BLOCKING_STATUS_CODES = {403, 429}
STORE_JITTER_RANGE = (1.0, 3.0)

# Domyslny interwal sprawdzania ofert (45 min) - rzadkie, prywatne
# sprawdzanie. CHECK_INTERVAL_SECONDS z .env ma pierwszenstwo.
DEFAULT_CHECK_INTERVAL_SECONDS = 2700
# Dolna granica CHECK_INTERVAL_SECONDS; mniejsza wartosc z .env jest
# podnoszona z ostrzezeniem (czeste odpytywanie prywatnego API).
MIN_CHECK_INTERVAL_SECONDS = 300
DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS = 15

# Po tylu kolejnych nieudanych cyklach idzie alert o awarii monitora
# (HEALTH_ALERT_AFTER_CYCLES z .env ma pierwszenstwo; 0 wylacza alert).
DEFAULT_HEALTH_ALERT_AFTER_CYCLES = 3
# Limit opisu bledu w stanie i w alercie.
HEALTH_LAST_ERROR_MAX_CHARS = 300

# Symetryczny jitter (+/- %) interwalu sprawdzania - rozbija sztywny rytm.
CHECK_INTERVAL_JITTER_PERCENT = 25

# Wykladniczy backoff po cyklu z 403/429. Jest GLOBALNY, nie per sklep:
# wszystkie sklepy ida przez jeden endpoint, wiec blokada dotyczy calego
# klienta. Stan jest trwaly (DYNAMIC_STATE_FILE, "blocking_backoff"), wiec
# restart nie resetuje harmonogramu.
# - pierwszy krok = max(CHECK_INTERVAL_SECONDS, BACKOFF_BASE_SECONDS), zeby
#   backoff nigdy nie zwiekszal ruchu wzgledem normalnego interwalu;
# - cap = max(pierwszy krok, BACKOFF_CAP_SECONDS);
# - jitter backoffu tylko w gore - nie skraca max(backoff, Retry-After).
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_CAP_SECONDS = 1800.0
BACKOFF_JITTER_PERCENT = 20

# Naglowki niezalezne od profilu klienta. user-agent i sec-ch-ua dochodza z
# BrowserProfile, zeby zgadzaly sie z impersonacja TLS curl_cffi.
BASE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "pl-PL,pl;q=0.8",
    "origin": "https://www.ikea.com",
    "priority": "u=1, i",
    "referer": "https://www.ikea.com/",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "sec-gpc": "1",
}

# Niezmienny profil klienta: impersonate curl_cffi + zgodne User-Agent/sec-ch-ua.
@dataclass(frozen=True)
class BrowserProfile:
    impersonate: str
    user_agent: str
    sec_ch_ua: str


# Tylko profile wspierane przez kazda wersje curl_cffi od minimalnej z
# requirements.txt (>=0.7.0). Nowszy profil wymaga podniesienia tej granicy.
CLIENT_PROFILES: tuple[BrowserProfile, ...] = (
    BrowserProfile(
        impersonate="chrome120",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="120", "Not_A Brand";v="8", "Google Chrome";v="120"',
    ),
    BrowserProfile(
        impersonate="chrome123",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Google Chrome";v="123", "Not(A:Brand";v="24", "Chromium";v="123"',
    ),
    BrowserProfile(
        impersonate="chrome124",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="124", "Not?A_Brand";v="24", "Google Chrome";v="124"',
    ),
)

# Profil wybrany na biezacy cykl (run_ikea_check_cycle()) - wszystkie
# zapytania cyklu uzywaja tych samych naglowkow i fingerprintu TLS.
CURRENT_CLIENT_PROFILE: "BrowserProfile | None" = None


# Trwaly stan globalnego backoffu po 403/429 (klucz "blocking_backoff").
# next_allowed_check_at to czas scienny (time.time()), bo musi przetrwac
# restart procesu; petla daemona odlicza czas zegarem monotonicznym.
@dataclass
class BackoffState:
    failure_count: int = 0
    last_status_code: "int | None" = None
    next_allowed_check_at: "float | None" = None
    retry_after_seconds: "float | None" = None

    def to_dict(self) -> dict:
        return {
            "failure_count": self.failure_count,
            "last_status_code": self.last_status_code,
            "next_allowed_check_at": self.next_allowed_check_at,
            "retry_after_seconds": self.retry_after_seconds,
        }

    @classmethod
    def from_dict(cls, data) -> "BackoffState":
        """Odtwarza stan z JSON; odporne na brakujace lub niepoprawne pola."""
        if not isinstance(data, dict):
            return cls()
        try:
            failure_count = int(data.get("failure_count") or 0)
        except (TypeError, ValueError):
            failure_count = 0
        return cls(
            failure_count=max(0, failure_count),
            last_status_code=data.get("last_status_code"),
            next_allowed_check_at=data.get("next_allowed_check_at"),
            retry_after_seconds=data.get("retry_after_seconds"),
        )


STATE_FILE = os.path.expanduser("~/.ikea_okazje_seen_offers.json")
RAW_DUMP_FILE = os.path.expanduser("~/.ikea_okazje_last_raw.json")
CONFIG_FILE = os.path.expanduser("~/.config/ikea-okazje.env")
DYNAMIC_STATE_FILE = os.path.expanduser("~/.ikea_okazje_dynamic.json")
TELEGRAM_OFFSET_FILE = os.path.expanduser("~/.ikea_okazje_telegram_offset.json")

SMTP_TIMEOUT = 30
REQUEST_TIMEOUT = 15

# Sklepy IKEA w Polsce: storeId -> nazwa + slug strony "Okazje na Okraglo".
# Uzywane przez komendy sklepow i linki rezerwacji (gdy STORE_URL_SLUGS z
# .env nie ma wpisu). store_display_name() obcina przedrostek "IKEA ".
KNOWN_STORES = {
    "1224": {"name": "IKEA Bielsko-Biala", "slug": "bielsko+biala"},
    "429": {"name": "IKEA Bydgoszcz", "slug": "bydgoszcz"},
    "203": {"name": "IKEA Gdansk", "slug": "gdańsk"},
    "306": {"name": "IKEA Katowice", "slug": "katowice"},
    "204": {"name": "IKEA Krakow", "slug": "kraków"},
    "329": {"name": "IKEA Lodz", "slug": "łódź"},
    "311": {"name": "IKEA Lublin", "slug": "lublin"},
    "205": {"name": "IKEA Poznan", "slug": "poznań"},
    "583": {"name": "IKEA Szczecin", "slug": "szczecin"},
    "188": {"name": "IKEA Warszawa Janki", "slug": "warszawa+janki"},
    "307": {"name": "IKEA Warszawa Targowek", "slug": "warszawa+targówek"},
    "294": {"name": "IKEA Wroclaw", "slug": "wrocław"},
}
# ------------------------------------------------------------------------


def log(message: str, to_stderr: bool = False) -> None:
    # flush: pod systemd stdout to pipe z buforowaniem blokowym, bez tego
    # wpisy trafiaja do journalctl z duzym opoznieniem.
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, file=sys.stderr if to_stderr else sys.stdout, flush=True)


def load_env_file(path: str) -> dict:
    values = {}
    if not os.path.isfile(path):
        return values

    permissions = os.stat(path).st_mode & 0o777
    if permissions & 0o077:
        raise RuntimeError(
            f"Zbyt szerokie uprawnienia pliku {path}: {oct(permissions)}. Ustaw chmod 600."
        )

    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export ") or key.startswith("export\t"):
                key = key[len("export"):].strip()
            values[key] = _parse_env_value(raw_value, path, line_number, key)
    return values


def _parse_env_value(raw_value: str, path: str, line_number: int, key: str) -> str:
    """Wartosc z linii .env. W cudzyslowie ('...' albo "...") brana doslownie
    (moze zawierac '#' i spacje); bez cudzyslowu komentarz zaczyna '#' po
    bialym znaku. Bledy nie pokazuja wartosci - to moze byc haslo."""
    value = raw_value.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end == -1:
            raise RuntimeError(
                f"Niezamkniety cudzyslow w {path}, linia {line_number} (klucz {key})."
            )
        rest = value[end + 1:].strip()
        if rest and not rest.startswith("#"):
            raise RuntimeError(
                f"Nieoczekiwane znaki po cudzyslowie zamykajacym w {path}, "
                f"linia {line_number} (klucz {key}). Dozwolony jest tylko "
                "komentarz zaczynajacy sie od '#'."
            )
        return value[1:end]
    comment = re.search(r"\s#", raw_value)
    if comment:
        return raw_value[:comment.start()].strip()
    return value


def parse_list(raw, default):
    if raw is None or raw.strip() == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_config_number(raw, key: str):
    """Liczba z .env (int, a gdy sie nie da - float) albo None dla pustej
    wartosci. Tekst, ktory nie jest skonczona liczba, konczy start
    RuntimeError, zamiast cicho wylaczyc filtr albo wlaczyc default."""
    if raw is None or raw.strip() == "":
        return None
    text = raw.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value):
        raise RuntimeError(
            f"Nieprawidlowe {key} w {CONFIG_FILE}: '{text}' nie jest liczba."
        )
    return value


def parse_optional_non_negative_number(raw, key: str):
    """Filtr liczbowy (MIN_DISCOUNT_PERCENT, MAX_PRICE): pusty -> None
    (wylaczony), ujemny lub nie-liczba -> RuntimeError."""
    value = _parse_config_number(raw, key)
    if value is not None and value < 0:
        raise RuntimeError(
            f"Nieprawidlowe {key} w {CONFIG_FILE}: '{raw.strip()}' - wartosc "
            "nie moze byc ujemna."
        )
    return value


def parse_positive_number(raw, key: str, default):
    """Dodatnia liczba z .env; pusty -> default, <= 0 lub nie-liczba ->
    RuntimeError."""
    value = _parse_config_number(raw, key)
    if value is None:
        return default
    if value <= 0:
        raise RuntimeError(
            f"Nieprawidlowe {key} w {CONFIG_FILE}: '{raw.strip()}' - wartosc "
            "musi byc wieksza od zera."
        )
    return value


def parse_check_interval_seconds(raw):
    """CHECK_INTERVAL_SECONDS: pusty -> DEFAULT_CHECK_INTERVAL_SECONDS.
    Wartosc ponizej MIN_CHECK_INTERVAL_SECONDS jest podnoszona z
    ostrzezeniem zamiast bledu, zeby istniejace instalacje dalej startowaly."""
    value = parse_positive_number(raw, "CHECK_INTERVAL_SECONDS", DEFAULT_CHECK_INTERVAL_SECONDS)
    if value < MIN_CHECK_INTERVAL_SECONDS:
        log(
            f"UWAGA: CHECK_INTERVAL_SECONDS={value} w {CONFIG_FILE} jest ponizej "
            f"minimum {MIN_CHECK_INTERVAL_SECONDS}s - uzywam {MIN_CHECK_INTERVAL_SECONDS}s.",
            to_stderr=True,
        )
        return MIN_CHECK_INTERVAL_SECONDS
    return value


def parse_non_negative_int(raw, key: str, default: int) -> int:
    """Liczba calkowita >= 0 z .env; pusty -> default, inna wartosc ->
    RuntimeError."""
    if raw is None or raw.strip() == "":
        return default
    text = raw.strip()
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        raise RuntimeError(
            f"Nieprawidlowe {key} w {CONFIG_FILE}: '{text}' - oczekiwana "
            "liczba calkowita >= 0."
        )
    return value


def parse_run_mode(raw) -> str:
    """Waliduje RUN_MODE (jak parse_smtp_mode()): pusty -> 'cron', literowka
    konczy start RuntimeError zamiast cicho wlaczyc tryb cron."""
    value = ("" if raw is None else raw).strip()
    mode = value.lower() or "cron"
    if mode not in ("cron", "daemon"):
        raise RuntimeError(
            f"Nieprawidlowe RUN_MODE w {CONFIG_FILE}: '{value}'. "
            "Dozwolone wartosci: cron, daemon."
        )
    return mode


def parse_bool(raw, default):
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "tak")


def parse_store_url_slugs(raw: str, default: dict) -> dict:
    """Parsuje STORE_URL_SLUGS ("ID:slug,ID:slug") do {store_id: slug}.
    Niepoprawne wpisy sa pomijane; gdy nie ma zadnego poprawnego - default."""
    if not raw or not raw.strip():
        return default
    result = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            continue
        store_id, _, slug = entry.partition(":")
        store_id = store_id.strip()
        slug = slug.strip()
        if store_id and slug:
            result[store_id] = slug
    return result if result else default


def normalize_article_number(value) -> str:
    """Zostawia same cyfry ('905.574.19', '905 574 19', 90557419 ->
    '90557419'), zeby porownywac numery z .env, Telegrama i API."""
    return "".join(char for char in str(value) if char.isdigit())


def normalize_article_numbers(values) -> list:
    """Normalizuje liste numerow; usuwa duplikaty (zachowujac kolejnosc) i
    wpisy bez cyfr."""
    result = []
    for value in values:
        normalized = normalize_article_number(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def parse_smtp_mode(raw) -> str:
    """Waliduje SMTP_MODE. Nieznana wartosc (np. literowka) konczy start
    RuntimeError, zamiast cicho wpasc w ktorys tryb. 'disabled' wylacza
    e-mail."""
    value = ("" if raw is None else raw).strip()
    mode = value.lower()
    if mode not in ("gmail", "local587", "exim", "disabled"):
        raise RuntimeError(
            f"Nieprawidlowe SMTP_MODE: '{value}'. "
            "Dozwolone wartosci: gmail, local587, exim, disabled."
        )
    return mode


def validate_notification_config(
    smtp_mode: str,
    smtp_user,
    smtp_pass,
    use_smtp_auth: bool,
    email_to,
    telegram_bot_token,
    telegram_chat_id,
) -> None:
    """Przy starcie wymaga co najmniej jednego kompletnego kanalu
    powiadomien (zamiast bledu przy pierwszej wysylce) i odrzuca
    niekompletny wlaczony e-mail lub polowiczny Telegram. Dostaje wartosci
    efektywne (po fallbackach); use_smtp_auth musi odpowiadac USE_AUTH."""
    has_token = bool(telegram_bot_token)
    has_chat_id = bool(telegram_chat_id)

    if has_token != has_chat_id:
        raise RuntimeError(
            "Niepelna konfiguracja Telegrama: potrzebujesz jednoczesnie "
            "TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID (albo obu, albo zadnego z "
            f"nich). Aktualnie ustawiony jest tylko "
            f"{'TELEGRAM_BOT_TOKEN' if has_token else 'TELEGRAM_CHAT_ID'}."
        )

    telegram_enabled = has_token and has_chat_id

    if smtp_mode == "disabled":
        if not telegram_enabled:
            raise RuntimeError(
                "Brak skonfigurowanego kanalu powiadomien: SMTP_MODE='disabled' "
                "wylacza wysylke e-mail, a Telegram nie jest skonfigurowany "
                "(TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID). Skonfiguruj albo e-mail "
                "(ustaw SMTP_MODE na 'gmail', 'local587' albo 'exim' i wypelnij "
                "wymagane pola SMTP), albo kompletny Telegram - patrz README."
            )
        return

    missing = []
    if use_smtp_auth:
        if not smtp_user:
            missing.append("SMTP_USER")
        if not smtp_pass:
            missing.append("SMTP_PASS")
    if not email_to:
        missing.append("EMAIL_TO")

    if missing:
        raise RuntimeError(
            f"SMTP_MODE='{smtp_mode}' wymaga uzupelnienia w {CONFIG_FILE} "
            f"nastepujacych pol: {', '.join(missing)}. Jesli nie chcesz "
            "uzywac e-maila, ustaw SMTP_MODE=disabled i skonfiguruj "
            "kompletny Telegram (TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID) - "
            "patrz README."
        )


# ---------------- USTAWIENIA UZYTKOWNIKA (z ~/.config/ikea-okazje.env) ----------------
# Nieszkodliwe defaulty; prawdziwe wartosci przypisuje initialize_runtime().
ENV: dict = {}

BASE_STORE_IDS = ["294"]
BASE_SEARCH_TERMS = ["Stall"]
BASE_SEARCH_ARTICLE_NUMBERS: list = []

MIN_DISCOUNT_PERCENT = None
MAX_PRICE = None
KEYWORDS_EXCLUDE: list = []

ALERT_EXISTING_ON_FIRST_RUN = False

SMTP_MODE = "gmail"
VERIFY_TLS = True

RUN_MODE = "cron"
CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL_SECONDS
TELEGRAM_POLL_INTERVAL_SECONDS = DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS
HEALTH_ALERT_AFTER_CYCLES = DEFAULT_HEALTH_ALERT_AFTER_CYCLES

STORE_URL_SLUGS: dict = {}

SMTP_HOST = None
SMTP_PORT = None
SMTP_USER = None
SMTP_PASS = None
EMAIL_FROM = None
USE_AUTH = False
EMAIL_ENABLED = False
EMAIL_TO = None

TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None
TELEGRAM_ENABLED = False

DYNAMIC_STATE: dict = {}
SEARCH_TERMS: list = []
SEARCH_ARTICLE_NUMBERS: list = []
STORE_IDS: list = []
NORMALIZED_TERMS: list = []
NORMALIZED_EXCLUDE: list = []
# ----------------------------------------------------------------------------------------

# Sygnal zamkniecia trybu daemon; handlery sygnalow instaluje dopiero run_daemon().
SHUTDOWN_EVENT = threading.Event()


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u0142", "l").replace("\u0141", "L")
    decomposed = unicodedata.normalize("NFKD", s)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold()


# ---------------- DYNAMICZNA LISTA (modyfikowana komendami z Telegrama) ----------------

def default_access_notification_state() -> dict:
    """Pusty stan bloku 'ikea_access_notification'. pending_outage=True:
    jeden w pelni zablokowany cykl (403/429 na wszystkich sklepach) bez
    alertu, czeka na potwierdzenie kolejnym takim cyklem."""
    return {"outage_active": False, "last_status_code": None, "pending_outage": False}


def migrate_access_notification_state(raw) -> dict:
    """Uzupelnia brakujace pola bloku 'ikea_access_notification' z dysku,
    zachowujac istniejace; niepoprawny wpis -> pusty stan. Brak
    pending_outage -> False (outage_active=True znaczy, ze alert juz
    wyslano, wiec nie ma czego potwierdzac)."""
    if not isinstance(raw, dict):
        return default_access_notification_state()
    return {
        "outage_active": bool(raw.get("outage_active", False)),
        "last_status_code": raw.get("last_status_code"),
        "pending_outage": bool(raw.get("pending_outage", False)),
    }


def default_blocking_backoff_state_dict() -> dict:
    """Pusty stan bloku 'blocking_backoff' (harmonogram globalnego backoffu,
    niezalezny od stanu alertu 'ikea_access_notification')."""
    return BackoffState().to_dict()


def default_health_state() -> dict:
    """Pusty stan bloku 'health' (alert o dluzszej awarii monitora,
    niezalezny od alertu o dostepie 403/429)."""
    return {
        "consecutive_failed_cycles": 0,
        "alert_active": False,
        "last_error": None,
        "last_successful_check_at": None,
    }


def migrate_health_state(raw) -> dict:
    """Odtwarza blok 'health' z JSON; niepoprawne pola -> wartosci domyslne.
    alert_active tylko dla dokladnie true, zeby np. "false" nie wlaczylo
    trwalego wyciszenia alertu."""
    if not isinstance(raw, dict):
        return default_health_state()
    try:
        failed_cycles = int(raw.get("consecutive_failed_cycles") or 0)
    except (TypeError, ValueError):
        failed_cycles = 0
    last_error = raw.get("last_error")
    if not isinstance(last_error, str):
        last_error = None
    last_success = raw.get("last_successful_check_at")
    if (
        isinstance(last_success, bool)
        or not isinstance(last_success, (int, float))
        or not math.isfinite(last_success)
    ):
        last_success = None
    return {
        "consecutive_failed_cycles": max(0, failed_cycles),
        "alert_active": raw.get("alert_active") is True,
        "last_error": last_error,
        "last_successful_check_at": last_success,
    }


def load_dynamic_state() -> dict:
    """Wczytuje dynamiczny stan (slowa, numery, sklepy, alerty, backoff).
    Pierwsze uruchomienie zasiewa go z .env; potem zrodlem prawdy jest plik
    (zmieniany komendami Telegrama). Brakujace klucze starszych plikow sa
    uzupelniane i zapisywane w miejscu, bez utraty danych."""
    if os.path.exists(DYNAMIC_STATE_FILE):
        with open(DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        had_store_ids = "store_ids" in data
        had_access_notification = "ikea_access_notification" in data
        had_blocking_backoff = "blocking_backoff" in data
        had_health = "health" in data
        raw_access_notification = data.get("ikea_access_notification")
        had_pending_outage_field = (
            isinstance(raw_access_notification, dict)
            and "pending_outage" in raw_access_notification
        )
        raw_article_numbers = data.get("search_article_numbers", list(BASE_SEARCH_ARTICLE_NUMBERS))
        normalized_article_numbers = normalize_article_numbers(raw_article_numbers)
        access_notification = migrate_access_notification_state(raw_access_notification)
        blocking_backoff = data.get("blocking_backoff") or default_blocking_backoff_state_dict()
        state = {
            "search_terms": data.get("search_terms", list(BASE_SEARCH_TERMS)),
            "search_article_numbers": normalized_article_numbers,
            "store_ids": data.get("store_ids", list(BASE_STORE_IDS)),
            "ikea_access_notification": access_notification,
            "blocking_backoff": blocking_backoff,
            "health": migrate_health_state(data.get("health")),
        }
        if (
            not had_store_ids
            or not had_access_notification
            or not had_pending_outage_field
            or not had_blocking_backoff
            or not had_health
            or normalized_article_numbers != raw_article_numbers
        ):
            save_dynamic_state(state)
        return state
    state = {
        "search_terms": list(BASE_SEARCH_TERMS),
        "search_article_numbers": list(BASE_SEARCH_ARTICLE_NUMBERS),
        "store_ids": list(BASE_STORE_IDS),
        "ikea_access_notification": default_access_notification_state(),
        "blocking_backoff": default_blocking_backoff_state_dict(),
        "health": default_health_state(),
    }
    save_dynamic_state(state)
    return state


def get_access_notification_state() -> dict:
    """Blok 'ikea_access_notification' z DYNAMIC_STATE (albo pusty stan)."""
    return DYNAMIC_STATE.get("ikea_access_notification") or default_access_notification_state()


def set_access_notification_state(
    outage_active: bool, last_status_code, pending_outage: bool = False
) -> None:
    """Zapisuje stan alertu na dysk, zeby restart w trakcie blokady nie
    powtorzyl alertu ani nie zgubil niepotwierdzonej porazki. Wolajacy
    nigdy nie ustawia jednoczesnie pending_outage i outage_active."""
    DYNAMIC_STATE["ikea_access_notification"] = {
        "outage_active": outage_active,
        "last_status_code": last_status_code,
        "pending_outage": pending_outage,
    }
    save_dynamic_state(DYNAMIC_STATE)


def get_blocking_backoff_state() -> BackoffState:
    """Trwaly stan globalnego backoffu ('blocking_backoff') jako BackoffState."""
    return BackoffState.from_dict(DYNAMIC_STATE.get("blocking_backoff"))


def set_blocking_backoff_state(state: BackoffState) -> None:
    """Zapisuje stan backoffu na dysk, zeby restart nie resetowal harmonogramu."""
    DYNAMIC_STATE["blocking_backoff"] = state.to_dict()
    save_dynamic_state(DYNAMIC_STATE)


def get_health_state() -> dict:
    """Kopia bloku 'health' z DYNAMIC_STATE (albo pusty stan)."""
    return migrate_health_state(DYNAMIC_STATE.get("health"))


def set_health_state(state: dict) -> None:
    """Zapisuje blok 'health' na dysk, zeby restart nie zerowal licznika
    nieudanych cykli ani nie powtarzal wyslanego alertu."""
    DYNAMIC_STATE["health"] = migrate_health_state(state)
    save_dynamic_state(DYNAMIC_STATE)


def save_dynamic_state(state: dict) -> None:
    tmp_path = DYNAMIC_STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, DYNAMIC_STATE_FILE)


def refresh_normalized_terms() -> None:
    """Wywolaj po kazdej zmianie SEARCH_TERMS przez komende Telegrama."""
    global NORMALIZED_TERMS
    NORMALIZED_TERMS = [normalize_text(t) for t in SEARCH_TERMS]


def initialize_runtime() -> None:
    """Start aplikacji: czyta .env, waliduje konfiguracje i kanaly
    powiadomien, wczytuje dynamiczny stan i ustawia globalne ustawienia.
    Wolana przez main() przed czymkolwiek innym; testy moga ja wolac z
    wlasnym HOME. Kazde wywolanie buduje globale od zera (bez doklejania)."""
    global ENV
    global BASE_STORE_IDS, BASE_SEARCH_TERMS, BASE_SEARCH_ARTICLE_NUMBERS
    global MIN_DISCOUNT_PERCENT, MAX_PRICE, KEYWORDS_EXCLUDE
    global ALERT_EXISTING_ON_FIRST_RUN, SMTP_MODE, VERIFY_TLS
    global RUN_MODE, CHECK_INTERVAL_SECONDS, TELEGRAM_POLL_INTERVAL_SECONDS
    global HEALTH_ALERT_AFTER_CYCLES
    global STORE_URL_SLUGS
    global SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, USE_AUTH
    global EMAIL_ENABLED, EMAIL_TO
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED
    global DYNAMIC_STATE, SEARCH_TERMS, SEARCH_ARTICLE_NUMBERS, STORE_IDS
    global NORMALIZED_TERMS, NORMALIZED_EXCLUDE

    ENV = load_env_file(CONFIG_FILE)

    # BASE_* tylko zasiewaja dynamiczny stan przy pierwszym uruchomieniu.
    BASE_STORE_IDS = parse_list(ENV.get("STORE_IDS"), ["294"])
    BASE_SEARCH_TERMS = parse_list(ENV.get("SEARCH_TERMS"), ["Stall"])
    BASE_SEARCH_ARTICLE_NUMBERS = normalize_article_numbers(
        parse_list(ENV.get("SEARCH_ARTICLE_NUMBERS"), [])
    )

    MIN_DISCOUNT_PERCENT = parse_optional_non_negative_number(
        ENV.get("MIN_DISCOUNT_PERCENT"), "MIN_DISCOUNT_PERCENT"
    )
    MAX_PRICE = parse_optional_non_negative_number(ENV.get("MAX_PRICE"), "MAX_PRICE")
    KEYWORDS_EXCLUDE = parse_list(ENV.get("KEYWORDS_EXCLUDE"), [])

    ALERT_EXISTING_ON_FIRST_RUN = parse_bool(ENV.get("ALERT_EXISTING_ON_FIRST_RUN"), False)

    SMTP_MODE = parse_smtp_mode(ENV.get("SMTP_MODE", "gmail"))
    VERIFY_TLS = parse_bool(ENV.get("VERIFY_TLS"), True)

    RUN_MODE = parse_run_mode(ENV.get("RUN_MODE"))
    CHECK_INTERVAL_SECONDS = parse_check_interval_seconds(ENV.get("CHECK_INTERVAL_SECONDS"))
    TELEGRAM_POLL_INTERVAL_SECONDS = parse_positive_number(
        ENV.get("TELEGRAM_POLL_INTERVAL_SECONDS"),
        "TELEGRAM_POLL_INTERVAL_SECONDS",
        DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS,
    )
    HEALTH_ALERT_AFTER_CYCLES = parse_non_negative_int(
        ENV.get("HEALTH_ALERT_AFTER_CYCLES"),
        "HEALTH_ALERT_AFTER_CYCLES",
        DEFAULT_HEALTH_ALERT_AFTER_CYCLES,
    )

    # Ma pierwszenstwo nad KNOWN_STORES (nowe sklepy bez zmiany kodu).
    STORE_URL_SLUGS = parse_store_url_slugs(ENV.get("STORE_URL_SLUGS", ""), {})

    if SMTP_MODE == "gmail":
        SMTP_HOST = "smtp.gmail.com"
        SMTP_PORT = 587
        SMTP_USER = ENV.get("SMTP_USER") or os.environ.get("SMTP_USER")
        SMTP_PASS = ENV.get("SMTP_PASS") or os.environ.get("SMTP_PASS")
        EMAIL_FROM = SMTP_USER
        USE_AUTH = True
    elif SMTP_MODE == "local587":
        SMTP_HOST = ENV.get("SMTP_HOST", "localhost")
        SMTP_PORT = 587
        SMTP_USER = ENV.get("SMTP_USER") or os.environ.get("SMTP_USER")
        SMTP_PASS = ENV.get("SMTP_PASS") or os.environ.get("SMTP_PASS")
        EMAIL_FROM = SMTP_USER
        USE_AUTH = True
    elif SMTP_MODE == "exim":
        # Przekazanie do lokalnego MTA, celowo bez uwierzytelniania.
        SMTP_HOST = ENV.get("SMTP_HOST", "localhost")
        SMTP_PORT = 25
        SMTP_USER = None
        SMTP_PASS = None
        EMAIL_FROM = ENV.get("EMAIL_FROM", "ikea-watch@localhost")
        USE_AUTH = False
    else:  # "disabled"
        SMTP_HOST = None
        SMTP_PORT = None
        SMTP_USER = None
        SMTP_PASS = None
        EMAIL_FROM = None
        USE_AUTH = False

    EMAIL_ENABLED = SMTP_MODE != "disabled"
    # Brak EMAIL_TO -> EMAIL_FROM (dla gmail/local587 to ta sama skrzynka).
    explicit_email_to = ENV.get("EMAIL_TO") or os.environ.get("EMAIL_TO")
    EMAIL_TO = explicit_email_to or EMAIL_FROM

    # Dla exim EMAIL_FROM bywa placeholderem, wiec EMAIL_TO musi byc jawny.
    email_to_for_validation = explicit_email_to if SMTP_MODE == "exim" else EMAIL_TO

    TELEGRAM_BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = ENV.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    validate_notification_config(
        SMTP_MODE,
        SMTP_USER,
        SMTP_PASS,
        USE_AUTH,
        email_to_for_validation,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
    )

    DYNAMIC_STATE = load_dynamic_state()
    SEARCH_TERMS = DYNAMIC_STATE["search_terms"]
    SEARCH_ARTICLE_NUMBERS = DYNAMIC_STATE["search_article_numbers"]
    STORE_IDS = DYNAMIC_STATE["store_ids"]
    NORMALIZED_TERMS = [normalize_text(t) for t in SEARCH_TERMS]
    NORMALIZED_EXCLUDE = [normalize_text(t) for t in KEYWORDS_EXCLUDE]


def store_display_name(store_id) -> str:
    """Nazwa z KNOWN_STORES bez przedrostka "IKEA " albo "Sklep <ID>"."""
    info = KNOWN_STORES.get(str(store_id))
    if not info:
        return f"Sklep {store_id}"
    name = info["name"]
    if name.startswith("IKEA "):
        name = name[len("IKEA "):]
    return name


# ---------------- SPOJNY PROFIL KLIENTA (impersonate + naglowki) ----------------

def choose_client_profile() -> BrowserProfile:
    """Losuje profil klienta; wolane raz na cykl, nigdy miedzy zapytaniami."""
    return random.choice(CLIENT_PROFILES)


def get_active_client_profile() -> BrowserProfile:
    """Profil biezacego cyklu; wywolane poza cyklem losuje i zapamietuje."""
    global CURRENT_CLIENT_PROFILE
    if CURRENT_CLIENT_PROFILE is None:
        CURRENT_CLIENT_PROFILE = choose_client_profile()
    return CURRENT_CLIENT_PROFILE


def build_headers_for_profile(profile: BrowserProfile) -> dict:
    """BASE_HEADERS + user-agent/sec-ch-ua zgodne z profilem (i jego TLS)."""
    headers = dict(BASE_HEADERS)
    headers["user-agent"] = profile.user_agent
    headers["sec-ch-ua"] = profile.sec_ch_ua
    return headers


# ---------------- IKEA API ----------------

def dump_raw(data) -> None:
    try:
        with open(RAW_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class BlockedByServerError(RuntimeError):
    """HTTP 403/429 (blokada/rate limit). Nie jest ponawiany w
    fetch_page_with_retry(), tylko konczy pobieranie, a cykl reaguje
    globalnym backoffem. retry_after_seconds to sparsowany naglowek
    Retry-After od serwera (None, gdy brak lub niepoprawny)."""

    def __init__(self, status_code: int, message: str, retry_after_seconds: "float | None" = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def parse_retry_after_header(raw_value) -> "float | None":
    """Parsuje Retry-After (RFC 9110): sekundy albo data HTTP. Wartosc
    bledna, pusta, ujemna lub przeszla -> None. Nigdy nie rzuca, zeby nie
    przerwac obslugi 403/429."""
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None

    # Format 1: liczba sekund.
    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        if seconds >= 0 and math.isfinite(seconds):
            return seconds
        return None

    # Format 2: data HTTP.
    try:
        target_dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target_dt is None:
        return None
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)

    delta_seconds = (target_dt - datetime.now(timezone.utc)).total_seconds()
    if delta_seconds < 0 or not math.isfinite(delta_seconds):
        return None
    return delta_seconds


def fetch_page_with_retry(store_id: str, page: int) -> dict:
    params = {
        "languageCode": "pl",
        "size": PAGE_SIZE,
        "storeIds": store_id,
        "page": str(page),
    }
    profile = get_active_client_profile()
    headers = build_headers_for_profile(profile)

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(
                API_URL,
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
                impersonate=profile.impersonate,
            )
        except Exception as exc:
            last_error = exc
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in BLOCKING_STATUS_CODES:
                # Bez ponawiania w miejscu (patrz BlockedByServerError).
                retry_after = parse_retry_after_header(resp.headers.get("retry-after"))
                raise BlockedByServerError(
                    resp.status_code,
                    f"HTTP {resp.status_code} (sklep {store_id}, strona {page}): {resp.text[:300]}",
                    retry_after_seconds=retry_after,
                )
            if resp.status_code not in RETRYABLE_STATUS_CODES:
                raise RuntimeError(
                    f"HTTP {resp.status_code} (sklep {store_id}, strona {page}): {resp.text[:300]}"
                )
            last_error = RuntimeError(f"HTTP {resp.status_code}, probuje dalej")

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)

    raise RuntimeError(
        f"Nie udalo sie pobrac danych po {MAX_RETRIES + 1} probach "
        f"(sklep {store_id}, strona {page}): {last_error}"
    )


def fetch_store_offers(store_id: str) -> list:
    all_content = []
    page = 0
    is_last = total_pages = None

    while page < MAX_PAGES:
        data = fetch_page_with_retry(store_id, page)

        if not isinstance(data, dict):
            raise RuntimeError(f"Odpowiedz JSON nie jest obiektem (sklep {store_id}, strona {page}).")

        content = data.get("content")
        if not isinstance(content, list):
            raise RuntimeError(
                f"Brak listy 'content' (sklep {store_id}, strona {page}). "
                f"Klucze: {list(data.keys())[:20]}"
            )

        # Zrzut przed kontrola formatu - przy zmianie API jest wtedy co
        # obejrzec w RAW_DUMP_FILE.
        if page == 0:
            meta = {k: v for k, v in data.items() if k != "content"}
            dump_raw({"store_id": store_id, "page0_metadata": meta, "content": content})

        # Zmiana schematu (np. inne nazwy pol) bez tego dalaby po cichu zero
        # dopasowan; jako blad sklepu trafia do alertu o awarii monitora.
        if content and not any(_looks_like_api_product(item) for item in content):
            first = content[0]
            first_keys = list(first.keys())[:10] if isinstance(first, dict) else type(first).__name__
            raise RuntimeError(
                f"Nieoczekiwany format odpowiedzi API (sklep {store_id}, strona {page}): "
                "zaden element 'content' nie ma pola 'title' i listy 'offers'. "
                f"Klucze pierwszego elementu: {first_keys}"
            )

        all_content.extend(content)

        is_last = data.get("last")
        total_pages = data.get("totalPages")
        if is_last is True:
            break
        if total_pages is not None and page + 1 >= total_pages:
            break
        if not content or len(content) < int(PAGE_SIZE):
            break

        page += 1

    if page >= MAX_PAGES:
        # Petla doszla do limitu bez sygnalu konca od API.
        log(
            f"UWAGA: sklep {store_id}: osiagnieto limit MAX_PAGES={MAX_PAGES} stron, "
            f"a API wskazuje kolejne (last={is_last}, totalPages={total_pages}) - "
            "wyniki sa niepelne.",
            to_stderr=True,
        )

    return all_content


def _looks_like_api_product(item) -> bool:
    """Element 'content' w znanym formacie: slownik z 'title' i lista 'offers'."""
    return isinstance(item, dict) and "title" in item and isinstance(item.get("offers"), list)


def fetch_all_offers() -> tuple:
    """Pobiera oferty ze wszystkich STORE_IDS; blad jednego sklepu nie
    przerywa cyklu. Zwraca (content, store_errors), gdzie store_errors to
    {store_id: wyjatek} - oryginalne wyjatki, zeby rozpoznac 403/429."""
    all_content = []
    store_errors = {}
    for i, store_id in enumerate(STORE_IDS):
        try:
            all_content.extend(fetch_store_offers(store_id))
        except Exception as exc:
            log(f"Blad pobierania sklepu {store_id}: {exc}", to_stderr=True)
            store_errors[store_id] = exc
        if i < len(STORE_IDS) - 1:
            time.sleep(random.uniform(*STORE_JITTER_RANGE))
    return all_content, store_errors


def product_matches(product: dict) -> bool:
    haystack = normalize_text(f"{product.get('title', '')} {product.get('description', '')}")
    text_match = any(term in haystack for term in NORMALIZED_TERMS)

    raw_article_numbers = product.get("articleNumbers") or []
    normalized_article_numbers = {normalize_article_number(an) for an in raw_article_numbers}
    article_match = any(an in normalized_article_numbers for an in SEARCH_ARTICLE_NUMBERS)

    return text_match or article_match


def product_excluded(product: dict) -> bool:
    if not NORMALIZED_EXCLUDE:
        return False
    haystack = normalize_text(f"{product.get('title', '')} {product.get('description', '')}")
    return any(term in haystack for term in NORMALIZED_EXCLUDE)


def resolve_store_slug(store_id) -> str | None:
    """Slug sklepu dla linku rezerwacji; STORE_URL_SLUGS ma pierwszenstwo."""
    slug = STORE_URL_SLUGS.get(str(store_id))
    if slug:
        return slug
    info = KNOWN_STORES.get(str(store_id))
    return info["slug"] if info else None


def build_offer_reservation_link(store_id, offer_number) -> str | None:
    """Link do oferty: .../second-hand/buy-from-ikea/#/<slug>/<offerNumber>.
    "+" (separator spacji w slugu) zostaje niezakodowany, polskie znaki sa
    kodowane. Bez offer_number lub sluga -> None (zamiast mylacego linku)."""
    if not offer_number:
        return None
    slug = resolve_store_slug(store_id)
    if not slug:
        return None
    encoded_slug = urllib.parse.quote(str(slug), safe="+")
    encoded_offer = urllib.parse.quote(str(offer_number), safe="+")
    return f"https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/{encoded_slug}/{encoded_offer}"


def calc_discount_percent(price, original_price):
    if not price or not original_price or original_price <= 0:
        return None
    return round((original_price - price) / original_price * 100)


def flatten_matching_offers(content: list) -> list:
    result = []
    for product in content:
        if product_excluded(product):
            continue
        if not product_matches(product):
            continue

        original_price = product.get("originalPrice")
        article_numbers = product.get("articleNumbers")
        store_id = product.get("storeId")

        for offer in product.get("offers", []):
            price = offer.get("price")
            discount_percent = calc_discount_percent(price, original_price)

            if MIN_DISCOUNT_PERCENT is not None:
                if discount_percent is None or discount_percent < MIN_DISCOUNT_PERCENT:
                    continue
            if MAX_PRICE is not None:
                if price is None or price > MAX_PRICE:
                    continue

            offer_number = offer.get("offerNumber")
            reservation_link = build_offer_reservation_link(store_id, offer_number)

            result.append({
                "offer_uuid": offer.get("offerUuid"),
                "offer_number": offer_number,
                "title": product.get("title"),
                "description": product.get("description"),
                "article_numbers": article_numbers,
                "currency": product.get("currency"),
                "price": price,
                "original_price": original_price,
                "discount_percent": discount_percent,
                "condition": offer.get("productConditionTitle"),
                "condition_desc": offer.get("productConditionDescription"),
                "reason_discount": offer.get("reasonDiscount"),
                "additional_info": offer.get("additionalInfo"),
                "hero_image": product.get("heroImage"),
                "store_id": store_id,
                "reservation_link": reservation_link,
            })
    return result


def _offer_group_key(o: dict) -> tuple:
    """Oferty sa w jednej grupie tylko przy identycznych wszystkich polach
    wspolnych. article_numbers sortowane, bo kolejnosc z API bywa rozna."""
    an = tuple(sorted(o.get("article_numbers") or []))
    return (
        o.get("title"),
        o.get("description"),
        an,
        o.get("currency"),
        o.get("price"),
        o.get("original_price"),
        o.get("discount_percent"),
        o.get("condition"),
        o.get("condition_desc"),
        o.get("reason_discount"),
        o.get("additional_info"),
        o.get("hero_image"),
        o.get("store_id"),
    )


def group_offers_for_notification(offers: list) -> list:
    """Grupuje oferty (tylko na potrzeby tresci powiadomienia), zachowujac
    kolejnosc. Zwraca liste {"shared": {wspolne pola}, "items": [{offer_uuid,
    offer_number, reservation_link}, ...]}."""
    groups: dict = {}
    for o in offers:
        key = _offer_group_key(o)
        if key not in groups:
            groups[key] = {
                "shared": {
                    "title": o.get("title"),
                    "description": o.get("description"),
                    "article_numbers": o.get("article_numbers"),
                    "currency": o.get("currency"),
                    "price": o.get("price"),
                    "original_price": o.get("original_price"),
                    "discount_percent": o.get("discount_percent"),
                    "condition": o.get("condition"),
                    "condition_desc": o.get("condition_desc"),
                    "reason_discount": o.get("reason_discount"),
                    "additional_info": o.get("additional_info"),
                    "hero_image": o.get("hero_image"),
                    "store_id": o.get("store_id"),
                },
                "items": [],
            }
        groups[key]["items"].append({
            "offer_uuid": o.get("offer_uuid"),
            "offer_number": o.get("offer_number"),
            "reservation_link": o.get("reservation_link"),
        })
    return list(groups.values())


def load_seen_state() -> dict:
    """Stan dostawy ofert per kanal: 'seen' (UUID dostarczone wszystkimi
    aktywnymi kanalami) i 'pending' ({uuid: [kanaly, ktore juz
    dostarczyly]}). Stary plik bez 'pending' -> pusty slownik."""
    if not os.path.exists(STATE_FILE):
        return {"seen": set(), "pending": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "seen": set(data.get("seen", [])),
        "pending": {
            uuid: list(channels)
            for uuid, channels in (data.get("pending") or {}).items()
        },
    }


def save_seen_state(seen: set, pending: dict) -> None:
    """Atomowo zapisuje 'seen' i 'pending'."""
    _write_seen_state(seen, pending)


def _write_seen_state(seen: set, pending: dict) -> None:
    """Atomowy zapis (plik .tmp + os.replace); pusty pending jest pomijany."""
    tmp_path = STATE_FILE + ".tmp"
    payload = {"seen": sorted(seen)}
    if pending:
        payload["pending"] = {
            uuid: sorted(channels)
            for uuid, channels in pending.items()
        }
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def format_offer_group_email(group: dict) -> str:
    """Formatuje grupe ofert dla e-maila: wspolny opis + lista egzemplarzy."""
    s = group["shared"]
    discount_txt = f"{s['discount_percent']}%" if s.get("discount_percent") is not None else "n/d"
    lines = [
        f"Produkt: {s['title']} - {s['description']}",
        f"Cena: {s['price']} {s['currency']} (cena wyjsciowa: {s['original_price']} {s['currency']}, rabat: {discount_txt})",
        f"Stan: {s['condition']} ({s.get('condition_desc', '')})",
        f"Powod przeceny: {s.get('reason_discount', '')}",
        f"Info: {s.get('additional_info', '')}",
        f"Numer artykulu: {', '.join(s.get('article_numbers') or [])}",
        f"Sklep (storeId): {s['store_id']} - {store_display_name(s['store_id'])}",
        f"Zdjecie: {s.get('hero_image', '')}",
    ]
    items = group["items"]
    if len(items) == 1:
        item = items[0]
        lines.append(f"Numer oferty: {item['offer_number']}")
        if item.get("reservation_link"):
            lines.append(f"Link do rezerwacji: {item['reservation_link']}")
        else:
            lines.append(
                "Rezerwacja: otwórz stronę \"Okazje na Okrągło online\", "
                "wybierz właściwy sklep i znajdź ofertę po numerze oferty."
            )
    else:
        lines.append(f"{len(items)} sztuk(i):")
        for item in items:
            if item.get("reservation_link"):
                lines.append(f"  - oferta {item['offer_number']} — {item['reservation_link']}")
            else:
                lines.append(f"  - oferta {item['offer_number']} — link niedostepny")
    return "\n".join(lines)


def deliver_email_message(subject: str, body: str) -> None:
    """Wysyla jedna wiadomosc tekstowa przez skonfigurowany SMTP (wspolne dla
    powiadomien o ofertach i alertow o dostepie)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
        server.ehlo()
        if USE_AUTH:
            tls_context = ssl.create_default_context()
            if not VERIFY_TLS:
                tls_context.check_hostname = False
                tls_context.verify_mode = ssl.CERT_NONE
            server.starttls(context=tls_context)
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def send_email(new_offers) -> None:
    groups = group_offers_for_notification(new_offers)
    titles = ", ".join(sorted({g["shared"]["title"] for g in groups if g["shared"].get("title")}))
    subject = f"IKEA Okazje: nowa oferta - {titles}"

    blocks = [format_offer_group_email(g) for g in groups]
    body = (
        "Znaleziono nowe oferty dla: " + ", ".join(SEARCH_TERMS) + "\n\n"
        + "\n\n".join(blocks)
        + "\n\nSprawdz strone:\nhttps://www.ikea.com/pl/pl/second-hand/buy-from-ikea/"
    )

    deliver_email_message(subject, body)


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def escape_html_attr(text: str) -> str:
    """Jak escape_html(), ale dodatkowo escapuje cudzyslowy - do uzycia w
    atrybutach HTML (np. href="...")."""
    return escape_html(text).replace('"', "&quot;").replace("'", "&#39;")


def _safe_truncate_html(raw_text: str, max_escaped_len: int) -> str:
    """Skraca surowy tekst tak, by po escape_html() (z '…') miescil sie w
    max_escaped_len. Ciecie przed escapowaniem nie przetnie encji ani tagu."""
    if not raw_text or max_escaped_len <= 0:
        return ""

    escaped = escape_html(raw_text)
    if len(escaped) <= max_escaped_len:
        return escaped

    ellipsis = "…"
    ellipsis_len = len(escape_html(ellipsis))  # len("…") == 1
    if max_escaped_len <= ellipsis_len:
        return ellipsis[:max_escaped_len]

    target_len = max_escaped_len - ellipsis_len

    # Binary search na liczbie znakow surowego tekstu
    low = 0
    high = len(raw_text)
    best_len = 0
    while low <= high:
        mid = (low + high) // 2
        candidate = raw_text[:mid]
        if len(escape_html(candidate)) <= target_len:
            best_len = mid
            low = mid + 1
        else:
            high = mid - 1

    return escape_html(raw_text[:best_len]) + ellipsis


def _format_meta_line_telegram(shared: dict) -> str:
    currency = escape_html(str(shared["currency"])) if shared.get("currency") is not None else ""
    condition = escape_html(str(shared["condition"])) if shared.get("condition") is not None else "n/d"
    price_txt = escape_html(str(shared["price"])) if shared.get("price") is not None else "n/d"
    original_price_txt = escape_html(str(shared["original_price"])) if shared.get("original_price") is not None else "n/d"
    discount_txt = f"{shared['discount_percent']}%" if shared.get("discount_percent") is not None else "n/d"
    if shared.get("store_id"):
        store_name = escape_html(store_display_name(shared["store_id"]))
    else:
        store_name = "brak"

    return (
        f"{store_name} | {price_txt} {currency} "
        f"(z {original_price_txt}, rabat {discount_txt}) | stan: {condition}"
    )


def _minimal_group_header_telegram(shared: dict) -> str:
    """Najkrotszy mozliwy naglowek (tytul '…', bez opisu) - do sprawdzenia,
    czy pojedyncza oferta w ogole zmiesci sie w wiadomosci."""
    meta_line = _format_meta_line_telegram(shared)
    return f"<b>…</b>\n{meta_line}"


def _format_group_header_telegram(shared: dict, max_len: int = None) -> str:
    """Naglowek grupy dla Telegrama. Przy max_len skraca najpierw opis, a
    tytul dopiero w ostatecznosci - zawsze przed escapowaniem HTML."""
    raw_title = str(shared.get("title") or "")
    raw_desc = str(shared.get("description") or "")
    meta_line = _format_meta_line_telegram(shared)

    safe_title = escape_html(raw_title)
    safe_desc = escape_html(raw_desc)

    if raw_desc:
        full_header = f"<b>{safe_title}</b> — {safe_desc}\n{meta_line}"
    else:
        full_header = f"<b>{safe_title}</b>\n{meta_line}"

    if max_len is None or len(full_header) <= max_len:
        return full_header

    # Za dlugi: skracamy opis, zachowujac pelny tytul.
    prefix = f"<b>{safe_title}</b>"
    if raw_desc:
        overhead = len(prefix) + len(" — \n") + len(meta_line)
        avail_desc = max_len - overhead
        if avail_desc >= 5:
            short_desc = _safe_truncate_html(raw_desc, avail_desc)
            if short_desc:
                candidate = f"{prefix} — {short_desc}\n{meta_line}"
                if len(candidate) <= max_len:
                    return candidate

    # Opis sie nie miesci: bez opisu, z (ewentualnie skroconym) tytulem.
    overhead = len("<b></b>\n") + len(meta_line)
    avail_title = max_len - overhead
    if avail_title >= 1:
        short_title = _safe_truncate_html(raw_title, avail_title)
        candidate = f"<b>{short_title}</b>\n{meta_line}"
        if len(candidate) <= max_len:
            return candidate

    short_title = _safe_truncate_html(raw_title, max(1, avail_title))
    return f"<b>{short_title}</b>\n{meta_line}"


def _format_offer_item_telegram(item: dict) -> str:
    """Formatuje jeden egzemplarz oferty (numer + link) dla Telegrama."""
    offer_number = item.get("offer_number")
    offer_number_txt = escape_html(str(offer_number)) if offer_number else "brak"
    if item.get("reservation_link"):
        safe_link = escape_html_attr(item["reservation_link"])
        return f'• oferta {offer_number_txt} — <a href="{safe_link}">Rezerwuj</a>'
    else:
        return f"• oferta {offer_number_txt} — link niedostępny"


# Limit Telegram API to 4096 znakow; uzywamy bezpiecznego marginesu.
TELEGRAM_MESSAGE_CHAR_LIMIT = 4096
TELEGRAM_SAFE_LIMIT = 4000


def telegram_preview_image_url(shared: dict) -> "str | None":
    """Zdjecie produktu (hero_image) do podgladu linku albo None - wtedy
    Telegram pokazuje podglad pierwszego linku z tresci."""
    url = shared.get("hero_image")
    if isinstance(url, str) and url.startswith("https://"):
        return url
    return None


def split_telegram_messages(groups: list) -> list:
    """Same teksty wiadomosci z split_telegram_messages_with_previews()."""
    return [text for text, _ in split_telegram_messages_with_previews(groups)]


def split_telegram_messages_with_previews(groups: list) -> list:
    """Dzieli grupy ofert na gotowe wiadomosci HTML Telegrama i zwraca liste
    (tekst, adres zdjecia pierwszej grupy w wiadomosci albo None). Gwarancje:
    - zadna wiadomosc nie przekracza TELEGRAM_SAFE_LIMIT;
    - kazda oferta (numer + pelny link) jest w dokladnie jednej wiadomosci,
      podzial nigdy nie tnie tagu, encji ani wiersza oferty;
    - pelny tytul ma priorytet: za duza grupa jest dzielona na kilka
      wiadomosci z powtorzonym naglowkiem "(cd.)"; najpierw skracany jest
      opis, tytul tylko w ostatecznosci, zawsze przed escapowaniem HTML;
    - wiersz oferty, ktory nie zmiesci sie nawet z minimalnym naglowkiem,
      powoduje ValueError, zanim cokolwiek zostanie wyslane."""
    if not groups:
        return []

    banner = "<b>IKEA Okazje - nowa oferta</b>"
    separator = "\n\n"

    # Walidacja wstepna: kazda oferta musi sie zmiescic z minimalnym naglowkiem.
    for group in groups:
        shared = group["shared"]
        min_hdr = _minimal_group_header_telegram(shared)
        min_overhead = len(banner) + len(separator) + len(min_hdr) + len("\n")
        if len(group["items"]) > 1:
            min_overhead += len("(cd.)\n")

        for item in group["items"]:
            item_line = _format_offer_item_telegram(item)
            if len(item_line) + min_overhead > TELEGRAM_SAFE_LIMIT:
                offer_num = item.get("offer_number") or item.get("offer_uuid") or "brak"
                raise ValueError(
                    f"Wiersz oferty {offer_num} jest zbyt dlugi ({len(item_line)} zn.), "
                    f"aby zmiescic sie w limicie wiadomosci Telegrama ({TELEGRAM_SAFE_LIMIT} zn.) "
                    f"nawet z minimalnym naglowkiem."
                )

    messages = []
    current_parts = [banner]
    current_len = len(banner)
    # Podglad wiadomosci = zdjecie pierwszej grupy w tej wiadomosci.
    current_preview = None

    for group in groups:
        shared = group["shared"]
        items = group["items"]
        item_lines = [_format_offer_item_telegram(item) for item in items]
        group_preview = telegram_preview_image_url(shared)

        if len(items) == 1:
            items_overhead = len("\n") + len(item_lines[0])
            count_label = ""
        else:
            count_label = f"{len(items)} nowych sztuk:"
            items_overhead = len("\n") + len(count_label) + sum(1 + len(l) for l in item_lines)

        full_hdr = _format_group_header_telegram(shared)
        full_hdr_len = len(full_hdr)

        # Proba 1: cala grupa z pelnym naglowkiem doklejona do biezacej wiadomosci.
        avail_space_in_current = TELEGRAM_SAFE_LIMIT - current_len - len(separator)
        if items_overhead + full_hdr_len <= avail_space_in_current:
            if len(items) == 1:
                full_group_block = f"{full_hdr}\n{item_lines[0]}"
            else:
                full_group_block = f"{full_hdr}\n{count_label}\n" + "\n".join(item_lines)

            if current_len + len(separator) + len(full_group_block) <= TELEGRAM_SAFE_LIMIT:
                if len(current_parts) == 1:
                    current_preview = group_preview
                current_parts.append(full_group_block)
                current_len += len(separator) + len(full_group_block)
                continue

        # Proba 2: nie miesci sie - zamykamy biezaca wiadomosc (jesli niepusta).
        if len(current_parts) > 1:
            messages.append(("\n\n".join(current_parts), current_preview))
            current_parts = [banner]
            current_len = len(banner)

        # Proba 3: cala grupa z pelnym naglowkiem w nowej wiadomosci.
        avail_space_fresh = TELEGRAM_SAFE_LIMIT - len(banner) - len(separator)
        if items_overhead + full_hdr_len <= avail_space_fresh:
            if len(items) == 1:
                full_group_block = f"{full_hdr}\n{item_lines[0]}"
            else:
                full_group_block = f"{full_hdr}\n{count_label}\n" + "\n".join(item_lines)

            if len(banner) + len(separator) + len(full_group_block) <= TELEGRAM_SAFE_LIMIT:
                current_preview = group_preview
                current_parts.append(full_group_block)
                current_len = len(banner) + len(separator) + len(full_group_block)
                continue

        # Proba 4: grupa za duza na jedna wiadomosc - dzielimy jej oferty.
        if len(current_parts) > 1:
            messages.append(("\n\n".join(current_parts), current_preview))
            current_parts = [banner]
            current_len = len(banner)

        continuation_idx = 0
        while continuation_idx < len(item_lines):
            first_item_line = item_lines[continuation_idx]
            if continuation_idx == 0:
                label = f"\n{len(items)} nowych sztuk:" if len(items) > 1 else ""
            else:
                label = "\n(cd.)"

            avail_for_hdr = (
                TELEGRAM_SAFE_LIMIT
                - len(banner)
                - len(separator)
                - len(label)
                - 1
                - len(first_item_line)
            )
            header = _format_group_header_telegram(shared, max_len=avail_for_hdr)
            msg_header = banner + separator + header + label

            msg_parts = [msg_header, first_item_line]
            msg_len = len(msg_header) + 1 + len(first_item_line)
            continuation_idx += 1

            while continuation_idx < len(item_lines):
                next_line = item_lines[continuation_idx]
                if msg_len + 1 + len(next_line) <= TELEGRAM_SAFE_LIMIT:
                    msg_parts.append(next_line)
                    msg_len += 1 + len(next_line)
                    continuation_idx += 1
                else:
                    break

            messages.append(("\n".join(msg_parts), group_preview))

        current_parts = [banner]
        current_len = len(banner)

    if len(current_parts) > 1:
        messages.append(("\n\n".join(current_parts), current_preview))

    # Niezmiennik dlugosci - ostatnia linia obrony przed wyslaniem.
    for idx, (msg, _) in enumerate(messages, 1):
        if len(msg) > TELEGRAM_SAFE_LIMIT:
            raise ValueError(
                f"Wiadomosc {idx}/{len(messages)} przekracza bezpieczny limit "
                f"{TELEGRAM_SAFE_LIMIT} znakow (faktyczna dlugosc: {len(msg)})."
            )

    return messages


# HTTP 429 z Bot API: jedno ponowienie po retry_after (brak -> 5 s), ale
# nie dluzej niz 60 s, zeby nie wstrzymywac cyklu.
TELEGRAM_RATE_LIMIT_DEFAULT_WAIT_SECONDS = 5
TELEGRAM_RATE_LIMIT_MAX_WAIT_SECONDS = 60
# Przerwa miedzy wiadomosciami jednej serii (Telegram limituje ok. 1
# wiadomosc/s na czat).
TELEGRAM_SERIES_DELAY_SECONDS = 1.0


class TelegramApiError(RuntimeError):
    """Blad Bot API. Tresc nigdy nie zawiera URL ani tokenu bota."""

    def __init__(self, message: str, status_code: "int | None" = None,
                 retry_after: "float | None" = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _hide_telegram_token(text) -> str:
    """Maskuje token bota, gdyby wrocil w tresci bledu."""
    text = str(text)
    if TELEGRAM_BOT_TOKEN:
        text = text.replace(TELEGRAM_BOT_TOKEN, "[ukryte]")
    return text


def _telegram_error_details(body: bytes) -> tuple:
    """(description, retry_after) z JSON bledu Bot API; brakujace albo
    niepoprawne pola -> None."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        description = None
    retry_after = None
    parameters = data.get("parameters")
    if isinstance(parameters, dict):
        value = parameters.get("retry_after")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        ):
            retry_after = float(value)
    return description, retry_after


def telegram_api_call(method: str, payload: dict, timeout: float):
    """POST JSON do metody Bot API, zwraca pole 'result'. Bledy jako
    TelegramApiError z kodem HTTP i opisem od Telegrama. URL zawiera token,
    wiec str() i .url wyjatkow urllib nie trafiaja do komunikatu."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read()
        except Exception:
            error_body = b""
        description, retry_after = _telegram_error_details(error_body)
        message = f"Telegram API ({method}) zwrocilo HTTP {exc.code}"
        if description:
            message += f": {_hide_telegram_token(description)}"
        raise TelegramApiError(message, exc.code, retry_after) from None
    except (urllib.error.URLError, OSError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        reason_text = str(reason) or type(reason).__name__
        raise TelegramApiError(
            f"Blad polaczenia z Telegram API ({method}): {_hide_telegram_token(reason_text)}"
        ) from None

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise TelegramApiError(
            f"Telegram API ({method}) zwrocilo odpowiedz, ktora nie jest JSON."
        ) from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        description = data.get("description") if isinstance(data, dict) else None
        raise TelegramApiError(
            f"Telegram API ({method}) zwrocilo blad: "
            f"{_hide_telegram_token(description or 'brak opisu')}"
        )
    return data.get("result")


def telegram_send_message(text: str, chat_id=None, preview_url=None, disable_preview=False) -> None:
    """Wysyla wiadomosc HTML. preview_url - obraz w podgladzie linku (bez
    niego Telegram bierze pierwszy link z tresci), disable_preview - bez
    podgladu. Po HTTP 429 czeka retry_after i ponawia raz; gdy w trakcie
    czekania przyjdzie SIGTERM, konczy bledem bez ponowienia."""
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    # link_preview_options (Bot API 7.0) zastepuje disable_web_page_preview.
    if disable_preview:
        payload["link_preview_options"] = {"is_disabled": True}
    elif preview_url:
        payload["link_preview_options"] = {"url": preview_url, "prefer_large_media": True}
    try:
        telegram_api_call("sendMessage", payload, REQUEST_TIMEOUT)
        return
    except TelegramApiError as exc:
        if exc.status_code != 429:
            raise
        rate_limit_error = exc

    delay = min(
        rate_limit_error.retry_after or TELEGRAM_RATE_LIMIT_DEFAULT_WAIT_SECONDS,
        TELEGRAM_RATE_LIMIT_MAX_WAIT_SECONDS,
    )
    log(f"Telegram: limit wysylania (HTTP 429) - ponawiam za {delay:.0f}s.", to_stderr=True)
    if SHUTDOWN_EVENT.wait(delay):
        raise TelegramApiError(
            f"{rate_limit_error} (zamykanie procesu - bez ponowienia)", 429
        )
    telegram_api_call("sendMessage", payload, REQUEST_TIMEOUT)


def send_telegram(new_offers) -> None:
    """Wysyla oferty Telegramem. Cala seria jest budowana i walidowana
    (split_telegram_messages_with_previews()) przed wyslaniem pierwszej
    wiadomosci; kazda dostaje w podgladzie zdjecie swojej pierwszej grupy."""
    groups = group_offers_for_notification(new_offers)
    messages = split_telegram_messages_with_previews(groups)
    if not messages:
        return

    sent_count = 0
    for index, (msg, preview_url) in enumerate(messages):
        if index > 0:
            # Po SIGTERM wait() wraca od razu - reszta serii idzie bez
            # przerwy, zeby cykl skonczyl sie szybko.
            SHUTDOWN_EVENT.wait(TELEGRAM_SERIES_DELAY_SECONDS)
        try:
            telegram_send_message(msg, preview_url=preview_url)
            sent_count += 1
        except Exception as exc:
            raise RuntimeError(
                f"Telegram: wyslano {sent_count}/{len(messages)} wiadomosci, "
                f"blad przy wiadomosci #{sent_count + 1}: {exc}"
            )


# ---------------- POWIADOMIENIA O UTRACIE/ODZYSKANIU DOSTEPU (403/429) ----------------
# Alert o dostepie do API jako calosci (nie o ofertach). Stan w bloku
# 'ikea_access_notification', kanaly te same co dla ofert.

ACCESS_OUTAGE_EMAIL_SUBJECT = "IKEA Okazje: brak dostepu (HTTP 403/429)"
ACCESS_OUTAGE_MESSAGE = (
    "IKEA odrzuca zapytania monitora (HTTP 403/429).\n"
    "Monitor nadal dziala, ale aktualnie nie moze pobrac ofert.\n"
    "Kolejne identyczne bledy nie beda wysylaly nastepnych powiadomien."
)
ACCESS_OUTAGE_TELEGRAM_MESSAGE = "⚠️ " + ACCESS_OUTAGE_MESSAGE

ACCESS_RECOVERY_EMAIL_SUBJECT = "IKEA Okazje: dostep odzyskany"
ACCESS_RECOVERY_MESSAGE = (
    "Monitor IKEA ponownie moze pobierac oferty.\n"
    "Dostep do danych zostal odzyskany."
)
ACCESS_RECOVERY_TELEGRAM_MESSAGE = "✅ " + ACCESS_RECOVERY_MESSAGE


def notify_status_message(email_subject: str, email_body: str, telegram_text: str) -> list:
    """Wysyla komunikat o stanie monitora (dostep do API, awaria) wszystkimi
    wlaczonymi kanalami. telegram_text idzie z parse_mode=HTML. Zwraca
    liste bledow (po jednym na kanal), zeby wolajacy rozpoznal calkowita
    porazke dostawy."""
    errors = []

    if EMAIL_ENABLED:
        try:
            deliver_email_message(email_subject, email_body)
        except Exception as exc:
            errors.append(f"e-mail: {exc}")

    if TELEGRAM_ENABLED:
        try:
            telegram_send_message(telegram_text, disable_preview=True)
        except Exception as exc:
            errors.append(f"telegram: {exc}")

    return errors


def handle_access_notification_state(all_stores_blocked: bool, blocked_status_code) -> None:
    """Aktualizuje stan alertu o dostepie po cyklu kwalifikujacym sie: pelna
    blokada 403/429 wszystkich sklepow albo pelny sukces. Cykle czesciowe
    obsluguje reset_pending_outage_after_partial_cycle().

    Alert o utracie wymaga DWOCH kolejnych w pelni zablokowanych cykli
    (pierwszy tylko ustawia pending_outage), zeby pojedyncza przejsciowa
    blokada nie dawala pary alertow. Odstep miedzy cyklami wynika z
    istniejacego harmonogramu (backoff/Retry-After) - ta funkcja niczego
    nie planuje. Pelny sukces: przy pending_outage czysci stan po cichu,
    przy outage_active wysyla jeden alert o odzyskaniu. Gdy dostawa alertu
    zawiedzie na wszystkich kanalach, stan sie nie zmienia i nastepny
    kwalifikujacy cykl sprobuje ponownie."""
    state = get_access_notification_state()
    active_channels = (1 if EMAIL_ENABLED else 0) + (1 if TELEGRAM_ENABLED else 0)

    if all_stores_blocked:
        if state.get("outage_active"):
            return  # alert juz wyslany

        if not state.get("pending_outage"):
            # Pierwszy taki cykl: tylko trwale oznaczamy, bez alertu.
            set_access_notification_state(False, blocked_status_code, pending_outage=True)
            log(
                "Pierwszy w pelni zablokowany cykl (HTTP 403/429 na wszystkich "
                "skonfigurowanych sklepach) - oznaczam jako oczekujaca utrata "
                "dostepu, bez wysylania alertu. Alert pojdzie tylko, jesli "
                "NASTEPNY kwalifikujacy cykl (wg istniejacego harmonogramu/"
                "backoffu) rowniez bedzie w pelni zablokowany."
            )
            return

        # Drugi kolejny taki cykl - potwierdzenie, wysylamy alert.
        errors = notify_status_message(
            ACCESS_OUTAGE_EMAIL_SUBJECT, ACCESS_OUTAGE_MESSAGE, ACCESS_OUTAGE_TELEGRAM_MESSAGE
        )
        for err in errors:
            log(f"Blad wysylki alertu o utracie dostepu ({err})", to_stderr=True)
        if errors and len(errors) == active_channels:
            # pending_outage zostaje - nastepny cykl ponowi wysylke.
            return

        set_access_notification_state(True, blocked_status_code, pending_outage=False)
        log(
            "Wyslano alert o utracie dostepu do IKEA (HTTP 403/429) - "
            "potwierdzone dwoma kolejnymi w pelni zablokowanymi cyklami."
        )
    else:
        if state.get("pending_outage"):
            # Sukces przed potwierdzeniem - alertu nie bylo, czyscimy po cichu.
            set_access_notification_state(False, None, pending_outage=False)
            log(
                "Pelny sukces po oczekujacej (niepotwierdzonej) utracie "
                "dostepu - czyszcze stan bez wysylania jakiegokolwiek "
                "powiadomienia."
            )
            return

        if not state.get("outage_active"):
            return  # nie bylo utraty dostepu

        errors = notify_status_message(
            ACCESS_RECOVERY_EMAIL_SUBJECT, ACCESS_RECOVERY_MESSAGE, ACCESS_RECOVERY_TELEGRAM_MESSAGE
        )
        for err in errors:
            log(f"Blad wysylki alertu o odzyskaniu dostepu ({err})", to_stderr=True)
        if errors and len(errors) == active_channels:
            return

        set_access_notification_state(False, None, pending_outage=False)
        log("Wyslano alert o odzyskaniu dostepu do IKEA.")


def reset_pending_outage_after_partial_cycle() -> None:
    """Cykl czesciowy/mieszany (np. jeden sklep OK, drugi 403; albo 403 i
    5xx) nie jest ani kwalifikujaca sie porazka, ani pelnym sukcesem.
    Zeruje tylko niepotwierdzona sekwencje (pending_outage i jej
    last_status_code) - kolejna pelna blokada zaczyna potwierdzanie od
    nowa. Wyslany alert (outage_active) zostaje do pelnego sukcesu; blok
    'blocking_backoff' nie jest tu ruszany."""
    state = get_access_notification_state()
    if not state.get("pending_outage"):
        return
    set_access_notification_state(
        state.get("outage_active", False),
        None,
        pending_outage=False,
    )
    log(
        "Czesciowy/mieszany cykl (nie pelna blokada 403/429, nie pelny "
        "sukces) - zeruje oczekujaca, niepotwierdzona sekwencje utraty "
        "dostepu. Kolejna pelna blokada 403/429 zacznie nowe, dwucyklowe "
        "potwierdzanie od nowa."
    )


# ---------------- POWIADOMIENIA O DLUZSZEJ AWARII MONITORA ----------------
# Alert, gdy kolejne cykle koncza sie bledem innym niz pelna blokada 403/429
# (ta ma wlasny alert powyzej). Stan w bloku 'health', kanaly te same co
# dla ofert.

# Wynik cyklu dla bloku 'health' (patrz run_ikea_check_cycle()).
CYCLE_SKIPPED = "skipped"
CYCLE_HEALTHY = "healthy"
CYCLE_UNHEALTHY = "unhealthy"

HEALTH_ALERT_EMAIL_SUBJECT = "IKEA Okazje: monitor nie dziala poprawnie"
HEALTH_RECOVERY_EMAIL_SUBJECT = "IKEA Okazje: monitor znow dziala"
HEALTH_RECOVERY_MESSAGE = (
    "Monitor IKEA znow poprawnie sprawdza oferty.\n"
    "Ostatni cykl zakonczyl sie bez bledow."
)


def format_local_timestamp(epoch) -> str:
    """Czas lokalny 'RRRR-MM-DD GG:MM:SS' albo 'nigdy' dla None."""
    if epoch is None:
        return "nigdy"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def build_health_alert_message(state: dict) -> str:
    """Tresc alertu o awarii (tekst; dla Telegrama escapowany przez wolajacego)."""
    return (
        "Monitor IKEA nie moze poprawnie sprawdzic ofert.\n"
        f"Kolejne nieudane cykle: {state['consecutive_failed_cycles']}\n"
        "Ostatnie udane sprawdzenie: "
        f"{format_local_timestamp(state['last_successful_check_at'])}\n"
        f"Ostatni blad: {state['last_error'] or 'brak opisu'}\n"
        "Szczegoly w logach: journalctl -u ikea-okazje (systemd) albo plik "
        "logu z crona.\n"
        "Kolejne nieudane cykle nie beda wysylaly nastepnych powiadomien."
    )


def exception_text(exc: BaseException) -> str:
    """Tresc wyjatku albo nazwa typu, gdy tresc jest pusta (np. TimeoutError())."""
    return str(exc) or type(exc).__name__


def describe_store_errors(store_errors: dict) -> str:
    """Krotki opis bledow sklepow dla bloku 'health'."""
    return "; ".join(
        f"sklep {store_id}: {exception_text(exc)}" for store_id, exc in store_errors.items()
    )


def sanitize_error_description(text: str) -> str:
    """Opis bledu trafia do pliku stanu i do alertu: bez sekretow i adresow
    URL, w jednej linii, max HEALTH_LAST_ERROR_MAX_CHARS znakow."""
    text = str(text)
    for secret in (TELEGRAM_BOT_TOKEN, SMTP_PASS):
        if secret:
            text = text.replace(secret, "[ukryte]")
    text = re.sub(r"https?://\S+", "[url]", text)
    text = " ".join(text.split())
    if len(text) > HEALTH_LAST_ERROR_MAX_CHARS:
        text = text[:HEALTH_LAST_ERROR_MAX_CHARS - 3] + "..."
    return text


def _all_channels_failed(errors: list) -> bool:
    """Dostawa komunikatu zawiodla na wszystkich wlaczonych kanalach."""
    return bool(errors) and len(errors) == len(_enabled_channels())


def update_health_state(outcome: str, error_description=None) -> None:
    """Aktualizuje blok 'health' po cyklu jednym zapisem na dysk i wysyla co
    najwyzej jeden komunikat: alert po HEALTH_ALERT_AFTER_CYCLES kolejnych
    nieudanych cyklach (0 - bez alertu) albo informacje o powrocie do
    dzialania po wyslanym alercie. Gdy dostawa zawiedzie na wszystkich
    kanalach, alert_active sie nie zmienia i nastepny taki cykl ponawia.
    Cykl pominiety (CYCLE_SKIPPED) niczego nie zmienia."""
    if outcome == CYCLE_SKIPPED:
        return
    state = get_health_state()

    if outcome == CYCLE_HEALTHY:
        state["consecutive_failed_cycles"] = 0
        state["last_error"] = None
        state["last_successful_check_at"] = time.time()
        if state["alert_active"]:
            errors = notify_status_message(
                HEALTH_RECOVERY_EMAIL_SUBJECT,
                HEALTH_RECOVERY_MESSAGE,
                "✅ " + escape_html(HEALTH_RECOVERY_MESSAGE),
            )
            for err in errors:
                log(f"Blad wysylki powiadomienia o powrocie monitora ({err})", to_stderr=True)
            if not _all_channels_failed(errors):
                state["alert_active"] = False
                log("Wyslano powiadomienie o powrocie monitora do poprawnego dzialania.")
        set_health_state(state)
        return

    state["consecutive_failed_cycles"] += 1
    state["last_error"] = sanitize_error_description(error_description or "nieznany blad")
    failed_cycles = state["consecutive_failed_cycles"]
    log(
        f"Nieudany cykl monitora ({failed_cycles} z rzedu): {state['last_error']}",
        to_stderr=True,
    )
    threshold = HEALTH_ALERT_AFTER_CYCLES
    if threshold > 0 and failed_cycles >= threshold and not state["alert_active"]:
        message = build_health_alert_message(state)
        errors = notify_status_message(
            HEALTH_ALERT_EMAIL_SUBJECT, message, "⚠️ " + escape_html(message)
        )
        for err in errors:
            log(f"Blad wysylki alertu o awarii monitora ({err})", to_stderr=True)
        if not _all_channels_failed(errors):
            state["alert_active"] = True
            log(f"Wyslano alert o awarii monitora ({failed_cycles} nieudanych cykli z rzedu).")
    set_health_state(state)


# ---------------- KOMENDY TELEGRAMA ----------------

def load_telegram_offset() -> int:
    if not os.path.exists(TELEGRAM_OFFSET_FILE):
        return 0
    try:
        with open(TELEGRAM_OFFSET_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("offset", 0)
    except (OSError, json.JSONDecodeError):
        return 0


def save_telegram_offset(offset: int) -> None:
    tmp_path = TELEGRAM_OFFSET_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)
    os.replace(tmp_path, TELEGRAM_OFFSET_FILE)


def telegram_get_updates(offset: int) -> list:
    try:
        result = telegram_api_call(
            "getUpdates", {"offset": offset, "timeout": 0}, REQUEST_TIMEOUT
        )
    except TelegramApiError as exc:
        if exc.status_code == 409:
            # Telegram oddaje aktualizacje tylko jednemu odbiorcy.
            raise TelegramApiError(
                f"{exc}. Prawdopodobnie dziala druga instancja skryptu z tym "
                "samym botem (cron, systemd albo reczne uruchomienie) albo bot "
                "ma ustawiony webhook - patrz README.",
                409,
            ) from None
        raise
    return result if isinstance(result, list) else []


def format_status_message() -> str:
    lines = ["<b>Aktualny monitoring:</b>", "", "Aktywne sklepy:"]
    if STORE_IDS:
        for sid in STORE_IDS:
            lines.append(f"- {escape_html(store_display_name(sid))} ({escape_html(str(sid))})")
    else:
        lines.append("(brak - monitoring nie pobierze zadnych ofert)")
    lines.append("")
    lines.append(f"Slowa kluczowe: {escape_html(', '.join(SEARCH_TERMS)) or '(brak)'}")
    lines.append(f"Numery artykulu: {escape_html(', '.join(SEARCH_ARTICLE_NUMBERS)) or '(brak)'}")
    if KEYWORDS_EXCLUDE:
        lines.append(f"Czarna lista: {escape_html(', '.join(KEYWORDS_EXCLUDE))}")
    if MIN_DISCOUNT_PERCENT is not None:
        lines.append(f"Min. rabat: {MIN_DISCOUNT_PERCENT}%")
    if MAX_PRICE is not None:
        lines.append(f"Maks. cena: {MAX_PRICE}")
    lines.append(f"Tryb pracy: {escape_html(RUN_MODE)}")
    return "\n".join(lines)


def format_stores_message() -> str:
    lines = ["<b>Aktywne sklepy:</b>"]
    if STORE_IDS:
        for sid in STORE_IDS:
            lines.append(f"- {escape_html(store_display_name(sid))} ({escape_html(str(sid))})")
    else:
        lines.append("(brak - monitoring nie pobierze zadnych ofert)")
    lines.append("")
    lines.append("<b>Wszystkie dostepne sklepy:</b>")
    for sid in KNOWN_STORES:
        lines.append(f"- {store_display_name(sid)} ({sid})")
    lines.append("")
    lines.append(
        "Uzyj /dodajsklep &lt;ID&gt;, np. /dodajsklep 1224, zeby dodac sklep, "
        "albo /usunsklep &lt;ID&gt;, np. /usunsklep 294, zeby go usunac."
    )
    return "\n".join(lines)


def format_help_message() -> str:
    return (
        "<b>Komendy:</b>\n"
        "/dodaj &lt;slowo&gt; - dodaj slowo kluczowe\n"
        "/usun &lt;slowo&gt; - usun slowo kluczowe\n"
        "/numer &lt;nr&gt; - dodaj numer artykulu\n"
        "/usunnumer &lt;nr&gt; - usun numer artykulu\n"
        "/sklepy - pokaz aktywne i dostepne sklepy\n"
        "/dodajsklep &lt;ID&gt; - dodaj sklep do monitoringu\n"
        "/usunsklep &lt;ID&gt; - usun sklep z monitoringu\n"
        "/status - pokaz aktualny monitoring\n"
        "/pomoc - ta lista"
    )


def handle_command(cmd: str, arg: str) -> str:
    """Zwraca tekst odpowiedzi. Modyfikuje globalny SEARCH_TERMS/
    SEARCH_ARTICLE_NUMBERS/STORE_IDS i zapisuje DYNAMIC_STATE, jesli trzeba."""
    global SEARCH_TERMS, SEARCH_ARTICLE_NUMBERS, STORE_IDS

    arg = arg.strip()

    if cmd in ("/dodaj", "/add"):
        if not arg:
            return "Podaj slowo do dodania, np. /dodaj stall"
        safe_arg = escape_html(arg)
        if normalize_text(arg) in NORMALIZED_TERMS:
            return f"'{safe_arg}' juz jest na liscie."
        SEARCH_TERMS.append(arg)
        refresh_normalized_terms()
        DYNAMIC_STATE["search_terms"] = SEARCH_TERMS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Dodano '{safe_arg}'. Aktualna lista: {escape_html(', '.join(SEARCH_TERMS))}"

    if cmd in ("/usun", "/remove"):
        if not arg:
            return "Podaj slowo do usuniecia, np. /usun stall"
        safe_arg = escape_html(arg)
        norm_arg = normalize_text(arg)
        new_terms = [t for t in SEARCH_TERMS if normalize_text(t) != norm_arg]
        if len(new_terms) == len(SEARCH_TERMS):
            return f"'{safe_arg}' nie bylo na liscie."
        SEARCH_TERMS = new_terms
        refresh_normalized_terms()
        DYNAMIC_STATE["search_terms"] = SEARCH_TERMS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Usunieto '{safe_arg}'. Aktualna lista: {escape_html(', '.join(SEARCH_TERMS)) or '(brak)'}"

    if cmd == "/numer":
        if not arg:
            return "Podaj numer artykulu, np. /numer 90557419"
        normalized_arg = normalize_article_number(arg)
        safe_arg = escape_html(arg)
        if not normalized_arg:
            return f"'{safe_arg}' nie zawiera zadnej cyfry - podaj poprawny numer artykulu."
        if normalized_arg in SEARCH_ARTICLE_NUMBERS:
            return f"Numer '{safe_arg}' juz jest na liscie."
        SEARCH_ARTICLE_NUMBERS.append(normalized_arg)
        DYNAMIC_STATE["search_article_numbers"] = SEARCH_ARTICLE_NUMBERS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Dodano numer '{safe_arg}'. Aktualna lista: {escape_html(', '.join(SEARCH_ARTICLE_NUMBERS))}"

    if cmd == "/usunnumer":
        if not arg:
            return "Podaj numer artykulu do usuniecia, np. /usunnumer 90557419"
        normalized_arg = normalize_article_number(arg)
        safe_arg = escape_html(arg)
        if not normalized_arg or normalized_arg not in SEARCH_ARTICLE_NUMBERS:
            return f"Numeru '{safe_arg}' nie bylo na liscie."
        SEARCH_ARTICLE_NUMBERS = [n for n in SEARCH_ARTICLE_NUMBERS if n != normalized_arg]
        DYNAMIC_STATE["search_article_numbers"] = SEARCH_ARTICLE_NUMBERS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Usunieto numer '{safe_arg}'. Aktualna lista: {escape_html(', '.join(SEARCH_ARTICLE_NUMBERS)) or '(brak)'}"

    if cmd in ("/sklepy", "/stores"):
        return format_stores_message()

    if cmd in ("/dodajsklep", "/addstore"):
        if not arg:
            return "Podaj ID sklepu, np. /dodajsklep 1224\n\n" + format_stores_message()
        store_id = arg
        safe_store_id = escape_html(store_id)
        if store_id not in KNOWN_STORES:
            return f"Nieznany ID sklepu '{safe_store_id}'.\n\n" + format_stores_message()
        if store_id in STORE_IDS:
            return f"{escape_html(store_display_name(store_id))} ({safe_store_id}) juz jest aktywny."
        STORE_IDS.append(store_id)
        DYNAMIC_STATE["store_ids"] = STORE_IDS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Dodano sklep {escape_html(store_display_name(store_id))} ({safe_store_id}) do monitoringu."

    if cmd in ("/usunsklep", "/removestore"):
        if not arg:
            return "Podaj ID sklepu do usuniecia, np. /usunsklep 294"
        store_id = arg
        safe_store_id = escape_html(store_id)
        if store_id not in STORE_IDS:
            return f"Sklep '{safe_store_id}' nie jest aktywny."
        STORE_IDS = [s for s in STORE_IDS if s != store_id]
        DYNAMIC_STATE["store_ids"] = STORE_IDS
        save_dynamic_state(DYNAMIC_STATE)
        reply = f"Usunieto sklep {escape_html(store_display_name(store_id))} ({safe_store_id}) z monitoringu."
        if not STORE_IDS:
            reply += (
                "\n\nUwaga: nie masz juz zadnych aktywnych sklepow - monitoring "
                "nie pobierze zadnych ofert, dopoki nie dodasz przynajmniej "
                "jednego (/dodajsklep &lt;ID&gt;)."
            )
        return reply

    if cmd in ("/status", "/lista"):
        return format_status_message()

    if cmd in ("/pomoc", "/help", "/start"):
        return format_help_message()

    return f"Nieznana komenda: {escape_html(cmd)}\n\n{format_help_message()}"


def handle_telegram_updates() -> None:
    """Sprawdza nowe wiadomosci od ostatniego razu i wykonuje komendy.
    Ignoruje wiadomosci od kogokolwiek innego niz TELEGRAM_CHAT_ID."""
    offset = load_telegram_offset()
    updates = telegram_get_updates(offset)

    max_update_id = offset - 1
    for update in updates:
        max_update_id = max(max_update_id, update.get("update_id", max_update_id))
        # Tylko nowe wiadomosci: edycja starej komendy (edited_message) nie
        # moze jej wykonac drugi raz. Offset i tak przesuwa sie dalej.
        message = update.get("message")
        if not message:
            continue

        chat_id = message.get("chat", {}).get("id")
        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            continue  # nieautoryzowany nadawca - ignoruj

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        parts = text.split(maxsplit=1)
        # W grupach Telegram dokleja nazwe bota: /status@MojBot -> /status.
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        try:
            reply = handle_command(cmd, arg)
        except Exception as exc:
            reply = f"Blad przy obsludze komendy: {exc}"

        try:
            telegram_send_message(reply, chat_id=chat_id, disable_preview=True)
        except Exception as exc:
            log(f"Nie udalo sie odpowiedziec na Telegramie: {exc}", to_stderr=True)

    if updates:
        save_telegram_offset(max_update_id + 1)


# ---------------- JITTER I WYKLADNICZY BACKOFF ----------------
# Stan backoffu jest aktualizowany w kazdym cyklu. Daemon planuje wedlug
# niego kolejne cykle; cron pomija odpalenia przed terminem (main()).


def apply_jitter_percent(value: float, percent: float) -> float:
    """Symetryczny jitter +/-percent%, wynik nigdy ujemny; percent <= 0
    wylacza. Tylko dla normalnego interwalu - backoff uzywa
    apply_backoff_jitter(), ktory nie skraca."""
    if percent <= 0:
        return value
    jitter_fraction = random.uniform(-percent, percent) / 100.0
    return max(0.0, value * (1 + jitter_fraction))


def compute_jittered_interval() -> float:
    """CHECK_INTERVAL_SECONDS z symetrycznym jitterem (gdy brak backoffu)."""
    return apply_jitter_percent(CHECK_INTERVAL_SECONDS, CHECK_INTERVAL_JITTER_PERCENT)


def effective_backoff_base_seconds() -> float:
    """Pierwszy krok backoffu, nigdy krotszy niz CHECK_INTERVAL_SECONDS -
    inaczej 403/429 zwiekszaloby czestotliwosc zapytan."""
    return max(CHECK_INTERVAL_SECONDS, BACKOFF_BASE_SECONDS)


def effective_backoff_cap_seconds() -> float:
    """Cap backoffu, nigdy nizszy niz pierwszy krok (przy duzym interwale)."""
    return max(effective_backoff_base_seconds(), BACKOFF_CAP_SECONDS)


def apply_backoff_jitter(value: float, percent: float = BACKOFF_JITTER_PERCENT) -> float:
    """Jitter tylko w gore: wynik w [value, value*(1+percent/100)], wiec nie
    skraca wyliczonego minimum (backoff/Retry-After)."""
    if percent <= 0:
        return value
    extra_fraction = random.uniform(0, percent) / 100.0
    return value * (1 + extra_fraction)


def compute_local_backoff_delay(failure_count: int) -> float:
    """min(baza * 2^(failure_count-1), cap), bez Retry-After i jittera;
    failure_count <= 0 -> 0.0."""
    if failure_count <= 0:
        return 0.0
    base = effective_backoff_base_seconds()
    cap = effective_backoff_cap_seconds()
    delay = base * (2 ** (failure_count - 1))
    return min(delay, cap)


def compute_next_allowed_check_delay(failure_count: int, retry_after_seconds=None) -> float:
    """Opoznienie po failure_count kolejnych blokadach: max(lokalny backoff,
    Retry-After), na koniec jitter w gore - wiec nigdy krocej niz ktorakolwiek
    z tych wartosci."""
    local_delay = compute_local_backoff_delay(failure_count)
    if retry_after_seconds is not None and retry_after_seconds > local_delay:
        base_delay = retry_after_seconds
    else:
        base_delay = local_delay
    if base_delay <= 0:
        return 0.0
    return apply_backoff_jitter(base_delay)


def all_stores_blocked_by_403_429(store_errors: dict) -> bool:
    """Utrata dostepu: STORE_IDS niepuste i kazdy sklep zawiodl wylacznie
    przez 403/429. Inny blad (5xx, timeout, parsowanie) lub chocby jeden
    sukces -> False."""
    if not STORE_IDS or not store_errors:
        return False
    if len(store_errors) < len(STORE_IDS):
        return False
    return all(isinstance(exc, BlockedByServerError) for exc in store_errors.values())


def full_cycle_fetched_successfully(store_errors: dict) -> bool:
    """Odzyskanie dostepu wymaga pelnego sukcesu: zadnego bledu sklepu."""
    return not store_errors


def update_blocking_backoff_state(store_errors: dict) -> BackoffState:
    """Aktualizuje i zapisuje globalny backoff na podstawie bledow cyklu.
    Blokada 403/429 zwieksza failure_count; cykl bez blokady (nawet z innymi
    bledami) resetuje stan. Zwraca zapisany BackoffState."""
    state = get_blocking_backoff_state()

    blocked_errors = [
        exc for exc in store_errors.values() if isinstance(exc, BlockedByServerError)
    ]

    if blocked_errors:
        blocked_codes = sorted({exc.status_code for exc in blocked_errors})
        # Najdluzszy Retry-After z cyklu (sklepy moga dostac rozne).
        retry_after_candidates = [
            exc.retry_after_seconds for exc in blocked_errors
            if exc.retry_after_seconds is not None
        ]
        retry_after_seconds = max(retry_after_candidates) if retry_after_candidates else None

        failure_count = state.failure_count + 1
        local_delay = compute_local_backoff_delay(failure_count)
        final_delay = compute_next_allowed_check_delay(failure_count, retry_after_seconds)

        state = BackoffState(
            failure_count=failure_count,
            last_status_code=blocked_codes[-1],
            next_allowed_check_at=time.time() + final_delay,
            retry_after_seconds=retry_after_seconds,
        )

        retry_after_txt = (
            f"{retry_after_seconds:.0f}s" if retry_after_seconds is not None else "brak"
        )
        log(
            f"Blokada/rate limit HTTP {', '.join(str(c) for c in blocked_codes)}: "
            f"porazka #{failure_count}. Lokalny backoff: {local_delay:.0f}s; "
            f"Retry-After: {retry_after_txt}; nastepna proba nie wcześniej niż "
            f"za {final_delay:.0f}s.",
            to_stderr=True,
        )
    elif state.failure_count:
        log(
            "Pobranie bez blokady/rate limitu - resetuje globalny stan backoffu "
            f"(byl na porazce #{state.failure_count})."
        )
        state = BackoffState()

    set_blocking_backoff_state(state)
    return state


def _enabled_channels() -> list:
    """Wlaczone kanaly ('email', 'telegram') w stalej kolejnosci."""
    channels = []
    if EMAIL_ENABLED:
        channels.append("email")
    if TELEGRAM_ENABLED:
        channels.append("telegram")
    return channels


def run_ikea_check_cycle() -> int:
    """Jeden cykl: pobranie ofert, backoff i alert o dostepie, dostawa per
    kanal, a na koncu jedna aktualizacja bloku 'health' (alert o dluzszej
    awarii monitora). Nieoczekiwany wyjatek jest logowany i liczony jako
    nieudany cykl, zeby nie przerwac petli daemona.

    Kod wyjscia: 0 - sukces/brak nowych ofert; 1 - zaden sklep nie pobrany,
    blad odczytu stanu albo nieoczekiwany blad; 2 - ktorys kanal zawiodl
    (postep zapisany); 3 - blad zapisu pliku stanu."""
    try:
        exit_code, outcome, error_description = _run_check_cycle()
    except Exception as exc:
        log(
            f"Nieoczekiwany blad cyklu sprawdzania ofert: {exception_text(exc)}\n"
            f"{traceback.format_exc()}",
            to_stderr=True,
        )
        exit_code, outcome = 1, CYCLE_UNHEALTHY
        error_description = f"nieoczekiwany blad: {type(exc).__name__}: {exc}"

    try:
        update_health_state(outcome, error_description)
    except Exception as exc:
        log(f"Blad aktualizacji stanu monitora (blok 'health'): {exception_text(exc)}", to_stderr=True)
    return exit_code


def _outcome_with_state_error(fetch_outcome: str, fetch_error, state_error: str) -> tuple:
    """(wynik, opis) po bledzie pliku stanu: cykl jest nieudany, chyba ze i
    tak byl pominiety (brak sklepow)."""
    if fetch_outcome == CYCLE_SKIPPED:
        return CYCLE_SKIPPED, None
    return CYCLE_UNHEALTHY, "; ".join(part for part in (fetch_error, state_error) if part)


def _run_check_cycle() -> tuple:
    """Wlasciwy cykl run_ikea_check_cycle(). Zwraca (kod wyjscia, wynik dla
    bloku 'health', opis bledu albo None). 'pending' w STATE_FILE pamieta,
    ktore kanaly juz dostarczyly oferte, wiec ponowienie idzie tylko do
    brakujacych; do 'seen' oferta trafia po dostarczeniu wszystkimi
    aktywnymi kanalami. Bledy kanalow (kod 2) nie psuja wyniku - alert i
    tak nie mialby jak dojsc."""
    if not SEARCH_TERMS and not SEARCH_ARTICLE_NUMBERS:
        log(
            "Brak aktywnych slow kluczowych i numerow artykulow - "
            "monitoring ofert jest wstrzymany."
        )
        return 0, CYCLE_SKIPPED, None

    # Jeden profil klienta na caly cykl (wszystkie strony i sklepy).
    global CURRENT_CLIENT_PROFILE
    CURRENT_CLIENT_PROFILE = choose_client_profile()

    try:
        content, store_errors = fetch_all_offers()
    except Exception as exc:
        log(f"Blad zapytania do API: {exc}", to_stderr=True)
        return 1, CYCLE_UNHEALTHY, f"blad zapytania do API: {exception_text(exc)}"

    update_blocking_backoff_state(store_errors)

    # Alert o dostepie dziala w cron i daemon (stan jest trwaly).
    if not STORE_IDS:
        # Nic nie sprawdzono - taki przebieg nie moze ani potwierdzic, ani
        # wyczyscic stanu alertu (ani stanu 'health').
        fetch_outcome, fetch_error = CYCLE_SKIPPED, None
    elif all_stores_blocked_by_403_429(store_errors):
        blocked_status_code = sorted({
            exc.status_code for exc in store_errors.values()
            if isinstance(exc, BlockedByServerError)
        })[-1]
        handle_access_notification_state(True, blocked_status_code)
        # Pelna blokada ma wlasny alert o dostepie - bez podwojnego alertu.
        fetch_outcome, fetch_error = CYCLE_SKIPPED, None
    elif full_cycle_fetched_successfully(store_errors):
        handle_access_notification_state(False, None)
        fetch_outcome, fetch_error = CYCLE_HEALTHY, None
    else:
        reset_pending_outage_after_partial_cycle()
        fetch_outcome, fetch_error = CYCLE_UNHEALTHY, describe_store_errors(store_errors)

    if store_errors and len(store_errors) >= len(STORE_IDS):
        log(
            "Nie udalo sie pobrac ofert z zadnego sklepu "
            f"({len(store_errors)}/{len(STORE_IDS)}). Przerywam cykl.",
            to_stderr=True,
        )
        return 1, fetch_outcome, fetch_error

    if store_errors:
        log(
            f"Nie udalo sie pobrac {len(store_errors)}/{len(STORE_IDS)} sklepow "
            f"({', '.join(str(sid) for sid in store_errors)}) - kontynuuje z pozostalymi.",
            to_stderr=True,
        )

    matching_offers = flatten_matching_offers(content)
    current_uuids = {o["offer_uuid"] for o in matching_offers if o.get("offer_uuid")}

    try:
        state = load_seen_state()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # ValueError obejmuje JSONDecodeError; TypeError/AttributeError -
        # poprawny JSON o zlej strukturze (np. lista zamiast obiektu).
        log(f"Blad odczytu pliku stanu: {exc}", to_stderr=True)
        return (1, *_outcome_with_state_error(
            fetch_outcome, fetch_error, f"plik stanu (odczyt): {exception_text(exc)}"
        ))

    seen_uuids = state["seen"]
    pending = state["pending"]   # {uuid: [channels_delivered]}

    first_run = not os.path.exists(STATE_FILE)
    if first_run and not ALERT_EXISTING_ON_FIRST_RUN:
        try:
            save_seen_state(current_uuids, {})
        except OSError as exc:
            log(f"Blad zapisu pliku stanu: {exc}", to_stderr=True)
            return (3, *_outcome_with_state_error(
                fetch_outcome, fetch_error, f"plik stanu (zapis): {exception_text(exc)}"
            ))
        log(
            "Pierwsze uruchomienie: zapisano stan bez wysylania alertu. "
            f"Znaleziono dopasowan: {len(matching_offers)}."
        )
        return 0, fetch_outcome, fetch_error

    # Oferty znikniete z API wypadaja z pending - tylko przy komplecie
    # danych ze wszystkich sklepow; przy czesciowym cyklu oferta
    # niedostepnego sklepu moze nadal istniec.
    if not store_errors:
        pending = {uuid: ch for uuid, ch in pending.items() if uuid in current_uuids}

    # Licza sie tylko aktualnie wlaczone kanaly, wiec wylaczenie kanalu nie
    # zostawia ofert w pending na zawsze.
    active_channels = _enabled_channels()
    active_channels_set = set(active_channels)


    # Oferty, ktorych co najmniej jeden aktywny kanal jeszcze nie dostarczyl.
    new_offers = []
    for o in matching_offers:
        uuid = o.get("offer_uuid")
        if not uuid:
            continue
        if uuid in seen_uuids:
            continue
        delivered_so_far = set(pending.get(uuid, []))
        remaining = active_channels_set - delivered_so_far
        if remaining:
            new_offers.append(o)

    any_channel_failed = False
    if new_offers:
        # Kazdy kanal dostaje tylko oferty, ktorych jeszcze nie dostarczyl.
        for channel in active_channels:
            channel_offers = [
                o for o in new_offers
                if channel not in set(pending.get(o["offer_uuid"], []))
            ]
            if not channel_offers:
                continue

            channel_error = None
            try:
                if channel == "email":
                    send_email(channel_offers)
                elif channel == "telegram":
                    send_telegram(channel_offers)
            except Exception as exc:
                channel_error = str(exc)
                log(f"Blad wysylki powiadomienia ({channel}: {channel_error})", to_stderr=True)
                any_channel_failed = True

            if channel_error is None:
                for o in channel_offers:
                    uuid = o["offer_uuid"]
                    already = pending.get(uuid, [])
                    if channel not in already:
                        pending[uuid] = sorted(set(already) | {channel})

    # Promocja do seen, gdy dostarczyly wszystkie aktywne kanaly - takze bez
    # nowych ofert (np. po wylaczeniu kanalu, ktory zawodzil).
    if active_channels_set:
        newly_seen = set()
        for uuid, delivered_channels in list(pending.items()):
            # Przy czesciowym cyklu nie promujemy ofert niewidocznych w tym
            # cyklu - ich sklep mogl wlasnie nie odpowiedziec.
            if store_errors and uuid not in current_uuids:
                continue

            if active_channels_set <= set(delivered_channels):
                newly_seen.add(uuid)
                del pending[uuid]

        seen_uuids |= newly_seen

    try:
        save_seen_state(seen_uuids, pending)
    except OSError as exc:
        log(f"Blad zapisu pliku stanu: {exc}", to_stderr=True)
        return (3, *_outcome_with_state_error(
            fetch_outcome, fetch_error, f"plik stanu (zapis): {exception_text(exc)}"
        ))

    if any_channel_failed:
        # Postep sprawnych kanalow jest juz zapisany.
        return 2, fetch_outcome, fetch_error

    if new_offers:
        log(f"Wyslano powiadomienie: {len(new_offers)} nowa(e) oferta(y).")
    else:
        log(f"Brak nowych ofert (aktualnie widocznych dopasowan: {len(matching_offers)}).")

    return 0, fetch_outcome, fetch_error


def request_shutdown(signum=None, frame=None) -> None:
    """Handler SIGTERM/SIGINT: ustawia SHUTDOWN_EVENT, petla daemona konczy
    sie po biezacym przebiegu."""
    SHUTDOWN_EVENT.set()


def install_shutdown_signal_handlers() -> None:
    """Instaluje handlery SIGTERM/SIGINT - tylko z run_daemon(), nigdy przy imporcie."""
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def seconds_until_next_allowed_check(state: BackoffState, now_wall_clock=None) -> "float | None":
    """Sekundy od teraz do zapisanego next_allowed_check_at (czas scienny).
    None - brak aktywnego backoffu; 0.0 - termin juz minal (np. dlugi
    postoj uslugi), sprawdzamy od razu. Jedyne miejsce liczace roznice
    time.time(); petla daemona odlicza dalej zegarem monotonicznym."""
    if state.next_allowed_check_at is None:
        return None
    now_wall_clock = time.time() if now_wall_clock is None else now_wall_clock
    remaining = state.next_allowed_check_at - now_wall_clock
    return max(0.0, remaining)


def blocking_backoff_active(now_wall_clock=None) -> "float | None":
    """Sekundy do konca trwalego backoffu po 403/429 albo None, gdy
    zapytanie do IKEA jest juz dozwolone (brak backoffu lub termin minal)."""
    remaining = seconds_until_next_allowed_check(get_blocking_backoff_state(), now_wall_clock)
    if remaining is None or remaining <= 0:
        return None
    return remaining


def log_cron_backoff_skip(remaining_seconds: float) -> None:
    """Komunikat o pominieciu sprawdzenia w trybie cron z powodu backoffu."""
    state = get_blocking_backoff_state()
    until_txt = format_local_timestamp(state.next_allowed_check_at)
    log(
        f"Aktywny backoff po blokadzie/rate limicie (porazka #{state.failure_count} "
        f"z rzedu, ostatni HTTP {state.last_status_code}) - pomijam sprawdzanie "
        f"ofert do {until_txt} (jeszcze {remaining_seconds:.0f}s), bez zapytania do IKEA."
    )


def run_daemon() -> int:
    """Petla trybu daemon: Telegram czesto, oferty IKEA rzadziej. Interwaly
    sa odliczane time.monotonic(), zeby skoki zegara (NTP) ich nie
    zmienialy; trwaly termin backoffu (czas scienny) jest przeliczany na
    sekundy przy starcie i po kazdym cyklu. Po SIGTERM/SIGINT konczy sie
    bez nowego cyklu i zwraca 0."""
    install_shutdown_signal_handlers()
    log(
        f"Start w trybie daemon (IKEA co ~{CHECK_INTERVAL_SECONDS}s "
        f"+/-{CHECK_INTERVAL_JITTER_PERCENT}% jitter, Telegram co "
        f"{TELEGRAM_POLL_INTERVAL_SECONDS}s)."
    )

    # Restart nie resetuje aktywnego backoffu - respektujemy zapisany termin.
    persisted_state = get_blocking_backoff_state()
    persisted_delay = seconds_until_next_allowed_check(persisted_state)
    if persisted_delay is not None:
        next_check_interval = persisted_delay
        log(
            f"Wznawiam po restarcie z aktywnym backoffem (porazka "
            f"#{persisted_state.failure_count} z rzedu, HTTP "
            f"{persisted_state.last_status_code}) - nastepna proba za "
            f"{next_check_interval:.0f}s."
        )
    else:
        next_check_interval = compute_jittered_interval()

    # Pierwsze sprawdzenie ofert dopiero po next_check_interval, nie od razu.
    last_ikea_check_monotonic = time.monotonic()

    while not SHUTDOWN_EVENT.is_set():
        if TELEGRAM_ENABLED:
            try:
                handle_telegram_updates()
            except Exception as exc:
                log(f"Blad obslugi komend Telegrama: {exc}", to_stderr=True)

        if SHUTDOWN_EVENT.is_set():
            break

        if time.monotonic() - last_ikea_check_monotonic >= next_check_interval:
            run_ikea_check_cycle()
            # Znacznik po cyklu, zeby czas pobierania nie skracal interwalu.
            last_ikea_check_monotonic = time.monotonic()

            backoff_state = get_blocking_backoff_state()
            if backoff_state.failure_count > 0:
                # Termin zapisal juz cykl; przeliczamy go na zegar tej petli.
                next_check_interval = seconds_until_next_allowed_check(backoff_state) or 0.0
                log(
                    f"Backoff aktywny (porazka #{backoff_state.failure_count} z rzedu, "
                    f"HTTP {backoff_state.last_status_code}) - nastepna proba za "
                    f"{next_check_interval:.0f}s."
                )
            else:
                next_check_interval = compute_jittered_interval()
                log(f"Nastepne sprawdzenie ofert za {next_check_interval:.0f}s.")

        # wait() budzi sie od razu po ustawieniu SHUTDOWN_EVENT (sleep() nie).
        SHUTDOWN_EVENT.wait(TELEGRAM_POLL_INTERVAL_SECONDS)

    log("Zatrzymywanie (SIGTERM/SIGINT) - koncze petle daemona bez uruchamiania nowego cyklu.")
    return 0


def warn_if_systemd_without_daemon_mode() -> None:
    """Ostrzega, gdy proces dziala pod systemd (INVOCATION_ID), a RUN_MODE
    nie jest 'daemon': jedno przejscie i exit 0 moze sprawic, ze systemd
    nie zrestartuje uslugi."""
    if os.environ.get("INVOCATION_ID") and RUN_MODE != "daemon":
        log(
            "UWAGA: proces zostal odpalony przez systemd, ale RUN_MODE w "
            f"{CONFIG_FILE} nie jest ustawiony na 'daemon' (aktualnie: "
            f"'{RUN_MODE}'). Skrypt wykona jedno przejscie typu 'cron' i "
            "zakonczy sie ze statusem 0 - systemd (Type=simple) moze uznac "
            "to za normalne zakonczenie i NIE zrestartuje uslugi. Ustaw "
            f"RUN_MODE=daemon w {CONFIG_FILE}, jesli chcesz uzywac tego "
            "skryptu jako usluge systemd.",
            to_stderr=True,
        )


def main() -> int:
    initialize_runtime()

    warn_if_systemd_without_daemon_mode()

    if TELEGRAM_ENABLED and RUN_MODE != "daemon":
        # W cron komendy sprawdzamy raz, przed ofertami (w daemon - petla).
        try:
            handle_telegram_updates()
        except Exception as exc:
            log(f"Blad obslugi komend Telegrama: {exc}", to_stderr=True)

    if RUN_MODE == "daemon":
        return run_daemon()

    # Cron odpala sie wg crontaba, wiec termin backoffu trzeba sprawdzic
    # tutaj - inaczej kazde odpalenie w trakcie blokady pytaloby IKEA.
    remaining = blocking_backoff_active()
    if remaining is not None:
        log_cron_backoff_skip(remaining)
        return 0

    return run_ikea_check_cycle()


if __name__ == "__main__":
    sys.exit(main())
