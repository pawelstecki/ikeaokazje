#!/usr/bin/env python3
"""
ikea_okazje.py

Male narzedzie, ktore sprawdza dzial "Okazje na okraglo" (second-hand /
buy-from-ikea) w wybranych sklepach IKEA i wysyla powiadomienie (e-mail
i/albo Telegram), kiedy pojawi sie produkt, ktorego szukasz.

Powstalo, bo czekalem na konkretny regal (STALL) i nie chcialem co
wieczor wchodzic na strone recznie. Strona jest zbudowana jako SPA i
caly ruch idzie do prywatnego API IKEA (web-api.ikea.com/circular/...),
ktore trzeba "podszyc" pod prawdziwa przegladarke, bo inaczej Cloudflare
odrzuca zapytanie na poziomie TLS/fingerprintu, zanim nawet dojdzie do
naglowkow HTTP. Stad curl_cffi, a nie zwykle requests.

Wymagania:
    pip install curl_cffi

Uzycie:
    1. Skopiuj .env.example do ~/.config/ikea-okazje.env i wypelnij.
    2. Ustaw SEARCH_TERMS / SEARCH_ARTICLE_NUMBERS i STORE_IDS nizej.
    3. Odpal recznie raz, zeby sprawdzic ze dziala:
           python3 ikea_okazje.py
    4. Wrzuc do crona, np. co 7 minut, z flockiem (patrz README).
"""

import json
import os
import random
from datetime import datetime
import smtplib
import ssl
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from email.message import EmailMessage

from curl_cffi import requests

# ---------------- KONFIGURACJA ----------------
API_URL = "https://web-api.ikea.com/circular/circular-asis/offers/grouped/search"

# Numery sklepow IKEA do monitorowania. Znajdziesz je w devtoolsach
# przegladarki (zakladka Network) po otwarciu strony "Okazje na okraglo"
# i wybraniu sklepu - parametr storeIds w zapytaniu do API. Mozna podac
# kilka - skrypt sprawdzi kazdy po kolei, z krotka przerwa (jitter)
# miedzy nimi, zamiast odpytywac wszystkie naraz.
STORE_IDS = ["294"]

PAGE_SIZE = "64"   # API ogranicza max rozmiar strony do 64
MAX_PAGES = 20      # zabezpieczenie przed niekonczaca sie paginacja

# Retry z backoffem - IKEA/Cloudflare czasem odpowiada 429 albo 5xx pod
# obciazeniem. Przy takich kodach (i przy bledach polaczenia) skrypt
# probuje ponownie z rosnacym odczekaniem, zamiast od razu sie poddawac.
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0   # sekundy, mnozone x2 przy kazdej kolejnej probie
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Krotka, losowa przerwa miedzy odpytywaniem kolejnych sklepow z listy
# STORE_IDS - zmniejsza szanse, ze wzorzec zapytan bedzie wygladal jak
# automat.
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
# Musi byc zgodne z wersja Chrome zadeklarowana w naglowkach powyzej -
# curl_cffi ma tez nowsze targety (np. chrome131), ale trzymam sie tego,
# co u mnie dziala stabilnie. NIE zmieniaj tego przez .env - to musi byc
# jeden, wewnetrznie spojny zestaw, inaczej Cloudflare znowu zaczyna
# blokowac zapytania.
IMPERSONATE = "chrome124"

# Szukane frazy - dopasowanie ignoruje wielkosc liter i akcenty, oraz
# szuka jako podciag, wiec krotki rdzen wylapie tez odmiany slowa, np.
# "poscie" zlapie "poscielp", "poscieli", "poscielowy" itd.
SEARCH_TERMS = ["Stall"]

# Numery artykulu do dopasowania 1:1 (dokladne, bez normalizacji) -
# przydatne jak juz wiesz konkretny numer produktu i chcesz go pilnowac
# niezaleznie od tego, jak IKEA go akurat nazwie/opisze.
SEARCH_ARTICLE_NUMBERS = []

# --- Filtry (wszystkie domyslnie WYLACZONE - None/pusta lista = brak
# filtrowania po tym kryterium) ---

# Minimalny rabat w procentach wzgledem ceny wyjsciowej produktu.
# Przyklad: MIN_DISCOUNT_PERCENT = 30 odrzuci oferty z rabatem mniejszym
# niz 30%. None = nie filtruj.
MIN_DISCOUNT_PERCENT = None

# Maksymalna cena oferty (w walucie sklepu, zwykle PLN).
# Przyklad: MAX_PRICE = 300 odrzuci oferty drozsze niz 300.
# None = nie filtruj.
MAX_PRICE = None

# Czarna lista slow-kluczowych - jesli tytul/opis produktu zawiera
# ktorekolwiek z tych slow (po normalizacji, jak w SEARCH_TERMS), oferta
# jest calkowicie ignorowana, nawet jesli dopasowuje sie do SEARCH_TERMS.
# Przydatne np. do odsiewania czesci zamiennych: ["front", "uchwyt",
# "noga", "sruba"]. Pusta lista = brak filtrowania.
KEYWORDS_EXCLUDE = []

STATE_FILE = os.path.expanduser("~/.ikea_okazje_seen_offers.json")
RAW_DUMP_FILE = os.path.expanduser("~/.ikea_okazje_last_raw.json")

