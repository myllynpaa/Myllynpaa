"""
Kerää johdon liiketoimet -tiedotteet suoraan Nasdaqin omasta rajapinnasta.

VAHVISTETTU TOIMIVAKSI OIKEAA SIVUA VASTEN (löytyi selaimen Network-
välilehdeltä, ei arvaus):

1) Listaushaku — pelkkä JSON/JSONP, ei selainta tarvita:
   https://api.news.eu.nasdaq.com/news/query.action
     ?countResults=true&globalGroup=exchangeNotice&displayLanguage=en
     &timeZone=CET&dateMask=yyyy-MM-dd+HH:mm:ss&limit=100&start=0&dir=DESC
     &globalName=NordicAllMarkets&cnsCategory=Managers%27+Transactions
     &market=&fromDate=<epoch-ms>&toDate=

   Vastaus (JSONP-käärittynä, esim. "handleResponse({...})"):
   {
     "results": { "item": [
       {
         "disclosureId": 1463988,
         "headline": "Tornator Oyj: Johtohenkilöiden liiketoimet",
         "language": "fi", "languages": ["fi"],
         "cnsCategory": "Managers' Transactions",
         "messageUrl": "https://view.news.eu.nasdaq.com/view?id=...&lang=fi&src=listed",
         "releaseTime": "2026-09-17 17:00:00",
         "published": "2026-09-17 17:00:00",
         "market": "Main Market, Helsinki",
         "attachment": [],
         "company": "Tornator Oyj"
       }, ...
     ]},
     "count": 10000
   }

2) Tiedotteen koko teksti — tavallinen staattinen HTML, EI JavaScript-
   renderöity: messageUrl (esim. https://view.news.eu.nasdaq.com/view?id=...).
   Sivu sisältää MAR 19 -pohjan sellaisenaan (Nimi:, Asema:,
   Liikkeeseenlaskija:, jne.) suoraan luettavassa muodossa.

MARKET-SUODATUS: "Nasdaq Helsinki" -pudotusvalikko tarkoittaa rajapinnassa
nimenomaan market="Main Market, Helsinki" — First North Finlandin tarkkaa
arvoa ei ole nähty. Sen sijaan että arvattaisiin, tämä keräin hakee KAIKKI
Pohjoismaat (market="", globalName="NordicAllMarkets") ja suodattaa itse
jokaisen rivin oman market-kentän perusteella niin, että se sisältää sanan
"Helsinki" — nappaa sekä päälistan että First Northin oli Nasdaqin sisäinen
nimitys mikä tahansa. Tämä on turvallisempi tapa kuin täsmähaku yhdellä
arvatulla merkkijonolla.

TARKISTAMATTA VIELÄ:
  - messageUrl-sivun tarkka HTML-rakenne (fetch_release_text() alla arvaa
    parhaansa mukaan; testaa ensimmäisellä ajolla ja säädä tarvittaessa)
  - "attachment"-kentän rakenne kun se ei ole tyhjä (kaikki nähdyt
    esimerkit olivat attachment: [])

Käyttö:
    pip install requests beautifulsoup4
    python nasdaq_api_collector.py --from-date 2026-09-01
    python nasdaq_api_collector.py --from-date 2026-09-01 --dsn "postgresql://..."
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from functools import partial
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from parser import parse_release, to_rows

QUERY_URL = "https://api.news.eu.nasdaq.com/news/query.action"
CNS_CATEGORY = "Managers' Transactions"
PAGE_SIZE = 100
HELSINKI_MARKET_HINT = "helsinki"  # suodatetaan item["market"]:sta tällä
RELEASE_TZ = ZoneInfo("CET")  # sama kuin pyynnön oma timeZone=CET-parametri
# Tiedotteiden tekstit haetaan rinnakkain, koska niitä voi isommalla
# aikavälillä olla satoja — peräkkäin haettuna sivu tuntuisi jumittuvan.
MAX_WORKERS = 8

_JSONP_RE = re.compile(r"^\s*[\w.$]+\s*\((.*)\)\s*;?\s*$", re.S)

# Vakiotekstit tiedotesivun ympärillä, jotka eivät kuulu itse tiedotteeseen.
# TARKISTA näitä ensimmäisellä ajolla oikeaa HTML:ää vasten.
_BOILERPLATE_MARKERS = [
    "Sosiaalinen jako",
    "Company NewsSubscribeEuropean Market Activity",
    "© 20",
]


def to_epoch_millis(d: date) -> int:
    """Sama laskutapa kuin sivun omassa UI:ssa: päivän alku miinus 1 ms, UTC:ssä."""
    midnight = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return int(midnight.timestamp() * 1000) - 1


def parse_release_dt(raw: str | None) -> datetime | None:
    """'2026-09-17 17:00:00' (CET) -> aikavyöhykkeellinen datetime."""
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=RELEASE_TZ)


def strip_jsonp(text: str) -> dict:
    """Poistaa JSONP-kääreen ('handleResponse(...)'). Toimii myös puhtaalle JSONille."""
    text = text.strip()
    m = _JSONP_RE.match(text)
    return json.loads(m.group(1) if m else text)


def fetch_page(session: requests.Session, from_date_ms: int, start: int) -> dict:
    params = {
        "countResults": "true",
        "globalGroup": "exchangeNotice",
        "displayLanguage": "en",
        "timeZone": "CET",
        "dateMask": "yyyy-MM-dd HH:mm:ss",
        "limit": PAGE_SIZE,
        "start": start,
        "dir": "DESC",
        "globalName": "NordicAllMarkets",  # kaikki Pohjoismaat - suodatetaan itse alla
        "cnsCategory": CNS_CATEGORY,
        "market": "",
        "fromDate": from_date_ms,
        "toDate": "",
    }
    resp = session.get(QUERY_URL, params=params, timeout=20)
    resp.raise_for_status()
    return strip_jsonp(resp.text)


def fetch_all_items(session: requests.Session, from_date: date) -> list[dict]:
    """Hakee KAIKKI Pohjoismaiden johdon liiketoimet -tiedotteet fromDate:sta
    alkaen. Helsinki-suodatus tehdään erikseen (ks. is_helsinki_listing)."""
    from_ms = to_epoch_millis(from_date)
    items: list[dict] = []
    start = 0
    while True:
        payload = fetch_page(session, from_ms, start)
        results = payload.get("results", {})
        page_items = results.get("item", []) if isinstance(results, dict) else []
        if isinstance(page_items, dict):  # yhden tuloksen vastaus ei aina ole listassa
            page_items = [page_items]
        total = payload.get("count", len(page_items))
        items.extend(page_items)
        print(f"haettu {len(items)}/{total}", file=sys.stderr)
        if not page_items or len(items) >= total:
            break
        start += PAGE_SIZE
    return items


def is_helsinki_listing(item: dict) -> bool:
    """Nappaa sekä 'Main Market, Helsinki' että First North Finlandin, oli
    sen tarkka nimitys mikä tahansa — kunhan market-kenttä sisältää 'Helsinki'."""
    return HELSINKI_MARKET_HINT in (item.get("market") or "").lower()


def fetch_release_text(session: requests.Session, message_url: str) -> str:
    """Hakee tiedotteen koko tekstin messageUrl-sivulta.
    TARKISTAMATTA: tarkka HTML-rakenne — paras arvaukseni, säädä tarvittaessa."""
    resp = session.get(message_url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not any(marker in ln for marker in _BOILERPLATE_MARKERS)]
    return "\n".join(lines)


def fetch_and_parse_one(session: requests.Session, item: dict) -> dict:
    rid = f"nasdaq-{item['disclosureId']}"
    text = fetch_release_text(session, item["messageUrl"])
    result = parse_release(text, title=item.get("headline"))
    rows = to_rows(result, release_id=rid)
    print(f"[{result.status}] {item.get('company')} — {item.get('headline')} ({item.get('market')})")
    return {
        "release": {
            "id": rid,
            "source": "nasdaq_company_news",
            "published_at": parse_release_dt(item.get("published") or item.get("releaseTime")),
            "company_name": item.get("company"),
            "title": item.get("headline"),
            "url": item.get("messageUrl"),
            "body": text,
            "parse_status": result.status,
            "parser_version": result.parser_version,
        },
        "transactions": rows,
    }


def collect(from_date: date) -> list[dict]:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; sisapiiriseula-collector/0.2)"

    all_items = fetch_all_items(session, from_date)
    helsinki_items = [i for i in all_items if is_helsinki_listing(i)]
    print(
        f"{len(helsinki_items)}/{len(all_items)} tiedotetta oli Helsingin markkinalla "
        "(päälista tai First North). Haetaan tekstit rinnakkain...",
        file=sys.stderr,
    )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        collected = list(pool.map(partial(fetch_and_parse_one, session), helsinki_items))
    return collected


def save_to_postgres(dsn: str, collected: list[dict]) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        for entry in collected:
            r = entry["release"]
            conn.execute(
                """insert into releases (id, source, published_at, company_name, title, url, body,
                                          parse_status, parser_version)
                   values (%(id)s, %(source)s, %(published_at)s, %(company_name)s, %(title)s,
                           %(url)s, %(body)s, %(parse_status)s, %(parser_version)s)
                   on conflict (id) do nothing""",
                r,
            )
            for row in entry["transactions"]:
                cols = ", ".join(row)
                placeholders = ", ".join(f"%({c})s" for c in row)
                conn.execute(
                    f"""insert into insider_transactions ({cols}) values ({placeholders})
                        on conflict (release_id, notification_index, block_index) do nothing""",
                    row,
                )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-date", type=date.fromisoformat, required=True)
    ap.add_argument("--dsn", help="Postgres-yhteysmerkkijono. Jätä pois jos haluat vain tulosteen.")
    args = ap.parse_args()

    collected = collect(args.from_date)
    print(f"\nJäsennetty {len(collected)} tiedotetta.")

    if args.dsn:
        save_to_postgres(args.dsn, collected)
        print("Tallennettu tietokantaan.")


if __name__ == "__main__":
    main()
