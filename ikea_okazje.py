#!/usr/bin/env python3
"""
ikea_okazje.py

Male narzedzie, ktore sprawdza dzial "Okazje na okraglo" (second-hand /
buy-from-ikea) w wybranych sklepach IKEA i wysyla powiadomienie (e-mail
i/albo Telegram), kiedy pojawi sie produkt, ktorego szukasz.

Jesli Telegram jest skonfigurowany, mozesz zarzadzac lista szukanych
slow, numerow artykulu i monitorowanych sklepow komendami w czacie z
botem:
    /dodaj <slowo>        - dodaj slowo kluczowe
    /usun <slowo>         - usun slowo kluczowe
    /numer <nr>           - dodaj numer artykulu
    /usunnumer <nr>        - usun numer artykulu
    /sklepy               - pokaz aktywne i dostepne sklepy
    /dodajsklep <ID>       - dodaj sklep do monitoringu
    /usunsklep <ID>        - usun sklep z monitoringu
    /status               - pokaz aktualnie monitorowane slowa/numery/sklepy
    /pomoc                - lista komend

Dwa tryby pracy (RUN_MODE w .env):
    "cron"   (domyslny) - jedno przejscie i wyjscie, do uzycia z crona.
    "daemon" - dziala w petli w tle (np. jako usluga systemd), sprawdza
               komendy Telegrama czesto (TELEGRAM_POLL_INTERVAL_SECONDS),
               a oferty IKEA rzadziej (CHECK_INTERVAL_SECONDS).

WAZNE: wszystkie ustawienia, ktore chcesz zmieniac (sklepy, szukane
produkty, filtry, sposob wysylki maila, Telegram, tryb pracy) sa w pliku
~/.config/ikea-okazje.env, NIE w tym skrypcie. Dzieki temu aktualizacja
skryptu (np. z GitHuba) nigdy nie nadpisze Twoich osobistych ustawien -
edytuj plik .env, nie ten kod. Zobacz .env.example.

Wymagania:
    pip install curl_cffi
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
import smtplib
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from curl_cffi import requests

# ---------------- KONFIGURACJA TECHNICZNA (nie zmieniaj tego przez .env) ----------------
API_URL = "https://web-api.ikea.com/circular/circular-asis/offers/grouped/search"

PAGE_SIZE = "64"   # API ogranicza max rozmiar strony do 64
MAX_PAGES = 20      # zabezpieczenie przed niekonczaca sie paginacja

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
STORE_JITTER_RANGE = (1.0, 3.0)

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "pl-PL,pl;q=0.8",
    "origin": "https://www.ikea.com",
    "priority": "u=1, i",
    "referer": "https://www.ikea.com/",
    "sec-ch-ua": '"Chromium";v="124", "Not?A_Brand";v="24", "Brave";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "sec-gpc": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
IMPERSONATE = "chrome124"

STATE_FILE = os.path.expanduser("~/.ikea_okazje_seen_offers.json")
RAW_DUMP_FILE = os.path.expanduser("~/.ikea_okazje_last_raw.json")
CONFIG_FILE = os.path.expanduser("~/.config/ikea-okazje.env")
DYNAMIC_STATE_FILE = os.path.expanduser("~/.ikea_okazje_dynamic.json")
TELEGRAM_OFFSET_FILE = os.path.expanduser("~/.ikea_okazje_telegram_offset.json")

SMTP_TIMEOUT = 30
REQUEST_TIMEOUT = 15

# Pelna, potwierdzona mapa sklepow IKEA w Polsce: storeId -> nazwa + slug
# uzywany w adresie strony "Okazje na Okraglo online". Sluzy jako wbudowana
# baza wiedzy, z ktorej korzystaja komendy /sklepy, /dodajsklep, /usunsklep
# oraz generator linkow rezerwacji (build_offer_reservation_link), gdy dany
# storeId nie ma wlasnego wpisu w STORE_URL_SLUGS z .env.
KNOWN_STORES = {
    "1224": {"name": "Bielsko-Biala", "slug": "bielsko+biala"},
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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, file=sys.stderr if to_stderr else sys.stdout)


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
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("'").strip('"')
            values[key.strip()] = value
    return values


def parse_list(raw, default):
    if raw is None or raw.strip() == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_optional_number(raw):
    if raw is None or raw.strip() == "":
        return None
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def parse_bool(raw, default):
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "tak")


def parse_store_url_slugs(raw: str, default: dict) -> dict:
    """Parsuje STORE_URL_SLUGS z .env.

    Format: STORE_ID:slug-sklepu,STORE_ID:slug-drugiego-sklepu
    Zwraca slownik {store_id_str: slug_str}.
    Ignoruje niepoprawne/puste wpisy.
    """
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


ENV = load_env_file(CONFIG_FILE)

# ---------------- USTAWIENIA UZYTKOWNIKA (z ~/.config/ikea-okazje.env) ----------------
# BASE_STORE_IDS/BASE_SEARCH_TERMS/BASE_SEARCH_ARTICLE_NUMBERS sluza wylacznie
# do zasiania dynamicznego stanu (patrz DYNAMICZNA LISTA nizej) - po pierwszym
# uruchomieniu zrodlem prawdy jest ~/.ikea_okazje_dynamic.json, a nie .env.
BASE_STORE_IDS = parse_list(ENV.get("STORE_IDS"), ["294"])
BASE_SEARCH_TERMS = parse_list(ENV.get("SEARCH_TERMS"), ["Stall"])
BASE_SEARCH_ARTICLE_NUMBERS = parse_list(ENV.get("SEARCH_ARTICLE_NUMBERS"), [])

MIN_DISCOUNT_PERCENT = parse_optional_number(ENV.get("MIN_DISCOUNT_PERCENT"))
MAX_PRICE = parse_optional_number(ENV.get("MAX_PRICE"))
KEYWORDS_EXCLUDE = parse_list(ENV.get("KEYWORDS_EXCLUDE"), [])

ALERT_EXISTING_ON_FIRST_RUN = parse_bool(ENV.get("ALERT_EXISTING_ON_FIRST_RUN"), False)

SMTP_MODE = ENV.get("SMTP_MODE", "gmail")
VERIFY_TLS = parse_bool(ENV.get("VERIFY_TLS"), True)

# "cron" (domyslny, jedno przejscie) albo "daemon" (petla w tle, np. systemd)
RUN_MODE = ENV.get("RUN_MODE", "cron").strip().lower()
CHECK_INTERVAL_SECONDS = parse_optional_number(ENV.get("CHECK_INTERVAL_SECONDS")) or 900
TELEGRAM_POLL_INTERVAL_SECONDS = parse_optional_number(ENV.get("TELEGRAM_POLL_INTERVAL_SECONDS")) or 15

# Mapowanie storeId -> slug sklepu uzywany w adresach "Okazje na Okraglo".
# Ma pierwszenstwo nad wbudowana mapa KNOWN_STORES (pozwala obsluzyc nowe
# sklepy albo zmiane routingu IKEA bez aktualizacji kodu). Jesli puste/brak
# w .env, uzywana jest wylacznie KNOWN_STORES.
STORE_URL_SLUGS = parse_store_url_slugs(ENV.get("STORE_URL_SLUGS", ""), {})
# ----------------------------------------------------------------------------------------

if SMTP_MODE == "gmail":
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587
    SMTP_USER = ENV.get("SMTP_USER") or os.environ.get("SMTP_USER")
    SMTP_PASS = ENV.get("SMTP_PASS") or os.environ.get("SMTP_PASS")
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError(
            f"SMTP_MODE='gmail' wymaga SMTP_USER i SMTP_PASS w {CONFIG_FILE}"
        )
    EMAIL_FROM = SMTP_USER
    USE_AUTH = True
elif SMTP_MODE == "local587":
    SMTP_HOST = ENV.get("SMTP_HOST", "localhost")
    SMTP_PORT = 587
    SMTP_USER = ENV.get("SMTP_USER") or os.environ.get("SMTP_USER")
    SMTP_PASS = ENV.get("SMTP_PASS") or os.environ.get("SMTP_PASS")
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError(
            f"SMTP_MODE='local587' wymaga SMTP_USER i SMTP_PASS w {CONFIG_FILE}"
        )
    EMAIL_FROM = SMTP_USER
    USE_AUTH = True
else:  # "exim"
    SMTP_HOST = ENV.get("SMTP_HOST", "localhost")
    SMTP_PORT = 25
    SMTP_USER = None
    SMTP_PASS = None
    EMAIL_FROM = ENV.get("EMAIL_FROM", "ikea-watch@localhost")
    USE_AUTH = False

EMAIL_TO = ENV.get("EMAIL_TO") or os.environ.get("EMAIL_TO") or EMAIL_FROM

TELEGRAM_BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = ENV.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u0142", "l").replace("\u0141", "L")
    decomposed = unicodedata.normalize("NFKD", s)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold()


# ---------------- DYNAMICZNA LISTA (modyfikowana komendami z Telegrama) ----------------

def load_dynamic_state() -> dict:
    """Pierwsze uzycie: zasiewa stan z SEARCH_TERMS/SEARCH_ARTICLE_NUMBERS/
    STORE_IDS z .env. Kolejne uzycia: czyta juz tylko z tego pliku - .env po
    pierwszym razie nie jest juz zrodlem prawdy dla tych list (zmieniaj je
    odtad komendami w Telegramie albo edytujac ten plik). Jesli plik juz
    istnieje, ale nie ma jeszcze klucza "store_ids" (starsza wersja stanu),
    dopisujemy go z .env jako fallback i zapisujemy z powrotem na dysk."""
    if os.path.exists(DYNAMIC_STATE_FILE):
        with open(DYNAMIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        had_store_ids = "store_ids" in data
        state = {
            "search_terms": data.get("search_terms", list(BASE_SEARCH_TERMS)),
            "search_article_numbers": data.get("search_article_numbers", list(BASE_SEARCH_ARTICLE_NUMBERS)),
            "store_ids": data.get("store_ids", list(BASE_STORE_IDS)),
        }
        if not had_store_ids:
            save_dynamic_state(state)
        return state
    state = {
        "search_terms": list(BASE_SEARCH_TERMS),
        "search_article_numbers": list(BASE_SEARCH_ARTICLE_NUMBERS),
        "store_ids": list(BASE_STORE_IDS),
    }
    save_dynamic_state(state)
    return state


def save_dynamic_state(state: dict) -> None:
    tmp_path = DYNAMIC_STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, DYNAMIC_STATE_FILE)


DYNAMIC_STATE = load_dynamic_state()
SEARCH_TERMS = DYNAMIC_STATE["search_terms"]
SEARCH_ARTICLE_NUMBERS = DYNAMIC_STATE["search_article_numbers"]
STORE_IDS = DYNAMIC_STATE["store_ids"]
NORMALIZED_TERMS = [normalize_text(t) for t in SEARCH_TERMS]
NORMALIZED_EXCLUDE = [normalize_text(t) for t in KEYWORDS_EXCLUDE]


def refresh_normalized_terms() -> None:
    """Wywolaj po kazdej zmianie SEARCH_TERMS przez komende Telegrama."""
    global NORMALIZED_TERMS
    NORMALIZED_TERMS = [normalize_text(t) for t in SEARCH_TERMS]


def store_display_name(store_id) -> str:
    """Zwraca czytelna nazwe sklepu z KNOWN_STORES, albo "Sklep <ID>",
    jesli ID nie jest w wbudowanej mapie (np. rowny wpis dodany recznie
    przez STORE_IDS w .env)."""
    info = KNOWN_STORES.get(str(store_id))
    return info["name"] if info else f"Sklep {store_id}"


# ---------------- IKEA API ----------------

def dump_raw(data) -> None:
    try:
        with open(RAW_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def fetch_page_with_retry(store_id: str, page: int) -> dict:
    params = {
        "languageCode": "pl",
        "size": PAGE_SIZE,
        "storeIds": store_id,
        "page": str(page),
    }

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(
                API_URL,
                headers=HEADERS,
                params=params,
                timeout=REQUEST_TIMEOUT,
                impersonate=IMPERSONATE,
            )
        except Exception as exc:
            last_error = exc
        else:
            if resp.status_code == 200:
                return resp.json()
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

        all_content.extend(content)

        if page == 0:
            meta = {k: v for k, v in data.items() if k != "content"}
            dump_raw({"store_id": store_id, "page0_metadata": meta, "content": content})

        is_last = data.get("last")
        total_pages = data.get("totalPages")
        if is_last is True:
            break
        if total_pages is not None and page + 1 >= total_pages:
            break
        if not content or len(content) < int(PAGE_SIZE):
            break

        page += 1

    return all_content


def fetch_all_offers() -> list:
    all_content = []
    for i, store_id in enumerate(STORE_IDS):
        all_content.extend(fetch_store_offers(store_id))
        if i < len(STORE_IDS) - 1:
            time.sleep(random.uniform(*STORE_JITTER_RANGE))
    return all_content


def product_matches(product: dict) -> bool:
    haystack = normalize_text(f"{product.get('title', '')} {product.get('description', '')}")
    text_match = any(term in haystack for term in NORMALIZED_TERMS)

    article_numbers = product.get("articleNumbers") or []
    article_match = any(an in article_numbers for an in SEARCH_ARTICLE_NUMBERS)

    return text_match or article_match


def product_excluded(product: dict) -> bool:
    if not NORMALIZED_EXCLUDE:
        return False
    haystack = normalize_text(f"{product.get('title', '')} {product.get('description', '')}")
    return any(term in haystack for term in NORMALIZED_EXCLUDE)


def resolve_store_slug(store_id) -> str | None:
    """Zwraca slug sklepu dla linku rezerwacji. STORE_URL_SLUGS z .env ma
    pierwszenstwo nad wbudowana mapa KNOWN_STORES."""
    slug = STORE_URL_SLUGS.get(str(store_id))
    if slug:
        return slug
    info = KNOWN_STORES.get(str(store_id))
    return info["slug"] if info else None


def build_offer_reservation_link(store_id, offer_number) -> str | None:
    """Buduje bezposredni link do konkretnej oferty w dziale 'Okazje na Okraglo'.

    Format: https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/<slug>/<offerNumber>

    Znak "+" (separator spacji w slugach typu "bielsko+biala") jest celowo
    pozostawiony niezakodowany (safe="+") - polskie znaki (np. "l" w
    "wroclaw") sa nadal URL-encoded normalnie.

    Zwraca None, jesli brakuje offer_number lub mapowania storeId -> slug -
    zamiast generowac mylacy link do zwyklego katalogu produktow IKEA.
    """
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


def load_seen_uuids() -> set:
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f).get("seen", []))


def save_seen_uuids(uuids: set) -> None:
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"seen": sorted(uuids)}, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)


def format_offer_block(o: dict) -> str:
    discount_txt = f"{o['discount_percent']}%" if o["discount_percent"] is not None else "n/d"
    lines = [
        f"Produkt: {o['title']} - {o['description']}",
        f"Cena: {o['price']} {o['currency']} (cena wyjsciowa: {o['original_price']} {o['currency']}, rabat: {discount_txt})",
        f"Stan: {o['condition']} ({o['condition_desc']})",
        f"Powod przeceny: {o['reason_discount']}",
        f"Info: {o['additional_info']}",
        f"Numer artykulu: {', '.join(o['article_numbers'] or [])}",
        f"Numer oferty: {o['offer_number']}",
        f"Sklep (storeId): {o['store_id']}",
        f"Zdjecie: {o['hero_image']}",
    ]
    if o.get("reservation_link"):
        lines.append(f"Link do rezerwacji: {o['reservation_link']}")
    else:
        lines.append(
            "Rezerwacja: otwórz stronę \"Okazje na Okrągło online\", "
            "wybierz właściwy sklep i znajdź ofertę po numerze oferty."
        )
    return "\n".join(lines)


def send_email(new_offers) -> None:
    titles = ", ".join(sorted({o["title"] for o in new_offers}))
    subject = f"IKEA Okazje: nowa oferta - {titles}"

    blocks = [format_offer_block(o) for o in new_offers]
    body = (
        "Znaleziono nowe oferty dla: " + ", ".join(SEARCH_TERMS) + "\n\n"
        + "\n\n".join(blocks)
        + "\n\nSprawdz strone:\nhttps://www.ikea.com/pl/pl/second-hand/buy-from-ikea/"
    )

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


def format_offer_telegram(o: dict) -> str:
    discount_txt = f"{o['discount_percent']}%" if o["discount_percent"] is not None else "n/d"
    title = escape_html(o["title"] or "")
    description = escape_html(o["description"] or "")
    offer_number_txt = escape_html(str(o["offer_number"])) if o.get("offer_number") else "brak"
    store_id_txt = escape_html(str(o["store_id"])) if o.get("store_id") else "brak"

    lines = [
        f"<b>{title}</b> - {description}",
        f"Cena: <b>{o['price']} {o['currency']}</b> "
        f"(z {o['original_price']} {o['currency']}, rabat {discount_txt})",
        f"Stan: {o['condition']}",
        f"Numer oferty: {offer_number_txt} | Sklep: {store_id_txt}",
    ]
    if o.get("reservation_link"):
        lines.append(f'<a href="{o["reservation_link"]}">Przejdź do rezerwacji oferty</a>')
    return "\n".join(lines)


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def telegram_send_message(text: str, chat_id=None) -> None:
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if len(text) > 4000:
        text = text[:3990] + "\n\n(...)"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram API zwrocilo HTTP {resp.status}")


def send_telegram(new_offers) -> None:
    text = "\n\n".join(format_offer_telegram(o) for o in new_offers)
    text = f"<b>IKEA Okazje - nowa oferta</b>\n\n{text}"
    telegram_send_message(text)


def notify(new_offers) -> list:
    errors = []

    try:
        send_email(new_offers)
    except Exception as exc:
        errors.append(f"e-mail: {exc}")

    if TELEGRAM_ENABLED:
        try:
            send_telegram(new_offers)
        except Exception as exc:
            errors.append(f"telegram: {exc}")

    return errors


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
    params = urllib.parse.urlencode({"offset": offset, "timeout": 0})
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?{params}"
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Blad polaczenia z Telegram API: {exc}")

    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates zwrocilo blad: {data}")
    return data.get("result", [])


def format_status_message() -> str:
    lines = ["<b>Aktualny monitoring:</b>", "", "Aktywne sklepy:"]
    if STORE_IDS:
        for sid in STORE_IDS:
            lines.append(f"- {store_display_name(sid)} ({sid})")
    else:
        lines.append("(brak - monitoring nie pobierze zadnych ofert)")
    lines.append("")
    lines.append(f"Slowa kluczowe: {', '.join(SEARCH_TERMS) or '(brak)'}")
    lines.append(f"Numery artykulu: {', '.join(SEARCH_ARTICLE_NUMBERS) or '(brak)'}")
    if KEYWORDS_EXCLUDE:
        lines.append(f"Czarna lista: {', '.join(KEYWORDS_EXCLUDE)}")
    if MIN_DISCOUNT_PERCENT is not None:
        lines.append(f"Min. rabat: {MIN_DISCOUNT_PERCENT}%")
    if MAX_PRICE is not None:
        lines.append(f"Maks. cena: {MAX_PRICE}")
    lines.append(f"Tryb pracy: {RUN_MODE}")
    return "\n".join(lines)


def format_stores_message() -> str:
    lines = ["<b>Aktywne sklepy:</b>"]
    if STORE_IDS:
        for sid in STORE_IDS:
            lines.append(f"- {store_display_name(sid)} ({sid})")
    else:
        lines.append("(brak - monitoring nie pobierze zadnych ofert)")
    lines.append("")
    lines.append("<b>Wszystkie dostepne sklepy:</b>")
    for sid, info in KNOWN_STORES.items():
        lines.append(f"- {info['name']} ({sid})")
    lines.append("")
    lines.append(
        "Uzyj /dodajsklep <ID>, np. /dodajsklep 1224, zeby dodac sklep, "
        "albo /usunsklep <ID>, np. /usunsklep 294, zeby go usunac."
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
        if normalize_text(arg) in NORMALIZED_TERMS:
            return f"'{arg}' juz jest na liscie."
        SEARCH_TERMS.append(arg)
        refresh_normalized_terms()
        DYNAMIC_STATE["search_terms"] = SEARCH_TERMS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Dodano '{arg}'. Aktualna lista: {', '.join(SEARCH_TERMS)}"

    if cmd in ("/usun", "/remove"):
        if not arg:
            return "Podaj slowo do usuniecia, np. /usun stall"
        norm_arg = normalize_text(arg)
        new_terms = [t for t in SEARCH_TERMS if normalize_text(t) != norm_arg]
        if len(new_terms) == len(SEARCH_TERMS):
            return f"'{arg}' nie bylo na liscie."
        SEARCH_TERMS = new_terms
        refresh_normalized_terms()
        DYNAMIC_STATE["search_terms"] = SEARCH_TERMS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Usunieto '{arg}'. Aktualna lista: {', '.join(SEARCH_TERMS) or '(brak)'}"

    if cmd == "/numer":
        if not arg:
            return "Podaj numer artykulu, np. /numer 90557419"
        if arg in SEARCH_ARTICLE_NUMBERS:
            return f"Numer '{arg}' juz jest na liscie."
        SEARCH_ARTICLE_NUMBERS.append(arg)
        DYNAMIC_STATE["search_article_numbers"] = SEARCH_ARTICLE_NUMBERS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Dodano numer '{arg}'. Aktualna lista: {', '.join(SEARCH_ARTICLE_NUMBERS)}"

    if cmd == "/usunnumer":
        if not arg:
            return "Podaj numer artykulu do usuniecia, np. /usunnumer 90557419"
        if arg not in SEARCH_ARTICLE_NUMBERS:
            return f"Numeru '{arg}' nie bylo na liscie."
        SEARCH_ARTICLE_NUMBERS = [n for n in SEARCH_ARTICLE_NUMBERS if n != arg]
        DYNAMIC_STATE["search_article_numbers"] = SEARCH_ARTICLE_NUMBERS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Usunieto numer '{arg}'. Aktualna lista: {', '.join(SEARCH_ARTICLE_NUMBERS) or '(brak)'}"

    if cmd in ("/sklepy", "/stores"):
        return format_stores_message()

    if cmd in ("/dodajsklep", "/addstore"):
        if not arg:
            return "Podaj ID sklepu, np. /dodajsklep 1224\n\n" + format_stores_message()
        store_id = arg
        if store_id not in KNOWN_STORES:
            return f"Nieznany ID sklepu '{store_id}'.\n\n" + format_stores_message()
        if store_id in STORE_IDS:
            return f"{store_display_name(store_id)} ({store_id}) juz jest aktywny."
        STORE_IDS.append(store_id)
        DYNAMIC_STATE["store_ids"] = STORE_IDS
        save_dynamic_state(DYNAMIC_STATE)
        return f"Dodano sklep {store_display_name(store_id)} ({store_id}) do monitoringu."

    if cmd in ("/usunsklep", "/removestore"):
        if not arg:
            return "Podaj ID sklepu do usuniecia, np. /usunsklep 294"
        store_id = arg
        if store_id not in STORE_IDS:
            return f"Sklep '{store_id}' nie jest aktywny."
        STORE_IDS = [s for s in STORE_IDS if s != store_id]
        DYNAMIC_STATE["store_ids"] = STORE_IDS
        save_dynamic_state(DYNAMIC_STATE)
        reply = f"Usunieto sklep {store_display_name(store_id)} ({store_id}) z monitoringu."
        if not STORE_IDS:
            reply += (
                "\n\nUwaga: nie masz juz zadnych aktywnych sklepow - monitoring "
                "nie pobierze zadnych ofert, dopoki nie dodasz przynajmniej "
                "jednego (/dodajsklep <ID>)."
            )
        return reply

    if cmd in ("/status", "/lista"):
        return format_status_message()

    if cmd in ("/pomoc", "/help", "/start"):
        return format_help_message()

    return f"Nieznana komenda: {cmd}\n\n{format_help_message()}"


def handle_telegram_updates() -> None:
    """Sprawdza nowe wiadomosci od ostatniego razu i wykonuje komendy.
    Ignoruje wiadomosci od kogokolwiek innego niz TELEGRAM_CHAT_ID."""
    offset = load_telegram_offset()
    updates = telegram_get_updates(offset)

    max_update_id = offset - 1
    for update in updates:
        max_update_id = max(max_update_id, update.get("update_id", max_update_id))
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue

        chat_id = message.get("chat", {}).get("id")
        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            continue  # nieautoryzowany nadawca - ignoruj

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        try:
            reply = handle_command(cmd, arg)
        except Exception as exc:
            reply = f"Blad przy obsludze komendy: {exc}"

        try:
            telegram_send_message(reply, chat_id=chat_id)
        except Exception as exc:
            log(f"Nie udalo sie odpowiedziec na Telegramie: {exc}", to_stderr=True)

    if updates:
        save_telegram_offset(max_update_id + 1)


# ---------------- GLOWNA LOGIKA (jeden cykl sprawdzenia ofert) ----------------

def run_ikea_check_cycle() -> int:
    try:
        content = fetch_all_offers()
    except Exception as exc:
        log(f"Blad zapytania do API: {exc}", to_stderr=True)
        return 1

    matching_offers = flatten_matching_offers(content)
    current_uuids = {o["offer_uuid"] for o in matching_offers if o.get("offer_uuid")}

    try:
        seen_uuids = load_seen_uuids()
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Blad odczytu pliku stanu: {exc}", to_stderr=True)
        return 1

    first_run = not os.path.exists(STATE_FILE)
    if first_run and not ALERT_EXISTING_ON_FIRST_RUN:
        try:
            save_seen_uuids(current_uuids)
        except OSError as exc:
            log(f"Blad zapisu pliku stanu: {exc}", to_stderr=True)
            return 3
        log(
            "Pierwsze uruchomienie: zapisano stan bez wysylania alertu. "
            f"Znaleziono dopasowan: {len(matching_offers)}."
        )
        return 0

    new_offers = [
        o for o in matching_offers
        if o.get("offer_uuid") and o["offer_uuid"] not in seen_uuids
    ]

    if new_offers:
        errors = notify(new_offers)
        if errors:
            for err in errors:
                log(f"Blad wysylki powiadomienia ({err})", to_stderr=True)
            if len(errors) == (1 + (1 if TELEGRAM_ENABLED else 0)):
                return 2

        sent_uuids = {o["offer_uuid"] for o in new_offers if o.get("offer_uuid")}
        try:
            save_seen_uuids(seen_uuids | sent_uuids)
        except OSError as exc:
            log(f"Powiadomienie wyslane, ale nie udalo sie zapisac stanu: {exc}", to_stderr=True)
            return 3

        log(f"Wyslano powiadomienie: {len(new_offers)} nowa(e) oferta(y).")
        return 0

    try:
        save_seen_uuids(seen_uuids | current_uuids)
    except OSError as exc:
        log(f"Blad zapisu pliku stanu: {exc}", to_stderr=True)
        return 3

    log(f"Brak nowych ofert (aktualnie widocznych dopasowan: {len(matching_offers)}).")
    return 0


def run_daemon() -> None:
    """Petla na potrzeby usterk systemd - Telegram sprawdzany czesto,
    oferty IKEA rzadziej."""
    log(
        f"Start w trybie daemon (IKEA co {CHECK_INTERVAL_SECONDS}s, "
        f"Telegram co {TELEGRAM_POLL_INTERVAL_SECONDS}s)."
    )
    last_ikea_check = 0.0

    while True:
        if TELEGRAM_ENABLED:
            try:
                handle_telegram_updates()
            except Exception as exc:
                log(f"Blad obslugi komend Telegrama: {exc}", to_stderr=True)

        now = time.time()
        if now - last_ikea_check >= CHECK_INTERVAL_SECONDS:
            run_ikea_check_cycle()
            last_ikea_check = now

        time.sleep(TELEGRAM_POLL_INTERVAL_SECONDS)


def main() -> int:
    if TELEGRAM_ENABLED and RUN_MODE != "daemon":
        # W trybie cron sprawdzamy komendy raz na starcie, przed
        # sprawdzeniem ofert - w trybie daemon robi to petla w run_daemon().
        try:
            handle_telegram_updates()
        except Exception as exc:
            log(f"Blad obslugi komend Telegrama: {exc}", to_stderr=True)

    if RUN_MODE == "daemon":
        run_daemon()
        return 0  # nieosiagalne w normalnych warunkach - run_daemon() nie wraca

    return run_ikea_check_cycle()


if __name__ == "__main__":
    sys.exit(main())
