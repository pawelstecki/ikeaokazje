# ikeaokazje

Skrypt sprawdzający dział "Okazje na okrągło" (second-hand) w wybranych
sklepach IKEA i wysyłający powiadomienie (e-mail i/albo Telegram), gdy
pojawi się szukany produkt. Przez Telegrama można też zarządzać listą
szukanych słów/numerów i monitorowanych sklepów bez edycji plików.

Strona "Okazje na okrągło" to SPA - skrypt odpytuje bezpośrednio prywatne
API IKEA (`web-api.ikea.com/circular/circular-asis/...`), które nie jest
publicznie dokumentowane i może zniknąć albo się zmienić bez ostrzeżenia.
To prywatne narzędzie do własnego użytku, nie scraper komercyjny - ma
tylko zautomatyzować sporadyczne sprawdzanie strony, które i tak
robiłbyś ręcznie.

Endpoint jest chroniony fingerprintem TLS (Cloudflare/Akamai), nie tylko
nagłówkami, więc skrypt używa `curl_cffi` z `impersonate=` zgodnym z
wysyłanym `User-Agent`/`sec-ch-ua` (patrz `CLIENT_PROFILES` w kodzie).

## Instalacja

```
python3 -m pip install -r requirements.txt
```

Jedyna zależność to `curl_cffi`, minimalna wymagana wersja `0.7.0`
(potrzebna do impersonacji Chrome 124 - nie schodź poniżej tej wersji).
Python 3.8+.

## Konfiguracja

```
mkdir -p ~/.config
cp .env.example ~/.config/ikea-okazje.env
chmod 600 ~/.config/ikea-okazje.env
nano ~/.config/ikea-okazje.env
```

`ikea_okazje.py` możesz spokojnie aktualizować z GitHuba (`git pull`) -
ten plik `.env` żyje poza repo i nigdy nie jest nadpisywany.

Najważniejsze pola (pełny opis i przykłady w `.env.example`):

| Pole | Opis | Domyślnie |
|---|---|---|
| `SMTP_MODE` | `gmail`, `local587`, `exim` albo `disabled` | `gmail` |
| `SMTP_USER`, `SMTP_PASS` | login/haslo SMTP - wymagane dla `gmail`/`local587`, nieużywane dla `exim` (brak autentykacji) | - |
| `EMAIL_TO` | odbiorca - wymagany, gdy e-mail jest włączony. Jeśli nie podasz `EMAIL_TO`, w trybach `gmail`/`local587` skrypt użyje `SMTP_USER`; w trybie `exim` `EMAIL_TO` trzeba podać jawnie | - |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | drugi kanał powiadomień + komendy - oba naraz albo żaden | wyłączone |
| `STORE_IDS` | numery sklepów IKEA, po przecinku - tylko na pierwszym starcie | `294` (Wrocław) |
| `SEARCH_TERMS`, `SEARCH_ARTICLE_NUMBERS` | szukane frazy / numery artykułu - tylko na pierwszym starcie | `Stall` / brak |
| `MIN_DISCOUNT_PERCENT`, `MAX_PRICE`, `KEYWORDS_EXCLUDE` | filtry, domyślnie wyłączone; `MIN_DISCOUNT_PERCENT`/`MAX_PRICE` muszą być liczbą >= 0 | brak |
| `RUN_MODE` | `cron` albo `daemon` | `cron` |
| `CHECK_INTERVAL_SECONDS` | tylko `daemon` - jak często sprawdzać oferty; minimum `300` (mniejsza wartość jest podnoszona do 300 z ostrzeżeniem w logu) | `2700` (45 min) |
| `TELEGRAM_LONG_POLL_SECONDS` | tylko `daemon` - ile sekund jedno zapytanie do Telegrama czeka na nowe komendy (long polling); liczba całkowita `0`-`50`, `0` wyłącza long polling | `25` |
| `TELEGRAM_POLL_INTERVAL_SECONDS` | tylko `daemon` - jak często sprawdzać komendy przy `TELEGRAM_LONG_POLL_SECONDS=0`; przy long pollingu to przerwa po błędzie Telegrama | `15` |
| `HEALTH_ALERT_AFTER_CYCLES` | po ilu kolejnych nieudanych cyklach wysłać alert o awarii monitora; `0` wyłącza alert (patrz "Alert o awarii monitora") | `3` |

