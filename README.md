# ikeaokazje

Skrypt, który sprawdza dział "Okazje na okrągło" (second-hand) w wybranych
sklepach IKEA i wysyła powiadomienie (e-mail i/albo Telegram), jak pojawi
się produkt, na który czekasz. Jeśli skonfigurujesz Telegrama, możesz
także zarządzać listą szukanych słów/numerów prosto z czatu z botem.

Strona "Okazje na okrągło" to SPA, więc cały ruch idzie do prywatnego
API IKEA - `web-api.ikea.com/circular/circular-asis/...` - znalezionego
w devtoolsach (zakładka Network) po otwarciu strony.

## Zanim zaczniesz - ważna uwaga

To API nie jest publicznie dokumentowane. Znalazłem je, bo strona z
niego korzysta, ale IKEA może je zmienić albo zablokować w każdej chwili,
bez ostrzeżenia.

Endpoint jest chroniony przez Cloudflare na poziomie fingerprintu TLS,
nie tylko nagłówków HTTP. Dlatego używam `curl_cffi` (a nie zwykłego
`requests`) - `HEADERS` i `IMPERSONATE` w skrypcie muszą się wzajemnie
zgadzać (ta sama wersja Chrome) - to jedyne dwie rzeczy w kodzie, których
bym nie ruszał.

## Kod vs ustawienia

**Cały kod (`ikea_okazje.py`) możesz spokojnie aktualizować z GitHuba** -
`git pull` nigdy nie nadpisze Twoich osobistych ustawień, bo one nie są
w tym pliku - są w `~/.config/ikea-okazje.env`.

## Instalacja

```
pip install curl_cffi
```

Działa od Pythona 3.8+ (curl_cffi z impersonacją tego wymaga).

## Konfiguracja

```
mkdir -p ~/.config
cp .env.example ~/.config/ikea-okazje.env
chmod 600 ~/.config/ikea-okazje.env
nano ~/.config/ikea-okazje.env
```

| Pole | Opis | Domyślnie |
|---|---|---|
| `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO` | dane logowania do wysyłki maila | wymagane |
| `SMTP_MODE` | `gmail`, `local587` albo `exim` | `gmail` |
| `SMTP_HOST` | tylko dla `local587`/`exim`, jeśli nie `localhost` | `localhost` |
| `VERIFY_TLS` | `false`, jeśli lokalny MTA ma zły certyfikat | `true` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | drugi kanał + komendy | wyłączone |
| `STORE_IDS` | numery sklepów IKEA, po przecinku | `294` |
| `SEARCH_TERMS` | szukane frazy - **tylko na starcie**, patrz niżej | `Stall` |
| `SEARCH_ARTICLE_NUMBERS` | numery artykułu - **tylko na starcie** | brak |
| `MIN_DISCOUNT_PERCENT` | minimalny rabat % | brak |
| `MAX_PRICE` | maksymalna cena | brak |
| `KEYWORDS_EXCLUDE` | czarna lista słów, po przecinku | brak |
| `ALERT_EXISTING_ON_FIRST_RUN` | alert od razu na starcie | `false` |
| `RUN_MODE` | `cron` albo `daemon` | `cron` |
| `CHECK_INTERVAL_SECONDS` | tylko dla `daemon` - co ile sprawdzać IKEA | `420` |
| `TELEGRAM_POLL_INTERVAL_SECONDS` | tylko dla `daemon` - co ile sprawdzać komendy | `15` |

### Ważne: SEARCH_TERMS/SEARCH_ARTICLE_NUMBERS działają tylko RAZ

`SEARCH_TERMS` i `SEARCH_ARTICLE_NUMBERS` z `.env` są używane wyłącznie
do zasiania pliku `~/.ikea_okazje_dynamic.json` **przy pierwszym
uruchomieniu**. Od tego momentu prawda jest w tym pliku JSON, a nie w
`.env` - zarządzasz listą komendami w Telegramie (`/dodaj`, `/usun`,
`/numer`, `/usunnumer`) albo ręcznie edytując ten plik JSON. Jeśli
chcesz zresetować listę do tego, co masz w `.env`, usuń plik:

```
rm ~/.ikea_okazje_dynamic.json
```

i uruchom skrypt ponownie - zasieje się na nowo z `.env`.

### Komendy Telegrama