# Przy pierwszym uruchomieniu (brak pliku stanu) nie chcemy alertu dla
# wszystkiego, co jest widoczne w tej chwili - zapisujemy to jako
# "juz znane" i czekamy na kolejne, nowe oferty.
ALERT_EXISTING_ON_FIRST_RUN = False

# --- E-MAIL ---
# "gmail"    -> smtp.gmail.com:587, STARTTLS + login (haslo aplikacji)
# "local587" -> localhost:587, STARTTLS + login (Twoje lokalne konto
#               pocztowe, jesli masz wlasny serwer z MTA)
# "exim"     -> localhost:25, bez logowania (tylko jesli Twoj lokalny
#               MTA jest juz skonfigurowany do relay'owania na zewnatrz)
SMTP_MODE = "gmail"

# Jesli Twoj lokalny serwer poczty ma certyfikat na inna nazwe albo
# akurat wygasl (zdarza sie), a i tak ufasz temu polaczeniu, bo nie
# wychodzi poza Twoj wlasny serwer - ustaw na False.
VERIFY_TLS = True

SMTP_TIMEOUT = 30
CONFIG_FILE = os.path.expanduser("~/.config/ikea-okazje.env")

REQUEST_TIMEOUT = 15
# ------------------------------------------------


def log(message: str, to_stderr: bool = False) -> None:
    """Print z dopisanym znacznikiem czasu - przydatne w logach crona,
    gdzie kolejne linie inaczej wygladaja identycznie."""
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


ENV = load_env_file(CONFIG_FILE)

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
else:  # "exim" - lokalny MTA bez uwierzytelniania
    SMTP_HOST = ENV.get("SMTP_HOST", "localhost")
    SMTP_PORT = 25
    SMTP_USER = None
    SMTP_PASS = None
    EMAIL_FROM = ENV.get("EMAIL_FROM", "ikea-watch@localhost")
    USE_AUTH = False

EMAIL_TO = ENV.get("EMAIL_TO") or os.environ.get("EMAIL_TO") or EMAIL_FROM

# --- TELEGRAM (opcjonalny drugi kanal powiadomien) ---
# Wypelnij TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID w .env, zeby wlaczyc -
# jesli oba sa puste, ten kanal jest po prostu pomijany.
TELEGRAM_BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = ENV.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def normalize_text(s: str) -> str:
    """Male litery + usuniecie akcentow, np. 'STALL' -> 'stall'.
    Dodatkowo mapuje polskie 'l' z ogonkiem, ktorego NFKD nie rozklada
    na zwykle 'l' plus znak diakrytyczny."""
    if not s:
        return ""
    s = s.replace("\u0142", "l").replace("\u0141", "L")  # l z ogonkiem -> l
    decomposed = unicodedata.normalize("NFKD", s)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold()


NORMALIZED_TERMS = [normalize_text(t) for t in SEARCH_TERMS]
NORMALIZED_EXCLUDE = [normalize_text(t) for t in KEYWORDS_EXCLUDE]


def dump_raw(data) -> None:
    try:
        with open(RAW_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def fetch_page_with_retry(store_id: str, page: int) -> dict:
    """Pobiera jedna strone wynikow, z retry+backoff na bledach 429/5xx
    i na bledach polaczenia."""
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
        except Exception as exc:  # blad polaczenia, timeout itp.
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
    """Pobiera wszystkie strony wynikow dla jednego sklepu."""
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
    """Pobiera oferty ze wszystkich sklepow z STORE_IDS, z krotka
    losowa przerwa miedzy kazdym sklepem."""
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


def build_product_link(article_numbers) -> str:
    if article_numbers:
        query = urllib.parse.quote(article_numbers[0])
        return f"https://www.ikea.com/pl/pl/search/?q={query}"
    return "https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/"


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

        for offer in product.get("offers", []):
            price = offer.get("price")
            discount_percent = calc_discount_percent(price, original_price)

            if MIN_DISCOUNT_PERCENT is not None:
                if discount_percent is None or discount_percent < MIN_DISCOUNT_PERCENT:
                    continue
            if MAX_PRICE is not None:
                if price is None or price > MAX_PRICE:
                    continue

            result.append({
                "offer_uuid": offer.get("offerUuid"),
                "offer_number": offer.get("offerNumber"),
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
                "store_id": product.get("storeId"),
                "link": build_product_link(article_numbers),
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
        f"Sklep: {o['store_id']}",
        f"Link: {o['link']}",
        f"Zdjecie: {o['hero_image']}",
    ]
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
    return (
        f"<b>{title}</b> - {description}\n"
        f"Cena: <b>{o['price']} {o['currency']}</b> "
        f"(z {o['original_price']} {o['currency']}, rabat {discount_txt})\n"
        f"Stan: {o['condition']}\n"
        f"<a href=\"{o['link']}\">Zobacz produkt</a>"
    )


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_telegram(new_offers) -> None:
    text = "\n\n".join(format_offer_telegram(o) for o in new_offers)
    text = f"<b>IKEA Okazje - nowa oferta</b>\n\n{text}"

    # Telegram ma limit ~4096 znakow na wiadomosc - w razie potrzeby
    # obcinamy, zeby wysylka nie padla.
    if len(text) > 4000:
        text = text[:3990] + "\n\n(...)"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
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


def notify(new_offers) -> list:
    """Wysyla powiadomienia wszystkimi skonfigurowanymi kanalami.
    Zwraca liste bledow (pusta = wszystko OK). Jeden kanal padajac nie
    blokuje pozostalych."""
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


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