Nieprawidłowa wartość pola liczbowego albo `RUN_MODE` (literówka, tekst
zamiast liczby, liczba ujemna, zero dla interwałów) kończy start skryptu
błędem z nazwą pola i ścieżką pliku, zamiast cicho wyłączyć filtr albo
wrócić do wartości domyślnej.

Format pliku: `KLUCZ=wartość`, opcjonalnie z przedrostkiem `export `.
Komentarz w linii zaczyna się od `#` poprzedzonego spacją
(`MAX_PRICE=300 # zł` daje `300`, a `abc#def` zostaje bez zmian).
Wartość zawierającą ` #` albo spacje na początku/końcu (np. hasło) wpisz
w cudzysłowie (`SMTP_PASS='ab #cd'`) - treść w cudzysłowie jest brana
dosłownie, a niezamknięty cudzysłów kończy start błędem z numerem linii.

Wymagany jest **przynajmniej jeden w pełni skonfigurowany kanał
powiadomień** - e-mail albo Telegram, może być tylko jeden z nich. Brak
kompletnej konfiguracji obu kończy start skryptu czytelnym błędem z listą
brakujących pól. Szczegóły trybów SMTP i wymogi Telegrama (oba pola albo
żadne) - patrz komentarze w `.env.example`.

**`SEARCH_TERMS`/`SEARCH_ARTICLE_NUMBERS`/`STORE_IDS` działają tylko przy
pierwszym uruchomieniu** - zasiewają plik `~/.ikea_okazje_dynamic.json`,
który od tej pory jest źródłem prawdy. Dalej zarządzasz listami komendami
Telegrama - to bezpieczniejsza opcja niż ręczna edycja pliku. Jeśli
edytujesz go wprost, zrób najpierw kopię. Usunięcie tego pliku resetuje
nie tylko sklepy/słowa/numery do wartości z `.env`, ale też stan backoffu,
stan alertu o utracie/odzyskaniu dostępu i alertu o awarii monitora
(patrz niżej) - traktuj to
jako pełny reset, nie tylko zmianę listy wyszukiwania.

Skrypt ma wbudowaną mapę wszystkich sklepów IKEA w Polsce (`KNOWN_STORES`
w kodzie, dostępna w Telegramie pod `/sklepy`) - do `STORE_IDS` wystarczy
sam numer, link do rezerwacji dobierze się automatycznie. `STORE_URL_SLUGS`
w `.env` służy tylko do nadpisania/rozszerzenia tej mapy (nowy sklep albo
zmiana routingu przez IKEA).

### Jak założyć bota Telegrama

1. W Telegramie napisz do `@BotFather`, wyślij `/newbot`.
2. Podaj nazwę i login kończący się na `bot` - dostaniesz token
   (`TELEGRAM_BOT_TOKEN`).
3. Napisz cokolwiek do swojego bota (musisz zacząć rozmowę pierwszy).
4. Wejdź na `https://api.telegram.org/bot<TOKEN>/getUpdates` i znajdź
   `"chat":{"id": ...}` - to `TELEGRAM_CHAT_ID`. Puste `result: []`
   znaczy, że jeszcze nie napisałeś do bota.

## Uruchamianie: cron albo systemd (nigdy oba naraz)

Nie uruchamiaj cron i systemd równocześnie dla tego samego skryptu - dwa
niezależne procesy mogą ze sobą kolidować (np. podwójne powiadomienia
albo podwójne odpowiedzi na te same komendy Telegrama). Wybierz jeden
mechanizm.

Niezależnie od wyboru, **każdy sposób odpalania (cron, systemd, ręczne
uruchomienie) musi używać tej samej ścieżki skryptu i tego samego pliku
blokady** przez `flock -n`. `-n` oznacza, że `flock` nie czeka - jeśli
blokada jest zajęta, ta kopia procesu po prostu wychodzi, więc cykle
nigdy się nie kolejkują i dwie kopie nigdy nie działają naraz.

