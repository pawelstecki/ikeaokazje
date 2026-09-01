# ikeaokazje

Skrypt, ktory sprawdza dzial "Okazje na okraglo" (second-hand) w wybranych
sklepach IKEA i wysyla powiadomienie (e-mail i/albo Telegram), jak pojawi
sie produkt, na ktory czekasz.

Zrobilem go, bo chcialem regal STALL, ktory rzadko trafia do tego dzialu,
a reczne odswiezanie strony kilka razy dziennie mi sie po tygodniu
znudzilo. Strona "Okazje na okraglo" to SPA, wiec caly ruch idzie do
prywatnego API IKEA - `web-api.ikea.com/circular/circular-asis/...` -
znalezionego w devtoolsach (zakladka Network) po otwarciu strony.

## Zanim zaczniesz - ważna uwaga

To API nie jest publicznie dokumentowane. Znalazlem je, bo strona z
niego korzysta, ale IKEA moze je zmienic albo zablokowac w kazdej chwili,
bez ostrzezenia. Ten skrypt dziala u mnie, ale nie oczekuj wsparcia od
IKEA, jesli cos przestanie funkcjonowac po ich update.

Druga rzecz: sam endpoint jest chroniony przez Cloudflare na poziomie
fingerprintu TLS, nie tylko naglowkow HTTP. Dlatego uzywam `curl_cffi`
(a nie zwyklego `requests`) - podszywa sie pod prawdziwa przegladarke na
poziomie handshake'u, bez czego dostajesz czysty 403 zanim serwer nawet
zobaczy Twoje naglowki. `HEADERS` i `IMPERSONATE` w skrypcie musza sie
wzajemnie zgadzac (ta sama wersja Chrome) - nie polecam edytowac ich
osobno, latwo znowu wpasc w blokade.

## Instalacja

```
pip install curl_cffi
```

Dziala tez na starszych Pythonach (testowane od 3.9 wzwyz). Jesli masz
Pythona 3.6 i pip próbuje sciagnac ancient, wycofana wersje 0.1.5 z
błędem kompilacji `pyconfig.h` - to znak, że musisz zainstalowac nowszego
Pythona, `curl_cffi` w wersji obslugujacej impersonacje wymaga 3.8+.

## Konfiguracja

Skopiuj przykladowy plik z sekretami:

```
mkdir -p ~/.config
cp .env.example ~/.config/ikea-okazje.env
chmod 600 ~/.config/ikea-okazje.env
nano ~/.config/ikea-okazje.env
```

Wypelnij `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`. Jesli uzywasz Gmaila,
`SMTP_PASS` to haslo aplikacji (16 znakow), nie zwykle haslo do konta -
trzeba wlaczyc weryfikacje dwuetapowa i wygenerowac je w ustawieniach
konta Google. Telegram jest opcjonalny - patrz sekcja nizej.

W samym `ikea_okazje.py` na gorze pliku ustaw:

- `STORE_IDS` - lista numerow sklepow IKEA do monitorowania, np.
  `["294", "307"]`. Numer znajdziesz w devtoolsach po wybraniu sklepu na
  stronie "Okazje na okraglo", parametr `storeIds` w zapytaniu do API.
  Miedzy kolejnymi sklepami skrypt robi krotka, losowa przerwe.
- `SEARCH_TERMS` - lista fraz do wyszukania. Dopasowanie ignoruje
  wielkosc liter i akcenty/ogonki, wiec `"Stall"` zlapie tez `"STÄLL"`.
  Szuka jako podciag, wiec krotki rdzen zlapie odmiany slowa - np.
  `"poscie"` zlapie `posciel`, `pościeli`, `pościelowy` itd.
- `SEARCH_ARTICLE_NUMBERS` - jesli wolisz sledzic konkretny numer
  artykulu, a nie tekst.
- `SMTP_MODE` - `"gmail"`, `"local587"` (wlasny serwer, port 587 ze
  STARTTLS+login) albo `"exim"` (wlasny serwer, port 25, bez logowania -
  dziala tylko jesli Twoj MTA juz jest skonfigurowany jako relay).

### Filtry (domyslnie wylaczone)

Trzy niezalezne filtry, kazdy mozesz wlaczyc osobno - domyslnie sa
wylaczone (`None` / pusta lista), zeby nie odsiewaly nic, dopoki sam nie
zdecydujesz, ze ich potrzebujesz:

```python
MIN_DISCOUNT_PERCENT = None   # np. 30 - tylko oferty z rabatem >= 30%
MAX_PRICE = None               # np. 300 - tylko oferty do 300 (w walucie sklepu)
KEYWORDS_EXCLUDE = []          # np. ["front", "uchwyt", "noga", "sruba"]
```

`KEYWORDS_EXCLUDE` dziala jak czarna lista - jesli tytul/opis produktu
zawiera ktores z tych slow, oferta jest calkowicie ignorowana, nawet
jesli akurat dopasowuje sie do `SEARCH_TERMS`. Przydatne, jesli szukasz
np. calego regalu KALLAX, ale nie chcesz dostawac alertow o samych
polkach czy nozkach do niego sprzedawanych osobno.

