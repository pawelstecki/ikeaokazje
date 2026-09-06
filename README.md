# ikeaokazje

Skrypt, który sprawdza dział "Okazje na okrągło" (second-hand) w wybranych
sklepach IKEA i wysyła powiadomienie (e-mail i/albo Telegram), jak pojawi
się produkt, na który czekasz. Jeśli skonfigurujesz Telegrama, możesz
także zarządzać listą szukanych słów/numerów i monitorowanych sklepów
prosto z czatu z botem.

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
python3 -m pip install -r requirements.txt
```

Jedyna zewnętrzna zależność to `curl_cffi` (patrz `requirements.txt`) -
wszystko inne w skrypcie korzysta ze standardowej biblioteki Pythona.
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
| `SMTP_MODE` | `gmail`, `local587`, `exim` albo `disabled` (dowolna inna wartosc konczy dzialanie skryptu czytelnym bledem) | `gmail` |
| `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO` | dane logowania do wysyłki maila - **wymagane, jeśli `SMTP_MODE` nie jest `disabled`** | wymagane (poza `disabled`) |
| `SMTP_HOST` | tylko dla `local587`/`exim`, jeśli nie `localhost` | `localhost` |
| `VERIFY_TLS` | `false`, jeśli lokalny MTA ma zły certyfikat | `true` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | drugi kanał + komendy - **muszą być ustawione oba naraz**, patrz niżej | wyłączone |
| `STORE_IDS` | numery sklepów IKEA, po przecinku - **tylko na starcie**, patrz niżej | `294` |
| `STORE_URL_SLUGS` | opcjonalne nadpisanie/rozszerzenie wbudowanej mapy sklepów dla linków rezerwacji | wbudowana mapa `KNOWN_STORES` |
| `SEARCH_TERMS` | szukane frazy - **tylko na starcie**, patrz niżej | `Stall` |
| `SEARCH_ARTICLE_NUMBERS` | numery artykułu - **tylko na starcie**, normalizowane do samych cyfr (można wpisywać z kropkami/spacjami, np. `905.574.19`) | brak |
| `MIN_DISCOUNT_PERCENT` | minimalny rabat % | brak |
| `MAX_PRICE` | maksymalna cena | brak |
| `KEYWORDS_EXCLUDE` | czarna lista słów, po przecinku | brak |
| `ALERT_EXISTING_ON_FIRST_RUN` | alert od razu na starcie | `false` |
| `RUN_MODE` | `cron` albo `daemon` | `cron` |
| `CHECK_INTERVAL_SECONDS` | tylko dla `daemon` - co ile sprawdzać IKEA (sekundy) | `900` |
| `TELEGRAM_POLL_INTERVAL_SECONDS` | tylko dla `daemon` - co ile sprawdzać komendy Telegrama (sekundy) | `15` |

### Kanały powiadomień: e-mail, Telegram albo oba

E-mail nie jest już obowiązkowy - skrypt wymaga **przynajmniej jednego w
pełni skonfigurowanego kanału powiadomień**, ale to może być tylko
Telegram.

- **`SMTP_MODE=disabled`** wyłącza wysyłkę e-mail całkowicie - skrypt nie
  wymaga wtedy `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`, `SMTP_HOST` ani
  żadnych ustawień TLS, i nie inicjuje żadnego połączenia SMTP.
- **Jeśli `SMTP_MODE` jest `gmail`, `local587` albo `exim`, e-mail musi
  być KOMPLETNIE skonfigurowany** - skrypt sprawdza to od razu przy
  starcie, a nie dopiero przy pierwszej próbie wysyłki:
  - `gmail`/`local587` wymagają niepustych `SMTP_USER` i `SMTP_PASS`
    (obie wartości są używane do `server.login()`) oraz działającego
    adresu odbiorcy;
  - `exim` (bez autentykacji - patrz `send_email()`) NIE wymaga
    `SMTP_USER`/`SMTP_PASS`, ale wymaga jawnie ustawionego `EMAIL_TO`
    w `.env` (dla `exim` `EMAIL_TO` nie ma sensownego automatycznego
    fallbacku, bo domyślny `EMAIL_FROM` to tylko placeholder
    `ikea-watch@localhost`, a nie prawdziwy adres);
  - dla `gmail`/`local587` `EMAIL_TO` może pozostać nieustawiony w
    `.env` - wtedy skrypt używa `SMTP_USER` jako odbiorcy (to jest
    istniejące, zamierzone zachowanie: wysyłasz powiadomienia na tę
    samą skrzynkę, z której są wysyłane).
  - brakujące pole(-a) powodują czytelny błąd przy starcie z listą
    tego, co trzeba uzupełnić.
- Telegram jest traktowany jako aktywny kanał tylko wtedy, gdy **oba**
  pola są ustawione: `TELEGRAM_BOT_TOKEN` i `TELEGRAM_CHAT_ID`. Ustawienie
  tylko jednego z nich (np. tokenu bez chat_id) jest odrzucane przy
  starcie jako niepełna konfiguracja - to dotyczy zarówno wysyłki
  powiadomień, jak i sprawdzania komend w Telegramie.
- Jeśli `SMTP_MODE=disabled` **i** Telegram nie jest w pełni
  skonfigurowany, skrypt odmawia startu z czytelnym błędem - musisz
  skonfigurować e-mail (jeden z `gmail`/`local587`/`exim`, kompletnie)
  albo pełny Telegram.

Przykład konfiguracji "tylko Telegram" (bez e-maila) w `.env`:

```ini
SMTP_MODE=disabled
TELEGRAM_BOT_TOKEN=123456789:TWOJ-TOKEN-Z-BOTFATHER
TELEGRAM_CHAT_ID=987654321
```

Jak założyć bota Telegrama - patrz sekcja "Telegram - jak założyć bota"
niżej.

### Mapa sklepów IKEA (storeId i slug)

Skrypt ma wbudowaną, potwierdzoną mapę wszystkich obsługiwanych sklepów
IKEA w Polsce (stała `KNOWN_STORES` w kodzie). Zawiera zarówno `storeId`
(do odpytywania API IKEA, pole `STORE_IDS`), jak i slug używany w
adresach URL działu "Okazje na Okrągło" (do budowania linków rezerwacji):

| Sklep | storeId | slug |
|---|---:|---|
| Bielsko-Biała | 1224 | `bielsko+biala` |
| IKEA Bydgoszcz | 429 | `bydgoszcz` |
| IKEA Gdańsk | 203 | `gdańsk` |
| IKEA Katowice | 306 | `katowice` |
| IKEA Kraków | 204 | `kraków` |
| IKEA Łódź | 329 | `łódź` |
| IKEA Lublin | 311 | `lublin` |
| IKEA Poznań | 205 | `poznań` |
| IKEA Szczecin | 583 | `szczecin` |
| IKEA Warszawa Janki | 188 | `warszawa+janki` |
| IKEA Warszawa Targówek | 307 | `warszawa+targówek` |
| IKEA Wrocław | 294 | `wrocław` |

Dla sklepów z tej listy wystarczy wpisać sam numer w `STORE_IDS` - link
rezerwacji zostanie zbudowany automatycznie z wbudowanej mapy.

### `STORE_IDS` vs `STORE_URL_SLUGS`

Te dwa pola mają różne role i nie trzeba wypełniać obu:

- **`STORE_IDS`** - decyduje, które sklepy skrypt faktycznie odpytuje w
  API IKEA (czyli w których szuka ofert). To jest wymagane ustawienie.
- **`STORE_URL_SLUGS`** - opcjonalne, ręczne mapowanie `storeId:slug`
  używane tylko do budowania linku rezerwacji w powiadomieniu. Ma
  pierwszeństwo nad wbudowaną mapą `KNOWN_STORES`, więc używaj go tylko,
  gdy: (a) monitorujesz sklep spoza powyższej listy, albo (b) IKEA
  zmieniła routing i wbudowana mapa jest nieaktualna.

Przykład dla samego Wrocławia (slug nie jest wymagany, bo Wrocław jest w
`KNOWN_STORES`, ale można go jawnie nadpisać):

```ini
STORE_IDS=294
STORE_URL_SLUGS=294:wrocław
```

Przykład dla kilku sklepów:

```ini
STORE_IDS=1224,306,294
STORE_URL_SLUGS=1224:bielsko+biala,306:katowice,294:wrocław
```

Znak `+` w slugach typu `bielsko+biala` czy `warszawa+targówek` jest
separatorem spacji używanym przez IKEA w trasach URL i skrypt celowo
zostawia go niezakodowanym (`urllib.parse.quote(..., safe="+")`) - polskie
znaki (np. `ł`, `ó`) są nadal normalnie kodowane w adresie (np. `wrocław`
staje się `wroc%C5%82aw`).

### Linki do rezerwacji ofert

Powiadomienia korzystają z `offerNumber` (zwracanego przez API IKEA) oraz
wbudowanej mapy `KNOWN_STORES` (lub `STORE_URL_SLUGS` z .env, jeśli
ustawione), aby wygenerować bezpośredni link do konkretnej oferty w
dziale "Okazje na Okrągło online":

```
https://www.ikea.com/pl/pl/second-hand/buy-from-ikea/#/<slug-sklepu>/<offerNumber>
```

**Zachowanie awaryjne:** jeśli oferta nie ma `offerNumber` lub dla danego
`storeId` nie ma mapowania na slug (ani w `STORE_URL_SLUGS`, ani w
`KNOWN_STORES`), skrypt **nie generuje** mylącego linku do standardowego
produktu IKEA - zamiast tego powiadomienie zawiera numer oferty i
instrukcję ręcznego wyszukania.

### Ważne: SEARCH_TERMS/SEARCH_ARTICLE_NUMBERS/STORE_IDS działają tylko RAZ

`SEARCH_TERMS`, `SEARCH_ARTICLE_NUMBERS` i `STORE_IDS` z `.env` są
używane wyłącznie do zasiania pliku `~/.ikea_okazje_dynamic.json` **przy
pierwszym uruchomieniu**. Od tego momentu prawda jest w tym pliku JSON, a
nie w `.env` - zarządzasz listami komendami w Telegramie (`/dodaj`,
`/usun`, `/numer`, `/usunnumer`, `/dodajsklep`, `/usunsklep`) albo ręcznie
edytując ten plik JSON. Jeśli chcesz zresetować wszystko do tego, co masz
w `.env`, usuń plik:

```
rm ~/.ikea_okazje_dynamic.json
```

i uruchom skrypt ponownie - zasieje się na nowo z `.env`.

Jeśli aktualizujesz skrypt ze starszej wersji (bez zarządzania sklepami
przez Telegram), a plik `~/.ikea_okazje_dynamic.json` już istnieje, ale
nie ma w nim jeszcze klucza `store_ids`, skrypt automatycznie doda go przy
najbliższym uruchomieniu, zasiewając wartością z `STORE_IDS` w `.env` -
nic nie musisz robić ręcznie.

### Komendy Telegrama

Jeśli skonfigurujesz `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, możesz
pisać do bota:

```
/dodaj stall          - dodaj słowo kluczowe
/usun stall            - usuń słowo kluczowe
/numer 90557419        - dodaj numer artykułu (można też z kropkami/spacjami, np. 905.574.19)
/usunnumer 90557419    - usuń numer artykułu (również z kropkami/spacjami)
/sklepy                - pokaż aktywne i dostępne sklepy
/dodajsklep 1224        - dodaj sklep do monitoringu (po ID)
/usunsklep 294          - usuń sklep z monitoringu (po ID)
/status                - pokaż aktualny monitoring (sklepy, słowa, numery, filtry)
/pomoc                 - lista komend
```

Dostępne są też opcjonalne angielskie aliasy: `/stores`, `/addstore <ID>`,
`/removestore <ID>`.

`/dodajsklep` akceptuje tylko ID obecne w wbudowanej mapie `KNOWN_STORES`
(patrz tabela wyżej) - nieznane ID zostanie odrzucone z podpowiedzią, żeby
sprawdzić `/sklepy`. Możesz usunąć nawet ostatni aktywny sklep, ale bot Cię
o tym ostrzeże, bo bez żadnego aktywnego sklepu monitoring nie pobierze
żadnych ofert.

`/status` pokazuje teraz aktywne sklepy w czytelnej liście, np.:

```
Aktywne sklepy:
- IKEA Wrocław (294)
- Bielsko-Biała (1224)
```

Bot reaguje tylko na wiadomości z Twojego `TELEGRAM_CHAT_ID` - komendy
od kogokolwiek innego są ignorowane.

**W trybie `cron`** komendy są sprawdzane raz na starcie każdego
przebiegu - czyli reakcja przychodzi w ciągu maks. jednego interwału
crona (np. do 15 minut). **W trybie `daemon`** komendy są sprawdzane co
`TELEGRAM_POLL_INTERVAL_SECONDS` (domyślnie 15 sekund), niezależnie od
tego, jak rzadko sprawdzane są oferty IKEA - reakcja jest praktycznie
od razu.

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

## Ochrona przed równoległymi procesami

**Wybierz JEDEN mechanizm trwałego uruchamiania - cron ALBO systemd, nigdy
oba naraz.** Odpalenie crona i usługi systemd równocześnie dla tego samego
skryptu powoduje podwójne powiadomienia (dwa niezależne procesy sprawdzają
te same oferty) oraz podwójne/kolidujące odpowiedzi na te same komendy
Telegrama.

Niezależnie od wybranego mechanizmu, **wszystkie sposoby odpalania
skryptu (cron, systemd, ręczne uruchomienie) muszą używać tego samego,
trwałego pliku blokady** poprzez `flock -n`, np.:

```
/home/TWOJ_UZYTKOWNIK/.ikea_okazje.lock
```

Użyj dokładnie tej samej, jawnej ścieżki (w katalogu domowym użytkownika,
NIE ścieżki względnej zależnej od katalogu roboczego) we wpisie crona,
w pliku usługi systemd i w każdym ręcznym uruchomieniu.

`flock -n` (flaga `-n` = "nie czekaj") celowo **nie czeka i nie buduje
kolejki** - jeśli blokada jest już zajęta (bo poprzedni cykl monitoringu
jeszcze trwa albo działa druga kopia procesu), nowa próba odpalenia po
prostu nic nie robi i wychodzi. Dzięki temu stare cykle nigdy się nie
kolejkują i dwie kopie monitoringu nigdy nie działają naraz - to jest
zamierzone zachowanie, nie błąd.

**Dla usługi systemd** przykładowy plik `ikea-okazje.service` używa
dodatkowo `flock -n -E 75 ...` w połączeniu z
`RestartPreventExitStatus=75` w sekcji `[Service]`. Flaga `-E 75` mówi
`flock`, żeby w sytuacji "blokada jest już zajęta" zakończył się
dedykowanym kodem wyjścia `75` (a nie standardowym `1`), a
`RestartPreventExitStatus=75` mówi systemd, żeby **nie** traktował tego
konkretnego kodu jako awarii i **nie** restartował usługi. Bez tego
`Restart=on-failure` wchodziłoby w pętlę restartów co `RestartSec`,
mimo że zajęta blokada jest zamierzonym, a nie błędnym stanem.
Prawdziwe awarie skryptu (np. błąd zapytania do API IKEA) kończą się
innymi kodami wyjścia (1, 2 albo 3) i wciąż są normalnie restartowane
przez systemd.

## Dwa tryby pracy

### Tryb "cron" (domyślny)

Jedno przejście i wyjście - klasyczne użycie z crona.