Przykłady poniżej zakładają repozytorium sklonowane do
`/home/TWOJ_UZYTKOWNIK/ikeaokazje` i plik blokady
`/home/TWOJ_UZYTKOWNIK/.ikea_okazje.lock` - podmień obie ścieżki na
swoje rzeczywiste (skrypt nie musi leżeć bezpośrednio w katalogu
domowym), ale użyj **tych samych** we wszystkich trzech miejscach
(cron, systemd, ręczne uruchomienie).

### cron (RUN_MODE=cron, domyślny)

Jedno przejście i wyjście, sprawdzanie ofert co interwał crona (poniżej:
15 minut). Wyjątek: po blokadzie 403/429 odpalenia przed zapisanym
terminem backoffu kończą się od razu (kod 0, wpis w logu, bez zapytania
do IKEA) - patrz "Zachowanie przy HTTP 403/429":

```
crontab -e
```

```
*/15 * * * * /usr/bin/flock -n /home/TWOJ_UZYTKOWNIK/.ikea_okazje.lock /usr/bin/python3 /home/TWOJ_UZYTKOWNIK/ikeaokazje/ikea_okazje.py >> /home/TWOJ_UZYTKOWNIK/ikea_okazje.log 2>&1
```

Prostsze, ale reakcja na komendy Telegrama i nowe oferty ograniczona do
interwału crona.

### systemd (RUN_MODE=daemon)

Działa w tle: oferty sprawdzane co `CHECK_INTERVAL_SECONDS` (domyślnie
45 min), komendy Telegrama przez long polling - zapytanie czeka do
`TELEGRAM_LONG_POLL_SECONDS` (domyślnie 25 s) na nową wiadomość, więc bot
odpowiada od razu, a zapytań do Telegrama jest ok. 3500 na dobę zamiast
5760. Long polling nie opóźnia sprawdzania ofert o więcej niż ok. 1 s.
`TELEGRAM_LONG_POLL_SECONDS=0` przywraca sprawdzanie komend co
`TELEGRAM_POLL_INTERVAL_SECONDS` (domyślnie 15 s). Tryb `cron` zawsze
sprawdza komendy jednym krótkim zapytaniem.

W `.env` (**nie** w pliku usługi - `Environment=` tam nie ma
pierwszeństwa nad `.env`, więc niczego by nie zmieniło):

```
RUN_MODE=daemon
```

Jeśli zapomnisz o tym wpisie, skrypt pod systemd wykona jedno przejście
"cron" i wyjdzie ze statusem 0 - systemd może to uznać za normalne
zakończenie i nie zrestartować usługi (dostaniesz ostrzeżenie w logach).

Instalacja (w pliku usługi podmień `TWOJ_UZYTKOWNIK` w `User=` i
`WorkingDirectory=`, a w `ExecStart=` ścieżkę do skryptu i pliku blokady
na te, które faktycznie u siebie używasz - domyślnie w pliku jest to
`/home/TWOJ_UZYTKOWNIK/ikea_okazje.py`, czyli skrypt bezpośrednio w
katalogu domowym; jeśli masz repo sklonowane gdzie indziej, popraw tę
ścieżkę tak samo, jak w przykładach crona/ręcznego uruchomienia wyżej):

```
sudo cp ikea-okazje.service /etc/systemd/system/
sudo nano /etc/systemd/system/ikea-okazje.service
sudo systemctl daemon-reload
sudo systemctl enable --now ikea-okazje
journalctl -u ikea-okazje -f
```

`ikea-okazje.service` używa `flock -n -E 75 ...` z tym samym plikiem
blokady co cron. `-E 75` daje dedykowany kod wyjścia dla "blokada zajęta",
a `RestartPreventExitStatus=75` mówi systemd, żeby w tym przypadku NIE
restartował usługi (to zamierzony, "cudzy" cykl, nie awaria). Prawdziwe
błędy skryptu (kody 1-3) są restartowane normalnie.

