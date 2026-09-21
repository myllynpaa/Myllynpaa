"""
Johdon liiketoimet -tiedotteiden (MAR 19 artikla) jäsennin.

Jäsentää suomalaisten listayhtiöiden pörssitiedotteet, jotka noudattavat
Suomessa vakiintunutta pohjaa: englanniksi "Transaction notification under
Article 19 of the EU Market Abuse Regulation", suomeksi "Johdon liiketoimet".
Tukee sekä englannin- että suomenkielistä pohjaa, lähipiiri-ilmoituksia,
useita liiketoimilohkoja samassa ilmoituksessa ja HTML:stä litistettyä tekstiä.

Periaate: jäsennin ei koskaan arvaa hiljaa. Jokainen epävarma kohta kirjataan
varoitukseksi, ja ilmoitus merkitään tilaan NEEDS_REVIEW. Jos tiedote näyttää
johdon liiketoimelta mutta pohjaa ei tunnisteta, tila on UNPARSED ja tiedote
kannattaa ohjata kielimallille (LLM_FALLBACK_PROMPT) tai käsin tarkistettavaksi.

Käyttö:
    from parser import parse_release, to_rows
    result = parse_release(teksti, title=otsikko)
    if result.status == "OK":
        rows = to_rows(result, release_id="nasdaq-123456")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

PARSER_VERSION = "0.1.0"

# --------------------------------------------------------------------------
# Kenttien otsikot (englanti ja suomi)
# --------------------------------------------------------------------------

HEADINGS = [
    "Person subject to the notification requirement",
    "Ilmoitusvelvollinen",
]

HEADER_LABELS = {
    "name": ["Name", "Nimi"],
    "position": ["Position", "Asema"],
    "issuer": ["Issuer", "Liikkeeseenlaskija"],
    "lei": ["LEI"],
    "notification_type": ["Notification type", "Ilmoituksen luonne"],
    "reference": ["Reference number", "Viitenumero"],
}

BLOCK_LABELS = {
    "date": ["Transaction date", "Liiketoimen päivämäärä"],
    "venue": ["Venue", "Kauppapaikka"],
    "instrument": ["Instrument type", "Instrumentin tyyppi", "Instrumenttityyppi"],
    "isin": ["ISIN"],
    "nature": ["Nature of the transaction", "Nature of transaction", "Liiketoimen luonne"],
}

DETAIL_SECTION = ["Transaction details", "Liiketoimien yksityiskohtaiset tiedot"]
AGGREGATE_SECTION = ["Aggregated transactions", "Liiketoimien yhdistetyt tiedot"]

FINNISH_MARKERS = ["Nimi", "Asema", "Liikkeeseenlaskija", "Liiketoimen luonne", "Volyymi"]

TITLE_KEYWORDS = [
    "managers' transactions",
    "managers’ transactions",
    "manager's transaction",
    "manager’s transaction",
    "johdon liiketoimet",
    "johdon liiketoimi",
    "ledningens transaktioner",
    "article 19",
    "19 artiklan",
]


def _alt(labels):
    """Regex-vaihtoehtolista, pisimmät ensin, jotta lyhyt otsikko ei syö pitkää."""
    return "|".join(re.escape(x) for x in sorted(labels, key=len, reverse=True))


_ALL_FIELD_LABELS = [lab for group in (HEADER_LABELS, BLOCK_LABELS) for labs in group.values() for lab in labs]
_FIELD_SPLIT_RE = re.compile(r"\b(?:%s)\s*:" % _alt(_ALL_FIELD_LABELS), re.I)
_SECTION_SPLIT_RE = re.compile(r"\b(?:%s)\b" % _alt(HEADINGS + DETAIL_SECTION + AGGREGATE_SECTION), re.I)
_HEADING_LINE_RE = re.compile(r"^\s*(?:%s)\b" % _alt(HEADINGS), re.I | re.M)
_DATE_LINE_RE = re.compile(r"^\s*(?:%s)\s*:" % _alt(BLOCK_LABELS["date"]), re.I | re.M)

_ITEM_RE = re.compile(
    r"(?:\(\s*\d+\s*\)|\d+\s*\.)\s*:?\s*"
    r"(?:Volume|Volyymi)\s*:\s*(?P<vol>\d[\d .,]*\d|\d)\s*"
    r"(?:Unit price|Yksikköhinta|Volume weighted average price|"
    r"Volyymipainotettu keskihinta|Painotettu keskihinta|Keskihinta)\s*:\s*"
    r"(?P<price>.+?)"
    r"(?:\s*(?:Total price|Kokonaishinta|Yhteensä)\s*:\s*(?P<total>.+?))?"
    r"(?=\s*(?:\(\s*\d+\s*\)|\d+\s*\.)\s*:?\s*(?:Volume|Volyymi)|\n|$)",
    re.I,
)
_NUMBER_TOKEN_RE = re.compile(r"\d[\d .,]*\d|\d")
_CURRENCY_RE = re.compile(r"\b(EUR|SEK|NOK|DKK|USD|GBP|CHF|ISK|JPY|CAD)\b")
_MIC_RE = re.compile(r"\(([A-Z0-9]{4})\)\s*$")
_COMPANY_SUFFIX_RE = re.compile(r"\b(oyj?|ab|abp|ltd|limited|plc|inc|gmbh|as|a/s|asa|ky|tmi|holding|invest)\b\.?", re.I)


# --------------------------------------------------------------------------
# Tietorakenteet
# --------------------------------------------------------------------------

@dataclass
class Transaction:
    block_index: int
    transaction_date: date | None
    venue: str | None
    venue_mic: str | None
    on_venue: bool
    instrument_type: str | None
    isin: str | None
    isin_valid: bool
    nature_raw: str | None
    kind: str
    volume: Decimal | None
    price: Decimal | None
    currency: str | None
    value: Decimal | None
    detail_count: int


@dataclass
class Notification:
    language: str
    reference_number: str | None
    notification_type: str
    issuer_name: str | None
    issuer_lei: str | None
    person_name: str | None
    person_display: str | None
    position_raw: str | None
    role: str
    is_closely_associated: bool
    pdmr_name: str | None
    pdmr_position: str | None
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # vaativat tarkistuksen
    notes: list[str] = field(default_factory=list)      # tiedoksi, eivät estä julkaisua


@dataclass
class ParseResult:
    status: str  # OK | NEEDS_REVIEW | UNPARSED | NOT_RELEVANT
    notifications: list[Notification] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


# --------------------------------------------------------------------------
# Apufunktiot
# --------------------------------------------------------------------------

def looks_like_managers_transaction(title: str | None, text: str) -> bool:
    """Karkea tunnistin: kannattaako tiedote ylipäätään yrittää jäsentää."""
    haystack = f"{title or ''}\n{text[:3000]}".lower()
    return any(k in haystack for k in TITLE_KEYWORDS)


def parse_number(raw: str, kind: str, lang: str) -> tuple[Decimal, bool]:
    """
    Muuntaa luvun tekstistä Decimaliksi. Palauttaa (arvo, oliko_tulkinnanvarainen).

    kind: "volume" tai "price". Suomenkielisessä pohjassa pilkku on aina
    desimaalierotin ja välilyönti tuhaterotin. Englanninkielisissä
    tiedotteissa suomalaiset yhtiöt käyttävät molempia tapoja, joten
    muoto "1,856" on aidosti tulkinnanvarainen.
    """
    s = re.sub(r"\s", "", raw).strip(".,")
    if not s or not re.fullmatch(r"\d[\d.,]*", s):
        raise ValueError(f"ei lukua: {raw!r}")

    ambiguous = False
    has_c, has_d = "," in s, "." in s
    if has_c and has_d:
        # Viimeisenä esiintyvä merkki on desimaalierotin.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_c:
        head, _, tail = s.partition(",")
        if s.count(",") > 1:
            s = s.replace(",", "")                      # 1,234,567
        elif lang == "fi":
            s = s.replace(",", ".")                     # 1,856 = 1.856
        elif len(tail) == 3 and kind == "volume":
            s = s.replace(",", "")                      # 100,200 kpl: osakemäärä on kokonaisluku
        elif len(tail) == 3:
            ambiguous = True                            # hinta 1,856 = 1856 vai 1.856?
            s = s.replace(",", ".")
        else:
            s = s.replace(",", ".")                     # 1,66510 = 1.66510
    elif has_d:
        if s.count(".") > 1:
            s = s.replace(".", "")                      # 1.234.567
        elif kind == "volume" and len(s.partition(".")[2]) == 3:
            ambiguous = True                            # 1.000 kpl vai 1,0 kpl?
            s = s.replace(".", "")
    try:
        return Decimal(s), ambiguous
    except InvalidOperation as exc:
        raise ValueError(f"ei lukua: {raw!r}") from exc


def isin_is_valid(isin: str | None) -> bool:
    """ISO 6166 -tarkiste (Luhn muunnetuille numeroille)."""
    if not isin or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(isin[-1])


def classify_nature(raw: str | None) -> str:
    """
    Luokittelee liiketoimen Finvizin tapaan. Järjestys on tärkeä:
    kannustinpalkkio voi sisältää sanan "acquisition", joten se tarkistetaan ensin.
    """
    if not raw:
        return "UNKNOWN"
    s = raw.upper()
    rules = [
        ("AWARD", ["INCENTIVE", "REMUNERATION", "KANNUSTI", "PALKKIO", "PALKITSEMIS"]),
        ("OPTION_EXERCISE", ["OPTION", "OPTIO"]),
        ("SUBSCRIPTION", ["SUBSCRIPTION", "MERKINT"]),
        ("GIFT_OR_INHERITANCE", ["GIFT", "INHERIT", "LAHJA", "PERINT"]),
        ("PLEDGE", ["PLEDGE", "PANT"]),
        ("LENDING", ["LENDING", "LAINA"]),
        ("BUY", ["ACQUISITION", "PURCHASE", "HANKINTA", "OSTO"]),
        ("SELL", ["DISPOSAL", "SALE", "LUOVUTUS", "MYYNTI"]),
    ]
    for kind, words in rules:
        if any(w in s for w in words):
            return kind
    return "OTHER"


def normalize_role(position: str | None) -> str:
    if not position:
        return "UNKNOWN"
    s = position.lower()
    # Järjestys on tärkeä: "Hallituksen varapuheenjohtaja" on hallitus,
    # "Executive Vice President" ei ole toimitusjohtaja.
    rules = [
        ("CLOSELY_ASSOCIATED", ["closely associated", "lähipiiri"]),
        ("SUPERVISORY", ["supervisory", "hallintoneuvosto", "auditor", "tilintarkastaja"]),
        ("BOARD", ["board", "hallitu", "chair", "puheenjohtaja"]),
        ("OTHER_EXECUTIVE", ["vice president", "deputy", "varatoimitusjohtaja", "evp", "svp"]),
        ("CEO", ["ceo", "chief executive", "president", "toimitusjohtaja"]),
        ("CFO", ["cfo", "chief financial", "talousjohtaja"]),
        ("OTHER_EXECUTIVE", ["senior manager", "ylin johto", "johtoryhm", "management",
                             "executive", "director", "johtaja"]),
    ]
    for role, words in rules:
        if any(w in s for w in words):
            return role
    return "OTHER"


def display_name(name: str | None) -> str | None:
    """'Virtanen, Matti' -> 'Matti Virtanen'. Yritysnimet jätetään ennalleen."""
    if not name:
        return None
    if name.count(",") == 1 and not _COMPANY_SUFFIX_RE.search(name):
        last, first = (p.strip() for p in name.split(","))
        if last and first:
            return f"{first.title()} {last.title()}" if name.isupper() else f"{first} {last}"
    return name


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    return None


def _normalize(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u202f", " ").replace("\r", "")
    text = text.replace("’", "'")
    text = re.sub(r"[ \t]+", " ", text)
    # Jokainen tunnettu kenttä ja osio omalle rivilleen (auttaa litistetyssä HTML:ssä).
    text = _SECTION_SPLIT_RE.sub(lambda m: "\n" + m.group(0) + "\n", text)
    text = _FIELD_SPLIT_RE.sub(lambda m: "\n" + m.group(0), text)
    # Yksityiskohtarivit "(1): Volume" omille riveilleen.
    text = re.sub(r"(?<!\n)\s*(\(\s*\d+\s*\)\s*:?\s*(?:Volume|Volyymi)\s*:)", r"\n\1", text, flags=re.I)
    return text


def _fields(text: str, labels: list[str]) -> list[str]:
    """Kaikki kentän arvot järjestyksessä. Tyhjä arvo -> seuraava rivi."""
    pattern = re.compile(r"^\s*(?:%s)\s*:[ \t]*(.*)$" % _alt(labels), re.I | re.M)
    values = []
    for m in pattern.finditer(text):
        value = m.group(1).strip()
        if not value:
            rest = text[m.end():].lstrip("\n").split("\n", 1)[0].strip()
            if rest and not _FIELD_SPLIT_RE.match(rest):
                value = rest
        values.append(value.strip(" _-,;") or None)
    return [v for v in values if v]


def _first(text: str, labels: list[str]) -> str | None:
    vals = _fields(text, labels)
    return vals[0] if vals else None


def _section(text: str, start_labels: list[str], stop_labels: list[str]) -> str:
    m = re.search(r"\b(?:%s)\b" % _alt(start_labels), text, re.I)
    if not m:
        return ""
    rest = text[m.end():]
    stop = re.search(r"\b(?:%s)\b" % _alt(stop_labels), rest, re.I) if stop_labels else None
    return rest[: stop.start()] if stop else rest


def _parse_items(section: str, lang: str, warnings: list[str], notes: list[str]):
    items = []
    for m in _ITEM_RE.finditer(section):
        try:
            vol, amb_v = parse_number(m["vol"], "volume", lang)
            num = _NUMBER_TOKEN_RE.search(m["price"])
            if not num:
                raise ValueError(f"hinta puuttuu: {m['price']!r}")
            price, amb_p = parse_number(num.group(0), "price", lang)
        except ValueError as exc:
            warnings.append(f"lukua ei voitu tulkita: {exc}")
            continue
        cur = _CURRENCY_RE.search(m["price"].upper())
        total = None
        if m["total"]:
            tnum = _NUMBER_TOKEN_RE.search(m["total"])
            if tnum:
                try:
                    total, _ = parse_number(tnum.group(0), "price", lang)
                except ValueError:
                    pass
        if amb_v:
            warnings.append(f"tulkinnanvarainen volyymi {m['vol'].strip()!r}, tulkittu {vol}")
        if amb_p:
            # Tulkinta desimaaliksi on lähes aina oikea (tuhansien eurojen osakkeita
            # ei Helsingissä juuri ole). Varmista myöhemmin päätöskurssia vasten.
            notes.append(f"hinta {num.group(0)!r} tulkittu desimaaliluvuksi {price}")
        items.append({"volume": vol, "price": price, "currency": cur.group(1) if cur else None, "total": total})
    return items


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= tolerance


def _parse_block(block: str, index: int, lang: str, warnings: list[str], notes: list[str]) -> Transaction:
    raw_date = _first(block, BLOCK_LABELS["date"])
    venue = _first(block, BLOCK_LABELS["venue"])
    instrument = _first(block, BLOCK_LABELS["instrument"])
    isin_raw = _first(block, BLOCK_LABELS["isin"])
    nature = _first(block, BLOCK_LABELS["nature"])

    tx_date = _parse_date(raw_date)
    if raw_date and not tx_date:
        warnings.append(f"päivämäärää ei tunnistettu: {raw_date!r}")

    isin = None
    if isin_raw:
        m = re.search(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", isin_raw.upper())
        isin = m.group(0) if m else None
    isin_ok = isin_is_valid(isin)
    if isin and not isin_ok:
        warnings.append(f"ISIN-tarkiste ei täsmää: {isin}")

    mic = None
    if venue:
        mm = _MIC_RE.search(venue.upper())
        mic = mm.group(1) if mm else (venue.strip().upper() if re.fullmatch(r"[A-Za-z0-9]{4}", venue.strip()) else None)
    off_venue = bool(venue) and any(w in venue.upper() for w in ["OUTSIDE", "ULKOPUOLELLA", "OFF-VENUE"])

    details = _parse_items(_section(block, DETAIL_SECTION, AGGREGATE_SECTION), lang, warnings, notes)
    aggregate = _parse_items(_section(block, AGGREGATE_SECTION, []), lang, warnings, notes)

    volume = price = currency = None
    if aggregate:
        volume, price, currency = aggregate[0]["volume"], aggregate[0]["price"], aggregate[0]["currency"]
        if len(aggregate) > 1:
            warnings.append(f"lohkossa {index} useita yhdistettyjä rivejä; käytetty ensimmäistä")
    elif details:
        volume = sum((d["volume"] for d in details), Decimal(0))
        value_sum = sum((d["volume"] * d["price"] for d in details), Decimal(0))
        price = (value_sum / volume).quantize(Decimal("0.000001")) if volume else Decimal(0)
        currency = details[0]["currency"]
        notes.append(f"lohkossa {index} ei yhdistettyä riviä; laskettu yksityiskohdista")
    else:
        warnings.append(f"lohkosta {index} ei löytynyt volyymia eikä hintaa")

    # Ristiintarkistus: yksityiskohtien summa vs. yhdistetty rivi.
    if aggregate and details:
        d_vol = sum((d["volume"] for d in details), Decimal(0))
        d_val = sum((d["volume"] * d["price"] for d in details), Decimal(0))
        if d_vol != volume:
            warnings.append(f"lohko {index}: yksityiskohtien volyymi {d_vol} ≠ yhdistetty {volume}")
        elif d_vol and not _within(d_val / d_vol, price, Decimal("0.005")):
            warnings.append(f"lohko {index}: yksityiskohtien keskihinta {d_val / d_vol:.4f} ≠ yhdistetty {price}")

    value = (volume * price).quantize(Decimal("0.01")) if volume is not None and price is not None else None
    total = aggregate[0]["total"] if aggregate else None
    if total is not None and value is not None and not _within(value, total, Decimal("0.01")):
        warnings.append(f"lohko {index}: volyymi × hinta {value} ≠ ilmoitettu kokonaishinta {total}")

    kind = classify_nature(nature)
    if kind in ("UNKNOWN", "OTHER"):
        warnings.append(f"liiketoimen luonne tuntematon: {nature!r}")
    if not currency and price:
        warnings.append(f"lohko {index}: valuutta puuttuu")

    return Transaction(
        block_index=index,
        transaction_date=tx_date,
        venue=venue,
        venue_mic=mic,
        on_venue=bool(venue) and not off_venue,
        instrument_type=instrument.upper() if instrument else None,
        isin=isin,
        isin_valid=isin_ok,
        nature_raw=nature,
        kind=kind,
        volume=volume,
        price=price,
        currency=currency,
        value=value,
        detail_count=len(details),
    )


def _parse_notification(segment: str, lang: str) -> Notification | None:
    starts = [m.start() for m in _DATE_LINE_RE.finditer(segment)]
    if not starts:
        return None
    header = segment[: starts[0]]
    blocks = [segment[s:e] for s, e in zip(starts, starts[1:] + [len(segment)])]

    warnings: list[str] = []
    notes: list[str] = []
    names = _fields(header, HEADER_LABELS["name"])
    positions = _fields(header, HEADER_LABELS["position"])
    person = names[0] if names else None
    position = positions[0] if positions else None
    role = normalize_role(position)
    closely = role == "CLOSELY_ASSOCIATED"

    pdmr_name = pdmr_position = None
    if closely:
        pdmr_name = names[1] if len(names) > 1 else None
        pdmr_position = positions[1] if len(positions) > 1 else None
        if not pdmr_name:
            warnings.append("lähipiiri-ilmoitus, mutta johtohenkilön nimeä ei löytynyt")
        elif len(names) > 2:
            # Sama lähipiiri-ilmoitus voi liittyä useampaan johtohenkilöön yhtä aikaa
            # (esim. eläkevakuutusyhtiö, jolla on kaksi henkilöä hallituksessa).
            # Tietomalli tukee vain yhtä pdmr_name-kenttää — loput jäävät pois.
            warnings.append(
                f"ilmoituksessa {len(names) - 1} johtohenkilöä, vain ensimmäinen "
                "(pdmr_name) tallennettu — tarkista käsin"
            )

    if not person:
        warnings.append("ilmoitusvelvollisen nimi puuttuu")

    ntype_raw = (_first(header, HEADER_LABELS["notification_type"]) or "").upper()
    if any(w in ntype_raw for w in ["AMEND", "MUUTOS", "KORJA", "CORRECT"]):
        ntype = "AMENDMENT"
        warnings.append("muutosilmoitus: korvaa aiemman ilmoituksen, tarkista käsin")
    else:
        ntype = "INITIAL"

    lei = _first(header, HEADER_LABELS["lei"])
    if lei:
        m = re.search(r"\b[A-Z0-9]{18}\d{2}\b", lei.upper())
        lei = m.group(0) if m else lei

    txs = [_parse_block(b, i + 1, lang, warnings, notes) for i, b in enumerate(blocks)]

    return Notification(
        language=lang,
        reference_number=_first(header, HEADER_LABELS["reference"]),
        notification_type=ntype,
        issuer_name=_first(header, HEADER_LABELS["issuer"]),
        issuer_lei=lei,
        person_name=person,
        person_display=display_name(person),
        position_raw=position,
        role=role,
        is_closely_associated=closely,
        pdmr_name=pdmr_name,
        pdmr_position=pdmr_position,
        transactions=txs,
        warnings=warnings,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Julkinen rajapinta
# --------------------------------------------------------------------------

def parse_release(text: str, title: str | None = None) -> ParseResult:
    """Jäsentää yhden pörssitiedotteen tekstin (otsikko valinnainen)."""
    relevant = looks_like_managers_transaction(title, text)
    norm = _normalize(text)
    lang = "fi" if any(re.search(rf"^\s*{re.escape(w)}\s*:", norm, re.I | re.M) for w in FINNISH_MARKERS) else "en"

    heading_starts = [m.start() for m in _HEADING_LINE_RE.finditer(norm)]
    segments = [norm[s:e] for s, e in zip(heading_starts, heading_starts[1:] + [len(norm)])] or [norm]

    notifications = [n for n in (_parse_notification(seg, lang) for seg in segments) if n]

    # Kaksi otsikkoa voi kuulua samaan lähipiiri-ilmoitukseen: yhdistä
    # otsikkosegmentti, jossa ei ollut liiketoimia, seuraavaan.
    if not notifications and len(segments) > 1:
        n = _parse_notification(norm, lang)
        notifications = [n] if n else []

    if not notifications or not any(n.transactions for n in notifications):
        status = "UNPARSED" if relevant else "NOT_RELEVANT"
        return ParseResult(status=status, warnings=["tunnettua MAR 19 -pohjaa ei löytynyt"] if relevant else [])

    status = "NEEDS_REVIEW" if any(n.warnings for n in notifications) else "OK"
    return ParseResult(status=status, notifications=notifications)


def to_rows(result: ParseResult, release_id: str) -> list[dict]:
    """Litistää tuloksen riveiksi taulua insider_transactions varten (schema.sql)."""
    rows = []
    for n_idx, n in enumerate(result.notifications):
        for t in n.transactions:
            rows.append({
                "release_id": release_id,
                "notification_index": n_idx,
                "block_index": t.block_index,
                "reference_number": n.reference_number,
                "notification_type": n.notification_type,
                "issuer_name": n.issuer_name,
                "issuer_lei": n.issuer_lei,
                "isin": t.isin,
                "isin_valid": t.isin_valid,
                "person_name": n.person_name,
                "person_display": n.person_display,
                "role": n.role,
                "position_raw": n.position_raw,
                "is_closely_associated": n.is_closely_associated,
                "pdmr_name": n.pdmr_name,
                "pdmr_position": n.pdmr_position,
                "transaction_date": t.transaction_date,
                "kind": t.kind,
                "nature_raw": t.nature_raw,
                "instrument_type": t.instrument_type,
                "venue": t.venue,
                "venue_mic": t.venue_mic,
                "on_venue": t.on_venue,
                "volume": t.volume,
                "price": t.price,
                "currency": t.currency,
                "value": t.value,
                "detail_count": t.detail_count,
                "status": result.status,
                "warnings": n.warnings,
                "notes": n.notes,
                "parser_version": result.parser_version,
            })
    return rows


# --------------------------------------------------------------------------
# Varajäsennin kielimallille (vain tiloille UNPARSED)
# --------------------------------------------------------------------------

LLM_FALLBACK_PROMPT = """\
Alla on pörssitiedote, joka koskee johdon liiketoimia (EU:n markkinoiden
väärinkäyttöasetuksen 19 artikla). Poimi siitä tiedot ja vastaa pelkällä
JSON-oliolla ilman muuta tekstiä. Älä päättele puuttuvia arvoja: jos tieto
puuttuu tekstistä, käytä null.

{
  "notifications": [{
    "person_name": str,            // ilmoitusvelvollinen sellaisenaan
    "position": str,               // asema sellaisenaan
    "is_closely_associated": bool,
    "pdmr_name": str | null,       // lähipiiri-ilmoituksessa johtohenkilö
    "issuer_name": str | null,
    "issuer_lei": str | null,
    "reference_number": str | null,
    "notification_type": "INITIAL" | "AMENDMENT",
    "transactions": [{
      "transaction_date": "YYYY-MM-DD",
      "venue": str | null,
      "instrument_type": str | null,
      "isin": str | null,
      "nature": str,               // liiketoimen luonne sellaisenaan
      "volume": number,            // yhdistetty volyymi, desimaalipiste
      "price": number,             // yhdistetty keskihinta, desimaalipiste
      "currency": str | null
    }]
  }]
}

Tiedote:
"""