> **Oferty IKEA są sprawdzane co 15 minut.**

```
crontab -e
```

```
*/15 * * * * /usr/bin/flock -n /home/TWOJ_UZYTKOWNIK/.ikea_okazje.lock /usr/bin/python3 ~/ikea_okazje.py >> ~/ikea_okazje.log 2>&1
```

Podmień `TWOJ_UZYTKOWNIK` na swojego użytkownika - to musi być **ta sama
ścieżka pliku blokady**, jaka jest użyta w pliku usługi systemd (patrz
niżej), jeśli kiedykolwiek przełączysz się między crona a systemd.

Prostsze w konfiguracji, ale reakcja na komendy Telegrama i wykrycie
nowej oferty ograniczone są do interwału crona - komenda albo nowa oferta
zostaną obsłużone dopiero przy następnym przebiegu skryptu.

### Tryb "daemon" (systemd)

Działa cały czas w tle:
- **oferty IKEA** są sprawdzane co `CHECK_INTERVAL_SECONDS` (domyślnie **900 sekund = 15 minut**);
- **komendy Telegrama** są sprawdzane co `TELEGRAM_POLL_INTERVAL_SECONDS` (domyślnie **15 sekund**) - reakcja jest praktycznie natychmiastowa, niezależnie od tego, jak rzadko sprawdzane są oferty IKEA.

**Przed odpaleniem usługi ustaw w swoim prywatnym pliku `.env`:**

```
RUN_MODE=daemon
```

**To jest jedyne miejsce, w którym to ustawiasz.** Plik `.env`
(`~/.config/ikea-okazje.env`) jest jedynym źródłem konfiguracji w tej
architekturze - przykładowy plik usługi `ikea-okazje.service` w tym repo
celowo NIE zawiera `Environment=RUN_MODE=daemon`, bo kod nie daje
zmiennym środowiskowym systemd priorytetu nad `.env`, więc taki wpis nic
by nie zmienił. Jeśli zapomnisz ustawić `RUN_MODE=daemon` w `.env`,
skrypt uruchomiony przez systemd wykona jedno przejście typu "cron" i
wyjdzie ze statusem 0 - a systemd (przy `Type=simple`) może uznać to za
normalne zakończenie i nie zrestartować usługi. Skrypt wykrywa ten
przypadek (po zmiennej `INVOCATION_ID`, którą systemd ustawia dla swoich
usług) i zapisuje w logach ostrzeżenie - ale nie zmienia trybu
automatycznie, żeby nie maskować błędnej konfiguracji.

Przykładowy plik usługi używa `flock -n` z tym samym, trwałym plikiem
blokady co przykład crona wyżej - podmień `TWOJ_UZYTKOWNIK` na swojego
użytkownika (zarówno w ścieżkach, jak i w polu `User=`), a potem
zainstaluj usługę:

```
sudo cp ikea-okazje.service /etc/systemd/system/
sudo nano /etc/systemd/system/ikea-okazje.service   # podmień TWOJ_UZYTKOWNIK
sudo systemctl daemon-reload
sudo systemctl enable --now ikea-okazje
sudo systemctl status ikea-okazje
journalctl -u ikea-okazje -f
```