`systemctl stop`/`restart` wysyła `SIGTERM` - daemon kończy bieżący cykl,
loguje podsumowanie i wychodzi czysto (bez naruszania plików stanu).
Jeśli `SIGTERM` trafi w trwające zapytanie long pollingu, daemon czeka na
jego koniec: zatrzymanie może trwać do `TELEGRAM_LONG_POLL_SECONDS`
(domyślnie 25 s; przy maksimum 50 s i wolnej sieci do ok. 65 s). To mieści
się w domyślnym `TimeoutStopSec=90s` systemd - jeśli ustawiasz w usłudze
krótszy `TimeoutStopSec`, zmniejsz też `TELEGRAM_LONG_POLL_SECONDS`.

### Ręczne uruchomienie

Samo `python3 ikea_okazje.py` nie przechodzi przez `flock` i może
kolidować z cronem/systemd. Użyj tej samej blokady:

```
/usr/bin/flock -n /home/TWOJ_UZYTKOWNIK/.ikea_okazje.lock \
  /usr/bin/python3 /home/TWOJ_UZYTKOWNIK/ikeaokazje/ikea_okazje.py
```

## Wyszukiwanie i powiadomienia

Pierwsze uruchomienie nie wysyła powiadomienia - zapisuje aktualny stan
jako "już znany". Ustaw `ALERT_EXISTING_ON_FIRST_RUN=true`, jeśli chcesz
alert od razu.

Każde powiadomienie o ofercie zawiera cenę, rabat, stan produktu, numer
artykułu, numer oferty i link do rezerwacji (jeśli oferta ma numer
i skrypt zna slug sklepu - w przeciwnym razie zamiast linku dostajesz
numer oferty do ręcznego wyszukania).

W Telegramie podgląd linku pod wiadomością pokazuje zdjęcie produktu
(pierwszego produktu w danej wiadomości), jeśli API IKEA podaje jego
adres `https://`; bez zdjęcia Telegram pokazuje zwykły podgląd pierwszego
linku. Odpowiedzi na komendy i komunikaty o stanie monitora (dostęp,
awaria) idą bez podglądu.

Gdy IKEA wystawia kilka egzemplarzy tego samego produktu (identyczna
nazwa, sklep, cena, stan), powiadomienie zawiera jeden wspólny opis
i listę osobnych linków do rezerwacji każdego egzemplarza. Jeśli łączna
treść powiadomienia Telegrama przekracza limit API, alert jest
automatycznie dzielony na wiele wiadomości — żaden egzemplarz nie zostaje
pominięty. Kolejne wiadomości takiej serii idą co 1 s (limit Telegrama).
Gdy Telegram mimo to odpowie HTTP 429 (za dużo wiadomości), skrypt czeka
wskazany przez niego czas (najwyżej 60 s) i ponawia wiadomość raz.
Wiadomości e-mail używają analogicznego grupowania.

### Komendy Telegrama

```
/dodaj <słowo>       - dodaj słowo kluczowe
/usun <słowo>        - usuń słowo kluczowe
/numer <nr>          - dodaj numer artykułu (kropki/spacje są ignorowane)
/usunnumer <nr>      - usuń numer artykułu
/sklepy              - pokaż aktywne i dostępne sklepy
/dodajsklep <ID>     - dodaj sklep do monitoringu (tylko znane ID)
/usunsklep <ID>      - usuń sklep z monitoringu
/status              - sklepy/słowa/numery/filtry i stan sprawdzania
/oferty              - dopasowania z ostatniego sprawdzenia
/pomoc               - lista komend
```

`/status` poza sklepami, słowami i filtrami pokazuje czas ostatniego
udanego sprawdzenia, liczbę dopasowań z ostatniego sprawdzenia, aktywny
backoff po 403/429 (do kiedy i po jakim kodzie HTTP) oraz aktywny alert
o awarii monitora razem z ostatnim błędem.

`/oferty` pokazuje wszystkie dopasowania zapisane przy ostatnim
sprawdzeniu ofert (także te, o których powiadomienie już przyszło),
pogrupowane jak w powiadomieniach: nazwa, sklep, cena i rabat, liczba
sztuk i linki „Rezerwuj” (albo numer oferty, gdy linku nie ma). Lista
odpowiada słowom kluczowym i numerom z chwili tamtego sprawdzenia - po
`/dodaj` albo `/usun` zmieni się dopiero po kolejnym. Jeśli któryś sklep
wtedy nie odpowiedział, odpowiedź o tym uprzedza. Wszystko mieści się
w jednej wiadomości: produkty, które się nie zmieszczą, są tylko
policzone („... i jeszcze N produktow”), a przy produkcie z wieloma
sztukami widać najwyżej 10 linków.