### Telegram (opcjonalny drugi kanal)

E-mail dziala zawsze. Telegram jest dodatkowy - jesli go skonfigurujesz,
powiadomienia poleca oboma kanalami rownolegle (jeden nie blokuje
drugiego, jak jeden padnie).

Jak zalozyc bota (2 minuty, jesli masz juz konto Telegram):

1. W aplikacji Telegram wyszukaj `@BotFather` i zacznij z nim rozmowe.
2. Wyslij komende `/newbot`.
3. Podaj nazwe wyswietlana bota (np. "Ikea Okazje Monitor") i unikalny
   login konczacy sie na `bot` (np. `ikea_okazje_monitor_bot`).
4. BotFather odpowie tokenem w formacie `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`
   - to jest Twoj `TELEGRAM_BOT_TOKEN`.
5. Znajdz swojego nowego bota w wyszukiwarce Telegrama i wyslij mu
   jakakolwiek wiadomosc (np. "hej") - Telegram nie pozwala botom pisac
   pierwszym, wiec musisz zainicjowac rozmowe.
6. Zeby dostac swoj `TELEGRAM_CHAT_ID`, wejdz w przegladarce na:
   `https://api.telegram.org/bot<TWOJ_TOKEN>/getUpdates`
   (podmien `<TWOJ_TOKEN>` na token z kroku 4) i po wyslaniu wiadomosci
   do bota odswiez ta strone - w odpowiedzi JSON znajdziesz
   `"chat":{"id": 123456789, ...}` - to jest Twoj chat_id.

Wpisz oba do `~/.config/ikea-okazje.env`:

```
TELEGRAM_BOT_TOKEN='123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ'
TELEGRAM_CHAT_ID='123456789'
```

Jesli zostawisz te pola puste, skrypt po prostu pomija kanal Telegram i
wysyla tylko maila - nic sie nie wywali.

## Uzycie

```
python3 ikea_okazje.py
```

Pierwsze uruchomienie nie wysle powiadomienia, nawet jesli od razu
znajdzie dopasowanie - zapisuje aktualny stan jako "juz znany" i czeka
na kolejne, nowe oferty. To zabezpieczenie przed zalewem powiadomien po
pierwszym teście albo po przywroceniu serwera z backupu. Jesli akurat
chcesz alert od razu, ustaw `ALERT_EXISTING_ON_FIRST_RUN = True`.

Kolejne uruchomienia wysylaja powiadomienie tylko dla **nowych** ofert
(sledzone po `offerUuid`), wiec nie dostaniesz spamu o tym samym
egzemplarzu co kilka minut. Kazde powiadomienie zawiera cene, procent
rabatu wzgledem ceny wyjsciowej, stan produktu, numer artykulu i link do
wyszukania go na stronie IKEA.

Jesli zapytanie do API dostanie tymczasowy blad (429 albo 5xx), skrypt
probuje ponownie z rosnaca przerwa (2s, 4s, 8s) przed poddaniem sie -
to lapie wiekszosc chwilowych przeciazen bez Twojej interwencji.

## Cron

```
crontab -e
```

```
*/7 * * * * /usr/bin/flock -n ~/.ikea_okazje.lock /usr/bin/python3 ~/ikea_okazje.py >> ~/ikea_okazje.log 2>&1
```

`flock -n` blokuje rownolegle uruchomienia - jesli poprzedni przebieg
sie zawiesi (np. na SMTP), kolejny cykl po prostu poczeka na wolne, a nie
odpali druga kopie, ktora moglaby wyslac duplikat powiadomienia.

Jesli `curl_cffi` zainstalowales z `pip install --user`, a cron zglasza
`ModuleNotFoundError`, prawdopodobnie problem jest w zmiennej `HOME` w
środowisku crona. Dodaj na gorze crontaba:

```
HOME=/home/twoj_user
PYTHONPATH=/home/twoj_user/.local/lib/python3.9/site-packages
```

## Typowe problemy

**Blokada Cloudflare / 403.** Zwykle oznacza, ze naglowki nie sa
kompletne albo `IMPERSONATE` nie zgadza sie z wersja Chrome zadeklarowana
w `HEADERS` (user-agent, sec-ch-ua). Musza sie zgadzac.

**`Size must be less than or equal to 64`.** API ma sztywny limit
rozmiaru strony - `PAGE_SIZE` w skrypcie jest juz ustawiony na `64`.

**Certyfikat SSL wygasl / hostname mismatch przy wysylce maila przez
wlasny serwer.** To akurat nie ma nic wspolnego ze skryptem - sprawdz
`sudo certbot certificates` i upewnij sie, ze certyfikat, ktorego uzywa
Twoj MTA, jest aktualny i wystawiony na nazwe hosta, z ktora sie laczysz.

**Telegram nie wysyla / brak `chat_id`.** Najczestszy powod: nie
wyslales jeszcze zadnej wiadomosci do swojego bota. `getUpdates` zwraca
pusta liste, dopoki bot nie dostanie od Ciebie choc jednej wiadomosci -
Telegram wymaga, zeby rozmowe zaczynal czlowiek, nie bot.

## Licencja

MIT - rob z tym co chcesz.
