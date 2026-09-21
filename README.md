# Sisäpiirikauppojen jäsennin (v0.2)

Jäsentää suomalaisten listayhtiöiden johdon liiketoimet -tiedotteet (MAR 19)
rakenteiseksi dataksi: kuka, missä asemassa, osti vai myi, milloin, kuinka
paljon ja mihin hintaan.

Tiedostot:
- parser.py: jäsennin, ei ulkoisia riippuvuuksia (Python 3.10+)
- test_parser.py: testit esimerkkitiedotteilla, mukana yksi oikean
  tuotantotiedotteen rakennetta jäljittelevä (fiktiivisin nimin) (pytest -q)
- nasdaq_api_collector.py: **suositeltu keräin**, käyttää suoraa HTTP/JSON-
  rajapintaa (ks. alla) — ei tarvitse selainta eikä Playwrightia

Putki:
1. Keräin hakee uudet tiedotteet ja tallentaa ne tauluun releases.
2. parse_release(teksti, title) palauttaa tilan:
   OK: rivit tauluun ja julkiseen näkymään
   NEEDS_REVIEW: rivit tauluun, odottavat käsin tarkistusta
   UNPARSED: näyttää sisäpiirikaupalta, mutta pohjaa ei tunnistettu:
     kielimallille (LLM_FALLBACK_PROMPT) tai käsin
   NOT_RELEVANT: ei johdon liiketoimi, ohitetaan
3. to_rows(tulos, release_id) muuntaa tuloksen tauluriveiksi.

Kun jäsennintä parannetaan, nosta PARSER_VERSIONia ja aja vanhat
tiedotteet uudelleen releases-taulusta.

## Asennus

    pip install requests beautifulsoup4 pytest

## Testien ajo

    pytest test_parser.py -v

## Datalähde: Nasdaqin oma JSON-rajapinta (vahvistettu toimivaksi)

Selaimen kehittäjätyökaluilla löytyi rajapinta, jota
nasdaq.com/european-market-activity/news/company-news itsekin kutsuu:

    GET https://api.news.eu.nasdaq.com/news/query.action
        ?countResults=true&globalGroup=exchangeNotice&displayLanguage=en
        &timeZone=CET&limit=100&start=0&dir=DESC&globalName=NordicAllMarkets
        &cnsCategory=Managers%27+Transactions&market=&fromDate=<epoch-ms>&toDate=

Vastaus on JSONP:tä ("handleResponse({...})") ja jokainen tiedote sisältää
mm. kentät disclosureId, headline, company, market, releaseTime, messageUrl.
messageUrl (esim. `view.news.eu.nasdaq.com/view?id=...`) on **tavallinen
staattinen HTML-sivu, ei JavaScript-renderöity**, ja sisältää MAR 19 -pohjan
suoraan luettavassa muodossa. Koko putki toimii siis pelkällä `requests`-
kirjastolla — selainta tai Playwrightia ei tarvita ollenkaan tuotannossa.

**First North Finland:** "Nasdaq Helsinki" -suodatin tarkoittaa rajapinnassa
nimenomaan `market="Main Market, Helsinki"` — First North ei sisälly siihen
automaattisesti (aiempi arvaukseni Nasdaq Helsinki Ltd:n juridisesta
rakenteesta oli väärä). Sen tarkkaa market-arvoa ei ole nähty, mutta sitä ei
myöskään tarvita: nasdaq_api_collector.py hakee KAIKKI Pohjoismaat kerralla
(`market=""`, `globalName=NordicAllMarkets`) ja suodattaa itse jokaisen
tiedotteen oman `market`-kentän perusteella niin, että se sisältää sanan
"Helsinki" — tämä nappaa sekä päälistan että First Northin oli Nasdaqin
sisäinen nimitys mikä tahansa.

**Tarkistamatta vielä:**
- fetch_release_text()in HTML-purku on paras arvaukseni; testaa ensimmäisellä
  oikealla ajolla ja säädä boilerplate-suodatusta tarvittaessa.
- "attachment"-kentän rakenne kun se ei ole tyhjä (nähdyt esimerkit olivat
  aina `attachment: []`).

**Ennen tuotantoajoa:** tarkista Nasdaqin käyttöehdot ja robots.txt.
Tiedotteiden sisältö on lakisääteisesti julkista, mutta rajapinnan
systemaattinen automaattinen haku voi silti olla ehtojen alaista.
ToS-turvallinen vaihtoehto: tilaa sähköposti-ilmoitukset
(subscribe.news.eu.nasdaq.com) ja jäsennä saapuvat viestit.

## Todellisesta tiedotteesta löytyneet korjaukset (v0.1 → v0.2)

Testattaessa jäsennintä oikeaa Tornator Oyj -tiedotetta vasten löytyi kolme
todellista muotovirhettä, jotka on nyt korjattu ja katettu testillä
(`test_bond_subscription_with_dot_numbering_and_two_pdmrs`):
1. Instrumenttityyppi-otsikko on suomeksi yksisanainen "Instrumenttityyppi",
   ei "Instrumentin tyyppi".
2. Liiketoimien numerointi voi olla "1." eikä vain "(1):" — molemmat
   hyväksytään nyt.
3. Nimi ja asema voivat olla samalla rivillä pilkulla erotettuna
   ("Nimi: Sukunimi, Etunimi, Asema: ..."), jolloin nimen perään jäi
   ylimääräinen pilkku — trimmataan nyt pois.
Lisäksi: lähipiiri-ilmoituksessa voi olla useampi kuin yksi johtohenkilö
(esim. eläkevakuutusyhtiö kahden hallitusjäsenen takana) — tietomalli tukee
vain yhtä `pdmr_name`-kenttää, joten tämä nyt vain merkitään NEEDS_REVIEW-
varoituksella sen sijaan että toinen nimi katoaisi hiljaa.