Bot reaguje tylko na wiadomości z `TELEGRAM_CHAT_ID` z `.env` - inni
nadawcy są ignorowani. Komenda z nazwą bota (np. `/status@MojBot`, tak
wysyła ją Telegram w grupach) działa tak samo jak `/status`. Edycja
wysłanej wcześniej komendy nie wykonuje jej ponownie - wyślij ją jako
nową wiadomość.

## Stan na dysku

- `~/.ikea_okazje_dynamic.json` - aktywne sklepy, słowa i numery
  artykułów (źródło prawdy po pierwszym starcie, patrz wyżej), plus stan
  backoffu, alertu dostępu i alertu o awarii monitora (opisane niżej).
- `~/.ikea_okazje_seen_offers.json` - identyfikatory już znanych ofert,
  aby nie zgłaszać ich ponownie. Nowe oferty są dopisywane do tego pliku
  wyłącznie wtedy, gdy wszystkie włączone kanały (e-mail, cała seria
  wiadomości Telegrama) zakończą się pełnym sukcesem. W razie awarii
  któregokolwiek kanału oferty nie są oznaczane jako znane i zostaną
  ponowione w kolejnym cyklu (kanał, który wcześniej odebrał wiadomość,
  może otrzymać duplikat; brak gwarancji exactly-once).
- `~/.ikea_okazje_telegram_offset.json` - offset ostatnio przetworzonej
  wiadomości Telegrama.
- `~/.ikea_okazje_last_matches.json` - dopasowania z ostatniego
  sprawdzenia (dla `/oferty` i `/status`) i lista sklepów, których wtedy
  nie udało się pobrać. Nadpisywany po każdym sprawdzeniu, w którym
  udało się pobrać co najmniej jeden sklep. Można go bezpiecznie usunąć;
  błąd zapisu trafia tylko do logu i nie wpływa na powiadomienia ani na
  alert o awarii monitora.

Zmiana `STORE_IDS`/`SEARCH_TERMS`/`SEARCH_ARTICLE_NUMBERS` w `.env` **nie
ma efektu**, jeśli `~/.ikea_okazje_dynamic.json` już istnieje - edytuj
listy przez Telegrama (bezpieczniej) albo zrób kopię i edytuj plik wprost.
Usunięcie pliku zasieje go na nowo z `.env`, ale zresetuje też backoff i
alert dostępu - patrz "Konfiguracja" wyżej.

## Zachowanie przy HTTP 403/429

Wszystkie sklepy są odpytywane przez **jeden, wspólny endpoint** API
IKEA - blokada (403, Akamai) albo rate limit (429) dotyczy więc całego
procesu, nie pojedynczego sklepu, i backoff jest liczony **globalnie**,
nie per sklep. Backoff obowiązuje w obu trybach: `daemon` planuje według
niego następny cykl, a w trybie `cron` każde odpalenie przed zapisanym
terminem obsługuje tylko komendy Telegrama, loguje, do kiedy trwa
backoff, i kończy się kodem 0 bez zapytania do IKEA. Przy aktualnych
domyślnych ustawieniach lokalny odstęp między
próbami po blokadzie wynosi co najmniej 45 minut (`CHECK_INTERVAL_SECONDS`)
i **nie wydłuża się** po kolejnych blokadach z rzędu - maksymalny odstęp
(cap) wypada na tę samą wartość co pierwszy krok backoffu. Jednostronny
jitter (tylko w górę) i nagłówek `Retry-After` z serwera (jeśli poprawny)
mogą ten czas dodatkowo wydłużyć - liczy się większa z tych wartości.
Licznik backoffu resetuje się po każdym cyklu bez błędu 403/429, nawet
jeśli w tym cyklu wystąpił inny błąd pobierania (np. timeout czy HTTP
5xx na jednym ze sklepów) - liczy się tylko brak blokady/rate limitu.
Powiadomienie o odzyskaniu dostępu (patrz niżej) to inna zasada i wciąż
wymaga w pełni udanego cyklu, bez żadnych błędów. Wyliczony termin
kolejnej próby jest zapisywany na dysku i przetrwa restart procesu albo
usługi - po restarcie skrypt czeka do tego samego terminu, a nie zaczyna
od nowa. Uszkodzona wartość w kluczu `"blocking_backoff"` pliku
`~/.ikea_okazje_dynamic.json` (np. tekst albo `NaN` zamiast liczby) jest
traktowana jak brak wartości - skrypt działa dalej, zamiast kończyć się
błędem.