Ręczne uruchomienie skryptu (np. `python3 ikea_okazje.py` prosto z
terminala) podczas gdy usługa systemd już działa może zostać celowo
"pominięte" przez `flock -n`, jeśli ręcznie użyjesz tego samego pliku
blokady - to zamierzone zachowanie (patrz "Ochrona przed równoległymi
procesami" wyżej), a nie błąd.

## Użycie

```
python3 ikea_okazje.py
```

Pierwsze uruchomienie nie wyśle powiadomienia, nawet jeśli od razu
znajdzie dopasowanie - zapisuje aktualny stan jako "już znany". Ustaw
`ALERT_EXISTING_ON_FIRST_RUN=true`, jeśli chcesz alert od razu.

Każde powiadomienie zawiera cenę, procent rabatu, stan produktu, numer
artykułu, numer oferty oraz bezpośredni link do rezerwacji w "Okazje na
Okrągło" (jeśli dostępny). Logi mają znacznik czasu.

## Testy

Repozytorium zawiera podstawowy zestaw testów jednostkowych w
`tests/test_ikea_okazje.py` (standardowy `unittest`, bez dodatkowych
zależności i bez połączenia z IKEA czy Telegramem). Uruchom je z:

```
python3 -m unittest tests/test_ikea_okazje.py
```

Testy sprawdzają m.in. mapowanie `storeId -> slug` w `KNOWN_STORES`,
poprawność generowania linków rezerwacji (w tym kodowanie polskich
znaków i pozostawienie `+` bez zmian), dodawanie/usuwanie sklepów
komendami Telegrama (bez duplikatów, odrzucanie nieznanych ID), walidację
`SMTP_MODE` (w tym `disabled`), walidację KOMPLETNOŚCI konfiguracji
kanałów powiadomień (Telegram-only, odrzucanie niepełnego Telegrama,
odrzucanie niekompletnego e-maila dla `gmail`/`local587`/`exim`, błąd
przy braku jakiegokolwiek kanału), pomijanie wysyłki e-mail gdy
`SMTP_MODE=disabled`, ostrzeżenie o uruchomieniu pod systemd bez
`RUN_MODE=daemon`, escapowanie HTML w odpowiedziach Telegrama,
normalizację numerów artykułu oraz odporność cyklu sprawdzania ofert na
błąd pojedynczego sklepu.

## Aktualizacja skryptu

```
git pull
```

Twoje ustawienia w `.env` i dynamiczna lista w `~/.ikea_okazje_dynamic.json`
zostają nietknięte.

## Typowe problemy

**Usługa systemd wychodzi natychmiast ze statusem 0 / nie zostaje
uruchomiona na dłużej.** Najpierw sprawdź, czy `RUN_MODE=daemon` jest
ustawiony w Twoim prywatnym pliku `.env`
(`~/.config/ikea-okazje.env`) - bez tego skrypt wykonuje tylko jedno
przejście typu "cron" i wychodzi, co systemd (przy `Type=simple`) może
zinterpretować jako normalne zakończenie. Skrypt zapisze w logach
(`journalctl -u ikea-okazje`) ostrzeżenie o tej sytuacji.

**Odpowiedzi na Telegramie są zduplikowane.** Sprawdź, czy nie działają
jednocześnie cron, usługa systemd i/albo ręcznie odpalony proces
skryptu - każdy z nich niezależnie odpytuje Telegrama i wysyła
odpowiedzi. Wybierz jeden mechanizm trwałego uruchamiania (cron albo
systemd, patrz "Ochrona przed równoległymi procesami") i upewnij się, że
wszystkie sposoby odpalania używają tego samego pliku blokady `flock`.

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

**Link do rezerwacji nie działa / brak linku.** Sprawdź, czy `storeId`
Twojego sklepu jest w wbudowanej mapie `KNOWN_STORES` (patrz tabela
wyżej) albo dodaj go ręcznie w `STORE_URL_SLUGS`. Slug sklepu znajdziesz
w adresie URL strony "Okazje na Okrągło" po wybraniu sklepu (fragment
`#/nazwa-sklepu/...`). Jeśli `offerNumber` nie jest zwracany przez API
dla danej oferty, skrypt celowo nie generuje żadnego linku.

## Licencja

Apache License 2.0 - copyright Paweł Stecki. Zobacz plik [LICENSE](LICENSE)
w tym repo po pełny tekst.

Możesz swobodnie kopiować, modyfikować i redystrybuować ten kod, w tym
komercyjnie. Jedyne wymogi: zachowaj oryginalną notatkę o prawach
autorskich (plik LICENSE i, jeśli redystrybuujesz, plik NOTICE) oraz
jasno oznacz, które pliki zmodyfikowałeś, jeśli publikujesz fork.
