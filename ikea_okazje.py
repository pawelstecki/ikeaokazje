#!/usr/bin/env python3
"""
ikea_okazje.py

Male narzedzie, ktore sprawdza dzial "Okazje na okraglo" (second-hand /
buy-from-ikea) w wybranym sklepie IKEA i wysyla e-mail, kiedy pojawi sie
produkt, ktorego szukasz.

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
    2. Ustaw SEARCH_TERMS / SEARCH_ARTICLE_NUMBERS i STORE_ID nizej.
    3. Odpal recznie raz, zeby sprawdzic ze dziala:
           python3 ikea_okazje.py
    4. Wrzuc do crona, np. co 7 minut, z flockiem (patrz README).
"""

import json
import os
import smtplib
import ssl
import sys
import unicodedata
from email.message import EmailMessage

from curl_cffi import requests

# ---------------- KONFIGURACJA ----------------
API_URL = "https://web-api.ikea.com/circular/circular-asis/offers/grouped/search"

# Numer sklepu IKEA, ktory ma byc monitorowany. Znajdziesz go w
# devtoolsach przegladarki (zakladka Network) po otwarciu strony
# "Okazje na okraglo" i wybraniu swojego sklepu - parametr storeIds
# w zapytaniu do API.
STORE_ID = "294"

PAGE_SIZE = "64"   # API ogranicza max rozmiar strony do 64
MAX_PAGES = 20      # zabezpieczenie przed niekonczaca sie paginacja

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
# co u mnie dziala stabilnie.
IMPERSONATE = "chrome124"

# Szukane frazy - dopasowanie ignoruje wielkosc liter i akcenty, oraz
# szuka jako podciag, wiec krotki rdzen wylapie tez odmiany slowa, np.
# "poscie" zlapie "poscielp", "poscieli", "poscielowy" itd.
SEARCH_TERMS = ["Stall"]

# Numery artykulu do dopasowania 1:1 (dokladne, bez normalizacji) -
# przydatne jak juz wiesz konkretny numer produktu i chcesz go pilnowac
# niezaleznie od tego, jak IKEA go akurat nazwie/opisze.
SEARCH_ARTICLE_NUMBERS = []

STATE_FILE = os.path.expanduser("~/.ikea_okazje_seen_offers.json")
RAW_DUMP_FILE = os.path.expanduser("~/.ikea_okazje_last_raw.json")

# Przy pierwszym uruchomieniu (brak pliku stanu) nie chcemy alertu dla
# wszystkiego, co jest widoczne w tej chwili - zapisujemy to jako
# "juz znane" i czekamy na kolejne, nowe oferty.
ALERT_EXISTING_ON_FIRST_RUN = False

# --- SMTP ---
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