Osobno od backoffu, skrypt wysyła powiadomienie o **utracie dostępu**
dopiero po dwóch kolejnych, w pełni nieudanych cyklach z rzędu (wszystkie
sklepy, wyłącznie 403/429) - pojedyncza, przejściowa blokada nic nie
wysyła. Jeśli po pierwszej takiej porażce kolejny cykl się powiedzie,
żadne powiadomienie nie idzie. Jeśli alert o utracie już wyszedł, przy
pierwszym późniejszym w pełni udanym cyklu skrypt próbuje wysłać jedno
powiadomienie o odzyskaniu dostępu.

## Alert o awarii monitora

Niezależnie od alertu o 403/429 skrypt pilnuje, czy w ogóle działa
poprawnie. Cykl jest **nieudany**, gdy którykolwiek sklep zwróci błąd
inny niż pełna blokada 403/429 (np. HTTP 404 albo 5xx, timeout, zmieniony
format odpowiedzi API - także gdy pozostałe sklepy odpowiedziały), gdy
nie da się odczytać albo zapisać pliku `~/.ikea_okazje_seen_offers.json`
albo gdy w cyklu wystąpi nieoczekiwany wyjątek (daemon go loguje
i działa dalej). Cykl, w którym wszystkie sklepy dostały wyłącznie
403/429, nie liczy się ani jako udany, ani jako nieudany - ma własny
alert opisany wyżej. Błąd wysyłki powiadomienia o ofercie nie psuje
cyklu (taki alert i tak by nie doszedł; błąd jest w logu).

Po `HEALTH_ALERT_AFTER_CYCLES` (domyślnie 3) nieudanych cyklach z rzędu
skrypt wysyła **jeden** alert "IKEA Okazje: monitor nie dziala
poprawnie" tymi samymi kanałami co oferty: liczbę nieudanych cykli, czas
ostatniego udanego sprawdzenia, ostatni błąd i podpowiedź, gdzie szukać
logów. Kolejne nieudane cykle nic nie wysyłają. Pierwszy w pełni udany
cykl wysyła jedno powiadomienie, że monitor znów działa. Jeśli wysyłka
zawiedzie na wszystkich kanałach, skrypt ponowi ją w następnym takim
cyklu. `HEALTH_ALERT_AFTER_CYCLES=0` wyłącza te powiadomienia (licznik
i ostatni błąd są dalej zapisywane). Stan trzyma klucz `"health"`
w `~/.ikea_okazje_dynamic.json`, więc restart nie zeruje licznika ani nie
powtarza alertu.

## Diagnozowanie problemów

**Dostałeś alert "monitor nie dziala poprawnie".** Zobacz ostatni błąd
w treści alertu i pełne logi: `journalctl -u ikea-okazje` (systemd) albo
plik logu z crona (np. `~/ikea_okazje.log`). Najczęstsze przyczyny są
opisane niżej. Ostatni błąd i licznik są też w kluczu `"health"`
w `~/.ikea_okazje_dynamic.json`.

**`Nieoczekiwany format odpowiedzi API`.** IKEA zmieniła format
odpowiedzi (np. nazwy pól) - skrypt celowo traktuje to jako błąd, zamiast
po cichu nie znajdować żadnych ofert. Surowa odpowiedź pierwszej strony
jest w `~/.ikea_okazje_last_raw.json`; potrzebna jest aktualizacja
skryptu.