Jeśli skonfigurujesz `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, możesz
pisać do bota:

```
/dodaj stall          - dodaj słowo kluczowe
/usun stall            - usuń słowo kluczowe
/numer 90557419        - dodaj numer artykułu
/usunnumer 90557419    - usuń numer artykułu
/status                - pokaż aktualny monitoring (sklepy, słowa, numery, filtry)
/pomoc                 - lista komend
```

Bot reaguje tylko na wiadomości z Twojego `TELEGRAM_CHAT_ID` - komendy
od kogokolwiek innego są ignorowane.

**W trybie `cron`** komendy są sprawdzane raz na starcie każdego
przebiegu - czyli reakcja przychodzi w ciągu maks. jednego interwału
crona (np. do 7 minut). **W trybie `daemon`** komendy są sprawdzane co
`TELEGRAM_POLL_INTERVAL_SECONDS` (domyślnie 15s), niezależnie od tego,
jak rzadko sprawdzane są oferty IKEA - reakcja jest praktycznie od razu.

Jak założyć bota - patrz sekcja "Telegram" niżej.

### Filtry

```
MIN_DISCOUNT_PERCENT=30
MAX_PRICE=300
KEYWORDS_EXCLUDE=front,uchwyt,noga,sruba
```

### Telegram - jak założyć bota

1. W Telegramie wyszukaj `@BotFather`, wyślij `/newbot`.
2. Podaj nazwę i login kończący się na `bot`.
3. BotFather odpowie tokenem (`123456789:ABC...`) - to `TELEGRAM_BOT_TOKEN`.
4. Napisz cokolwiek do swojego nowego bota (musisz zacząć rozmowę pierwszy).
5. Wejdź na `https://api.telegram.org/bot<TOKEN>/getUpdates`, znajdź
   `"chat":{"id": ...}` - to `TELEGRAM_CHAT_ID`.

Jeśli `getUpdates` zwraca `{"ok":true,"result":[]}`, jeszcze nie
wysłałeś wiadomości do bota - zrób to i odśwież ponownie.

## Dwa tryby pracy

### Tryb "cron" (domyślny)

Jedno przejście i wyjście - klasyczne użycie z crona:

```
crontab -e
```

```
*/7 * * * * /usr/bin/flock -n ~/.ikea_okazje.lock /usr/bin/python3 ~/ikea_okazje.py >> ~/ikea_okazje.log 2>&1
```

Prostsze w konfiguracji, ale reakcja na komendy Telegrama i wykrycie
nowej oferty ograniczone są do interwału crona.

### Tryb "daemon" (systemd)

Działa cały czas w tle - sprawdza komendy Telegrama często (sekundy), a
oferty IKEA rzadziej (minuty), bez zależności od crona.

```
RUN_MODE=daemon
```

w `.env`, a potem zainstaluj usługę (przykładowy plik `ikea-okazje.service`
w tym repo - podmień `TWOJ_UZYTKOWNIK` na swojego użytkownika):

```
sudo cp ikea-okazje.service /etc/systemd/system/
sudo nano /etc/systemd/system/ikea-okazje.service   # podmień TWOJ_UZYTKOWNIK
sudo systemctl daemon-reload
sudo systemctl enable --now ikea-okazje
sudo systemctl status ikea-okazje
journalctl -u ikea-okazje -f
```

Wybierz jeden z dwóch trybów - nie odpalaj jednocześnie crona i usługi
systemd dla tego samego skryptu, będziesz mieć podwójne powiadomienia.

## Użycie

```
python3 ikea_okazje.py
```

Pierwsze uruchomienie nie wyśle powiadomienia, nawet jeśli od razu
znajdzie dopasowanie - zapisuje aktualny stan jako "już znany". Ustaw
`ALERT_EXISTING_ON_FIRST_RUN=true`, jeśli chcesz alert od razu.

Każde powiadomienie zawiera cenę, procent rabatu, stan produktu, numer
artykułu i link. Logi mają znacznik czasu.

## Aktualizacja skryptu

```
git pull
```

Twoje ustawienia w `.env` i dynamiczna lista w `~/.ikea_okazje_dynamic.json`
zostają nietknięte.

## Typowe problemy

**Blokada Cloudflare / 403.** `IMPERSONATE` musi zgadzać się z wersją
Chrome w `HEADERS`.

**`Size must be less than or equal to 64`.** `PAGE_SIZE` już jest na `64`.

**Certyfikat SSL wygasł / hostname mismatch.** Sprawdź
`sudo certbot certificates` i uprawnienia plików certyfikatu dla Twojego
MTA.

**Telegram nie wysyła / brak `chat_id`.** Wyślij najpierw jakąś
wiadomość do bota - Telegram wymaga, żeby rozmowę zaczynał człowiek.

**Komendy w Telegramie nie działają.** Sprawdź, czy piszesz z tego
samego konta, którego `chat_id` jest w `.env` - bot ignoruje wiadomości
od innych nadawców. W trybie `cron` komenda zadziała dopiero przy
następnym przebiegu (do interwału crona).

## Licencja

Apache License 2.0 - copyright Paweł Stecki. Zobacz plik [LICENSE](LICENSE)
w tym repo po pełny tekst.

Możesz swobodnie kopiować, modyfikować i redystrybuować ten kod, w tym
komercyjnie. Jedyne wymogi: zachowaj oryginalną notatkę o prawach
autorskich (plik LICENSE i, jeśli redystrybuujesz, plik NOTICE) oraz
jasno oznacz, które pliki zmodyfikowałeś, jeśli publikujesz fork.