def dump_raw(data) -> None:
    try:
        with open(RAW_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def fetch_all_offers() -> list:
    """Pobiera wszystkie strony wynikow (dopoki API zglasza kolejne)."""
    all_content = []
    page = 0

    while page < MAX_PAGES:
        params = {
            "languageCode": "pl",
            "size": PAGE_SIZE,
            "storeIds": STORE_ID,
            "page": str(page),
        }
        resp = requests.get(
            API_URL,
            headers=HEADERS,
            params=params,
            timeout=REQUEST_TIMEOUT,
            impersonate=IMPERSONATE,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} (strona {page}): {resp.text[:300]}")

        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Odpowiedz JSON nie jest obiektem (strona {page}).")

        content = data.get("content")
        if not isinstance(content, list):
            raise RuntimeError(
                f"Brak listy 'content' (strona {page}). Klucze: {list(data.keys())[:20]}"
            )

        all_content.extend(content)

        if page == 0:
            meta = {k: v for k, v in data.items() if k != "content"}
            dump_raw({"page0_metadata": meta, "content": content})

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


def product_matches(product: dict) -> bool:
    haystack = normalize_text(f"{product.get('title', '')} {product.get('description', '')}")
    text_match = any(term in haystack for term in NORMALIZED_TERMS)

    article_numbers = product.get("articleNumbers") or []
    article_match = any(an in article_numbers for an in SEARCH_ARTICLE_NUMBERS)

    return text_match or article_match


def flatten_matching_offers(content: list) -> list:
    result = []
    for product in content:
        if not product_matches(product):
            continue
        for offer in product.get("offers", []):
            result.append({
                "offer_uuid": offer.get("offerUuid"),
                "offer_number": offer.get("offerNumber"),
                "title": product.get("title"),
                "description": product.get("description"),
                "article_numbers": product.get("articleNumbers"),
                "currency": product.get("currency"),
                "price": offer.get("price"),
                "original_price": product.get("originalPrice"),
                "condition": offer.get("productConditionTitle"),
                "condition_desc": offer.get("productConditionDescription"),
                "reason_discount": offer.get("reasonDiscount"),
                "additional_info": offer.get("additionalInfo"),
                "hero_image": product.get("heroImage"),
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
    lines = [
        f"Produkt: {o['title']} - {o['description']}",
        f"Cena: {o['price']} {o['currency']} (cena wyjsciowa: {o['original_price']} {o['currency']})",
        f"Stan: {o['condition']} ({o['condition_desc']})",
        f"Powod przeceny: {o['reason_discount']}",
        f"Info: {o['additional_info']}",
        f"Numer artykulu: {', '.join(o['article_numbers'] or [])}",
        f"Numer oferty: {o['offer_number']}",
        f"Zdjecie: {o['hero_image']}",
    ]
    return "\n".join(lines)


def send_email(new_offers) -> None:
    titles = ", ".join(sorted({o["title"] for o in new_offers}))
    subject = f"IKEA Okazje: nowa oferta - {titles}"

    blocks = [format_offer_block(o) for o in new_offers]
    body = (
        "Znaleziono nowe oferty dla: " + ", ".join(SEARCH_TERMS) + "\n"
        f"Sklep: storeIds={STORE_ID}\n\n"
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


def main() -> int:
    try:
        content = fetch_all_offers()
    except Exception as exc:
        print(f"Blad zapytania do API: {exc}", file=sys.stderr)
        return 1

    matching_offers = flatten_matching_offers(content)
    current_uuids = {o["offer_uuid"] for o in matching_offers if o.get("offer_uuid")}

    try:
        seen_uuids = load_seen_uuids()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Blad odczytu pliku stanu: {exc}", file=sys.stderr)
        return 1

    first_run = not os.path.exists(STATE_FILE)
    if first_run and not ALERT_EXISTING_ON_FIRST_RUN:
        try:
            save_seen_uuids(current_uuids)
        except OSError as exc:
            print(f"Blad zapisu pliku stanu: {exc}", file=sys.stderr)
            return 3
        print(
            "Pierwsze uruchomienie: zapisano stan bez wysylania alertu. "
            f"Znaleziono dopasowan: {len(matching_offers)}."
        )
        return 0

    new_offers = [
        o for o in matching_offers
        if o.get("offer_uuid") and o["offer_uuid"] not in seen_uuids
    ]

    if new_offers:
        try:
            send_email(new_offers)
        except Exception as exc:
            print(f"Blad wysylki e-maila: {exc}", file=sys.stderr)
            return 2

        sent_uuids = {o["offer_uuid"] for o in new_offers if o.get("offer_uuid")}
        try:
            save_seen_uuids(seen_uuids | sent_uuids)
        except OSError as exc:
            print(f"Mail wyslany, ale nie udalo sie zapisac stanu: {exc}", file=sys.stderr)
            return 3

        print(f"Wyslano e-mail: {len(new_offers)} nowa(e) oferta(y).")
        return 0

    try:
        save_seen_uuids(seen_uuids | current_uuids)
    except OSError as exc:
        print(f"Blad zapisu pliku stanu: {exc}", file=sys.stderr)
        return 3

    print(f"Brak nowych ofert (aktualnie widocznych dopasowan: {len(matching_offers)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