**`Blad odczytu pliku stanu`.** Plik `~/.ikea_okazje_seen_offers.json`
jest uszkodzony. Popraw go albo usuń - po usunięciu następny cykl
zapisze aktualne oferty jako znane bez wysyłania powiadomień (jak przy
pierwszym uruchomieniu, chyba że `ALERT_EXISTING_ON_FIRST_RUN=true`).

**`osiagnieto limit MAX_PAGES`.** Sklep ma więcej stron ofert niż limit
`MAX_PAGES` w kodzie - wyniki z dalszych stron są pomijane.


**Usługa systemd wychodzi natychmiast ze statusem 0.** Sprawdź
`RUN_MODE=daemon` w `~/.config/ikea-okazje.env` - patrz wyżej.

**Zdublowane odpowiedzi na Telegramie.** Cron, systemd i/albo ręczne
uruchomienie działają równocześnie - wybierz jeden mechanizm i sprawdź,
że wszystkie używają tego samego pliku blokady `flock`.

**403/429.** Sprawdź zgodność wersji Chrome między `impersonate` a
`User-Agent`/`sec-ch-ua` w każdym profilu `CLIENT_PROFILES`. Poza tym to
zamierzone zachowanie (patrz "Zachowanie przy HTTP 403/429" wyżej) -
skrypt nie próbuje tego obchodzić, tylko odczekuje backoff/`Retry-After`.

**`Size must be less than or equal to 64`.** `PAGE_SIZE` już jest `64` -
to błąd API niezależny od tego ustawienia.

**Telegram nie wysyła / brak `chat_id`.** Napisz najpierw do bota -
Telegram wymaga, żeby rozmowę zaczynał człowiek. Błąd w logu zawiera kod
HTTP i opis od Telegrama (np. `HTTP 400: Bad Request: chat not found`),
bez tokenu bota.

**`Telegram API (getUpdates) zwrocilo HTTP 409`.** Telegram oddaje
komendy tylko jednemu odbiorcy. Zwykle działa druga kopia skryptu z tym
samym botem (cron i systemd naraz, ręczne uruchomienie bez `flock`, inny
serwer) albo bot ma ustawiony webhook - usuń go, otwierając
`https://api.telegram.org/bot<TOKEN>/deleteWebhook`.

**Komendy Telegrama nie działają.** Sprawdź, czy piszesz z konta o
`chat_id` z `.env`. W trybie `cron` komenda zadziała przy następnym
przebiegu skryptu.

**Brak linku do rezerwacji.** Sklep nie jest w `KNOWN_STORES` - dodaj go
w `STORE_URL_SLUGS` albo sprawdź `/sklepy` w Telegramie.

## Testy

```
python3 -m unittest tests/test_ikea_okazje.py
```

Standardowy `unittest`, bez sieci, bez `.env` użytkownika (testy izolują
`HOME` we własnym tymczasowym katalogu).

## Stan dostawy per kanał

Plik `~/.ikea_okazje_seen_offers.json` przechowuje teraz dwa klucze: `seen`
(UUID-y w pełni dostarczone przez wszystkie aktywne kanały) i `pending` (UUID-y
częściowo dostarczone — słownik `{uuid: [kanały, które już dostarczyły]}`).
Dzięki temu gdy jeden kanał (np. SMTP z wygasłym certyfikatem) pada trwale,
sprawny kanał (np. Telegram) dostarcza ofertę tylko raz — przy kolejnym cyklu
nie dostaje duplikatu. UUID trafia do `seen` dopiero gdy wszystkie aktualnie
włączone kanały go dostarczą. Wyłączenie kanału jest od razu respektowane —
oferty w `pending` nie czekają wiecznie. Stare pliki bez klucza `pending`
działają bez modyfikacji (migracja jest transparentna).

## Nieoficjalne API i licencja

API IKEA użyte tutaj nie jest publicznie dokumentowane i może się zmienić
albo zniknąć bez ostrzeżenia - patrz uwaga na początku.

Apache License 2.0, © Paweł Stecki - pełny tekst w [LICENSE](LICENSE).
Możesz kopiować, modyfikować i redystrybuować (także komercyjnie);
zachowaj notatkę o prawach autorskich (LICENSE, przy redystrybucji też
NOTICE) i oznacz zmienione pliki przy publikowaniu forka.
