# ikeaokazje

Skrypt, ktory sprawdza dzial "Okazje na okraglo" (second-hand) w wybranych
sklepach IKEA i wysyla powiadomienie (e-mail i/albo Telegram), jak pojawi
sie produkt, na ktory czekasz. Jesli skonfigurujesz Telegrama, mozesz
takze zarzadzac lista szukanych slow/numerow prosto z czatu z botem.

Zrobilem go, bo chcialem regal STALL, ktory rzadko trafia do tego dzialu,
a reczne odswiezanie strony kilka razy dziennie mi sie po tygodniu
znudzilo. Strona "Okazje na okraglo" to SPA, wiec caly ruch idzie do
prywatnego API IKEA - `web-api.ikea.com/circular/circular-asis/...` -
znalezionego w devtoolsach (zakladka Network) po otwarciu strony.

## Zanim zaczniesz - ważna uwaga

To API nie jest publicznie dokumentowane. Znalazlem je, bo strona z
niego korzysta, ale IKEA moze je zmienic albo zablokowac w kazdej chwili,
bez ostrzezenia.

Endpoint jest chroniony przez Cloudflare na poziomie fingerprintu TLS,
nie tylko naglowkow HTTP. Dlatego uzywam `curl_cffi` (a nie zwyklego
`requests`) - `HEADERS` i `IMPERSONATE` w skrypcie musza sie wzajemnie
zgadzac (ta sama wersja Chrome) - to jedyne dwie rzeczy w kodzie, ktorych
bym nie ruszal.

## Kod vs ustawienia

**Caly kod (`ikea_okazje.py`) mozesz spokojnie aktualizowac z GitHuba** -
`git pull` nigdy nie nadpisze Twoich osobistych ustawien, bo one nie sa
w tym pliku - sa w `~/.config/ikea-okazje.env`.

## Instalacja

```
pip install curl_cffi
```

Dziala od Pythona 3.8+ (curl_cffi z impersonacja tego wymaga).

## Konfiguracja

```
mkdir -p ~/.config
cp .env.example ~/.config/ikea-okazje.env
chmod 600 ~/.config/ikea-okazje.env
nano ~/.config/ikea-okazje.env
```

| Pole | Opis | Domyslnie |
|---|---|---|
| `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO` | dane logowania do wysylki maila | wymagane |
| `SMTP_MODE` | `gmail`, `local587` albo `exim` | `gmail` |
| `SMTP_HOST` | tylko dla `local587`/`exim`, jesli nie `localhost` | `localhost` |
| `VERIFY_TLS` | `false`, jesli lokalny MTA ma zly certyfikat | `true` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | drugi kanal + komendy | wylaczone |
| `STORE_IDS` | numery sklepow IKEA, po przecinku | `294` |
| `SEARCH_TERMS` | szukane frazy - **tylko na starcie**, patrz nizej | `Stall` |
| `SEARCH_ARTICLE_NUMBERS` | numery artykulu - **tylko na starcie** | brak |
| `MIN_DISCOUNT_PERCENT` | minimalny rabat % | brak |
| `MAX_PRICE` | maksymalna cena | brak |
| `KEYWORDS_EXCLUDE` | czarna lista slow, po przecinku | brak |
| `ALERT_EXISTING_ON_FIRST_RUN` | alert od razu na starcie | `false` |
| `RUN_MODE` | `cron` albo `daemon` | `cron` |
| `CHECK_INTERVAL_SECONDS` | tylko dla `daemon` - co ile sprawdzac IKEA | `420` |
| `TELEGRAM_POLL_INTERVAL_SECONDS` | tylko dla `daemon` - co ile sprawdzac komendy | `15` |

### Waznie: SEARCH_TERMS/SEARCH_ARTICLE_NUMBERS dzialaja tylko RAZ

`SEARCH_TERMS` i `SEARCH_ARTICLE_NUMBERS` z `.env` sa uzywane wylacznie
do zasiania pliku `~/.ikea_okazje_dynamic.json` **przy pierwszym
uruchomieniu**. Od tego momentu prawda jest w tym pliku JSON, a nie w
`.env` - zarzadzasz lista komendami w Telegramie (`/dodaj`, `/usun`,
`/numer`, `/usunnumer`) albo recznie edytujac ten plik JSON. Jesli
chcesz zresetowac liste do tego, co masz w `.env`, usun plik:

```
rm ~/.ikea_okazje_dynamic.json
```

i uruchom skrypt ponownie - zasieje sie na nowo z `.env`.

### Komendy Telegrama

Jesli skonfigurujesz `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, mozesz
pisac do bota:

```
/dodaj stall          - dodaj slowo kluczowe
/usun stall            - usun slowo kluczowe
/numer 90557419        - dodaj numer artykulu
/usunnumer 90557419    - usun numer artykulu
/status                - pokaz aktualny monitoring (sklepy, slowa, numery, filtry)
/pomoc                 - lista komend
```

Bot reaguje tylko na wiadomosci z Twojego `TELEGRAM_CHAT_ID` - komendy
od kogokolwiek innego sa ignorowane.

**W trybie `cron`** komendy sa sprawdzane raz na starcie kazdego
przebiegu - czyli reakcja przychodzi w ciagu maks. jednego interwalu
crona (np. do 7 minut). **W trybie `daemon`** komendy sa sprawdzane co
`TELEGRAM_POLL_INTERVAL_SECONDS` (domyslnie 15s), niezaleznie od tego,
jak rzadko sprawdzane sa oferty IKEA - reakcja jest praktycznie od razu.

Jak zalozyc bota - patrz sekcja "Telegram" nizej.

### Filtry

```
MIN_DISCOUNT_PERCENT=30
MAX_PRICE=300
KEYWORDS_EXCLUDE=front,uchwyt,noga,sruba
```

### Telegram - jak zalozyc bota

1. W Telegramie wyszukaj `@BotFather`, wyslij `/newbot`.
2. Podaj nazwe i login konczacy sie na `bot`.
3. BotFather odpowie tokenem (`123456789:ABC...`) - to `TELEGRAM_BOT_TOKEN`.
4. Napisz cokolwiek do swojego nowego bota (musisz zaczac rozmowe pierwszy).
5. Wejdz na `https://api.telegram.org/bot<TOKEN>/getUpdates`, znajdz
   `"chat":{"id": ...}` - to `TELEGRAM_CHAT_ID`.

Jesli `getUpdates` zwraca `{"ok":true,"result":[]}`, jeszcze nie
wyslales wiadomosci do bota - zrob to i odswiez ponownie.

## Dwa tryby pracy

### Tryb "cron" (domyslny)

Jedno przejscie i wyjscie - klasyczne uzycie z crona:

```
crontab -e
```

```
*/7 * * * * /usr/bin/flock -n ~/.ikea_okazje.lock /usr/bin/python3 ~/ikea_okazje.py >> ~/ikea_okazje.log 2>&1
```

Prostsze w konfiguracji, ale reakcja na komendy Telegrama i wykrycie
nowej oferty ograniczone sa do interwalu crona.

### Tryb "daemon" (systemd)

Dziala caly czas w tle - sprawdza komendy Telegrama czesto (sekundy), a
oferty IKEA rzadziej (minuty), bez zaleznosci od crona.

```
RUN_MODE=daemon
```

w `.env`, a potem zainstaluj usluge (przykladowy plik `ikea-okazje.service`
w tym repo - podmien `TWOJ_UZYTKOWNIK` na swojego uzytkownika):

```
sudo cp ikea-okazje.service /etc/systemd/system/
sudo nano /etc/systemd/system/ikea-okazje.service   # podmien TWOJ_UZYTKOWNIK
sudo systemctl daemon-reload
sudo systemctl enable --now ikea-okazje
sudo systemctl status ikea-okazje
journalctl -u ikea-okazje -f
```

Wybierz jeden z dwoch trybow - nie odpalaj jednoczesnie crona i usterk
systemd dla tego samego skryptu, bedziesz miec podwojne powiadomienia.

## Uzycie

```
python3 ikea_okazje.py
```

Pierwsze uruchomienie nie wysle powiadomienia, nawet jesli od razu
znajdzie dopasowanie - zapisuje aktualny stan jako "juz znany". Ustaw
`ALERT_EXISTING_ON_FIRST_RUN=true`, jesli chcesz alert od razu.

Kazde powiadomienie zawiera cene, procent rabatu, stan produktu, numer
artykulu i link. Logi maja znacznik czasu.

## Aktualizacja skryptu

```
git pull
```

Twoje ustawienia w `.env` i dynamiczna lista w `~/.ikea_okazje_dynamic.json`
zostaja nietkniete.

## Typowe problemy

**Blokada Cloudflare / 403.** `IMPERSONATE` musi zgadzac sie z wersja
Chrome w `HEADERS`.

**`Size must be less than or equal to 64`.** `PAGE_SIZE` juz jest na `64`.

**Certyfikat SSL wygasl / hostname mismatch.** Sprawdz
`sudo certbot certificates` i uprawnienia plikow certyfikatu dla Twojego
MTA.

**Telegram nie wysyla / brak `chat_id`.** Wyslij najpierw jakas
wiadomosc do bota - Telegram wymaga, zeby rozmowe zaczynal czlowiek.

**Komendy w Telegramie nie dzialaja.** Sprawdz, czy piszesz z tego
samego konta, ktorego `chat_id` jest w `.env` - bot ignoruje wiadomosci
od innych nadawcow. W trybie `cron` komenda zadziala dopiero przy
nastepnym przebiegu (do interwalu crona).

## Licencja

MIT - rob z tym co chcesz.
