"""
Testit johdon liiketoimet -jäsentimelle. Kaikki yhtiöt, henkilöt ja tunnisteet
ovat keksittyjä; tiedotteiden rakenne noudattaa Suomessa käytettyä pohjaa.
Aja: pytest -q
"""
from datetime import date
from decimal import Decimal

import pytest

from parser import (
    classify_nature,
    display_name,
    isin_is_valid,
    normalize_role,
    parse_number,
    parse_release,
    to_rows,
)

EN_BASIC = """
Esimerkki Oyj – Stock exchange release – Managers' transactions

Transaction notification under Article 19 of the EU Market Abuse Regulation

Person subject to the notification requirement
Name: VIRTANEN, MATTI
Position: President and CEO
Issuer: Esimerkki Oyj
LEI: 743700ABCDEF12345678

Notification type: INITIAL NOTIFICATION
Reference number: 743700ABCDEF12345678_20260310120000_3

Transaction date: 2026-03-09
Venue: NASDAQ HELSINKI LTD (XHEL)
Instrument type: SHARE
ISIN: FI0009000012
Nature of the transaction: ACQUISITION

Transaction details
(1): Volume: 1000 Unit price: 8.069 (EUR)

Aggregated transactions
(1): Volume: 1000 Volume weighted average price: 8.069 (EUR)
"""

FI_BASIC = """
Kuvitteellinen Teollisuus Oyj  Pörssitiedote  10.3.2026 klo 16.30
Johdon liiketoimet

Ilmoitusvelvollinen
Nimi: Lahtinen, Aino
Asema: Talousjohtaja
Liikkeeseenlaskija: Kuvitteellinen Teollisuus Oyj
LEI: 743700ZYXWVU98765432

Ilmoituksen luonne: ENSIMMÄINEN ILMOITUS
Viitenumero: 743700ZYXWVU98765432_20260310_7
____________________________________________

Liiketoimen päivämäärä: 2026-03-10
Kauppapaikka: NASDAQ HELSINKI LTD (XHEL)
Instrumentin tyyppi: OSAKE
ISIN: FI4000123450
Liiketoimen luonne: LUOVUTUS

Liiketoimien yksityiskohtaiset tiedot
(1): Volyymi: 100 200 Yksikköhinta: 1,856 EUR

Liiketoimien yhdistetyt tiedot
(1): Volyymi: 100 200 Keskihinta: 1,856 EUR
"""

EN_COMMA_DECIMAL = """
Person subject to the notification requirement
Name: Korhonen, Pekka
Position: Member of the Board
Issuer: Esimerkki Oyj
Notification type: INITIAL NOTIFICATION
Reference number: REF-1
Transaction date: 2026-02-02
Venue: NASDAQ HELSINKI LTD (XHEL)
Instrument type: SHARE
ISIN: FI0009000012
Nature of the transaction: ACQUISITION
Transaction details
(1): Volume: 100,200 Unit price: 1,66510 EUR
Aggregated transactions
(1): Volume: 100,200 Volume weighted average price: 1,66510 EUR
"""

EN_CLOSELY_ASSOCIATED = """
Person subject to the notification requirement
Name: Esimerkki Holding Oy
Position: Closely associated person
(X) Legal person
(1) Person Discharging Managerial Responsibilities In Issuer
Name: Nieminen, Laura
Position: Member of the Board
Issuer: Esimerkki Oyj
LEI: 743700ABCDEF12345678
Notification type: INITIAL NOTIFICATION
Reference number: REF-2
____________________________________________
Transaction date: 2026-01-15
Venue: NASDAQ HELSINKI LTD (XHEL)
Instrument type: SHARE
ISIN: FI0009000012
Nature of the transaction: ACQUISITION
Transaction details
(1): Volume: 5,000 Unit price: 10.00 EUR
(2): Volume: 3,000 Unit price: 10.20 EUR
Aggregated transactions
(1): Volume: 8,000 Volume weighted average price: 10.075 EUR
"""

EN_TWO_BLOCKS_AND_AWARD = """
Person subject to the notification requirement
Name: Mäkinen, Juha
Position: Other senior manager
Issuer: Esimerkki Oyj
Notification type: INITIAL NOTIFICATION
Reference number: REF-3
Transaction date: 2026-04-01
Venue: Outside a trading venue
Instrument type: SHARE
ISIN: FI0009000012
Nature of the transaction: RECEIPT OF A SHARE-BASED INCENTIVE OR REMUNERATION
Transaction details
(1): Volume: 2,285 Unit price: EUR 0
Aggregated transactions
(1): Volume: 2,285 Volume weighted average price: EUR 0
____________________________________________
Transaction date: 2026-04-02
Venue: NASDAQ HELSINKI LTD (XHEL)
Instrument type: SHARE
ISIN: FI0009000012
Nature of the transaction: DISPOSAL
Transaction details
(1): Volume: 1,000 Unit price: EUR 4.9596
Aggregated transactions
(1): Volume: 1,000 Volume weighted average price: EUR 4.9596 Total price: EUR 4,959.60
"""

# Sama kuin EN_BASIC, mutta HTML:stä litistettynä yhdelle riville.
EN_FLATTENED = " ".join(EN_BASIC.split())

EN_MISMATCH = EN_BASIC.replace(
    "(1): Volume: 1000 Volume weighted average price: 8.069 (EUR)",
    "(1): Volume: 1500 Volume weighted average price: 8.069 (EUR)",
)

# Jäljittelee tuotannosta löytynyttä oikeaa rakennetta: "N."-numerointi (ei
# "(N):"), prosenttimuotoinen hinta velkainstrumentille, kauppapaikan
# ulkopuolinen merkintä, ja KAKSI johtohenkilöä samassa lähipiiri-ilmoituksessa.
# Yhtiö, henkilöt ja tunnisteet ovat keksittyjä.
FI_BOND_TWO_PDMRS = """
Ilmoitusvelvollinen
Nimi: Keskinäinen Eläkevakuutusyhtiö Kuvitteellinen
Asema: Lähipiiriin kuuluva henkilö (oikeushenkilö)

Liikkeeseenlaskijassa johtotehtävissä toimiva henkilö
Nimi: Aalto, Elina, Asema: Hallituksen jäsen / varajäsen
Nimi: Virkkunen, Sami, Asema: Hallituksen jäsen / varajäsen

Liikkeeseenlaskija: Esimerkki Metsä Oyj
LEI: 743700EXAMPLE1234567
Ilmoituksen luonne: ENSIMMÄINEN ILMOITUS
Viitenumero: 999999/1/1
____________________________________________

Liiketoimen päivämäärä: 16.9.2026
Kauppapaikka: Kauppapaikan ulkopuolella
Instrumenttityyppi: VIERAAN PÄÄOMAN EHTOINEN INSTRUMENTTI
ISIN: FI4000999990
Liiketoimen luonne: MERKINTÄ

Liiketoimien yksityiskohtaiset tiedot:

1. Volyymi: 6.000.000 Yksikköhinta: 99.644 %

Liiketoimien yhdistetyt tiedot:

1. Volyymi: 6.000.000 Yksikköhinta: 99.644 %
"""

FREEFORM = """
Managers' transactions
Esimerkki Oyj:n toimitusjohtaja Matti Virtanen on ostanut yhtiön osakkeita
tuhat kappaletta hintaan noin kahdeksan euroa.
"""


def only_tx(result):
    assert len(result.notifications) == 1
    n = result.notifications[0]
    assert len(n.transactions) == 1
    return n, n.transactions[0]


def test_english_basic():
    r = parse_release(EN_BASIC)
    assert r.status == "OK", r
    n, t = only_tx(r)
    assert n.language == "en"
    assert n.person_display == "Matti Virtanen"
    assert n.role == "CEO"
    assert n.issuer_lei == "743700ABCDEF12345678"
    assert n.notification_type == "INITIAL"
    assert t.transaction_date == date(2026, 3, 9)
    assert t.kind == "BUY" and t.on_venue and t.venue_mic == "XHEL"
    assert t.isin == "FI0009000012" and t.isin_valid
    assert t.volume == Decimal("1000") and t.price == Decimal("8.069")
    assert t.currency == "EUR" and t.value == Decimal("8069.00")


def test_finnish_template_uses_decimal_comma_and_space_thousands():
    r = parse_release(FI_BASIC)
    assert r.status == "OK", r.notifications[0].warnings
    n, t = only_tx(r)
    assert n.language == "fi"
    assert n.role == "CFO"
    assert t.kind == "SELL"
    assert t.volume == Decimal("100200")
    assert t.price == Decimal("1.856")


def test_english_release_with_comma_decimal():
    r = parse_release(EN_COMMA_DECIMAL)
    n, t = only_tx(r)
    assert t.volume == Decimal("100200")
    assert t.price == Decimal("1.66510")
    assert n.role == "BOARD"
    assert r.status == "OK", n.warnings


def test_ambiguous_price_is_note_not_blocker():
    r = parse_release(EN_BASIC.replace("8.069 (EUR)", "8,069 (EUR)"))
    n, t = only_tx(r)
    assert t.price == Decimal("8.069")
    assert r.status == "OK"
    assert any("desimaaliluvuksi" in x for x in n.notes)


def test_closely_associated_legal_person():
    r = parse_release(EN_CLOSELY_ASSOCIATED)
    n, t = only_tx(r)
    assert n.is_closely_associated
    assert n.person_name == "Esimerkki Holding Oy"
    assert n.person_display == "Esimerkki Holding Oy"
    assert n.pdmr_name == "Nieminen, Laura"
    assert n.pdmr_position == "Member of the Board"
    assert t.volume == Decimal("8000")
    assert t.detail_count == 2
    assert not any("≠" in w for w in n.warnings)  # yksityiskohdat täsmäävät


def test_bond_subscription_with_dot_numbering_and_two_pdmrs():
    r = parse_release(FI_BOND_TWO_PDMRS)
    assert r.status == "NEEDS_REVIEW"  # toinen johtohenkilö ei mahdu tietomalliin
    n, t = only_tx(r)
    assert n.is_closely_associated
    assert n.person_name == "Keskinäinen Eläkevakuutusyhtiö Kuvitteellinen"
    assert n.pdmr_name == "Aalto, Elina"  # vain ensimmäinen johtohenkilö tallentuu
    assert any("2 johtohenkilöä" in w for w in n.warnings)
    assert t.instrument_type == "VIERAAN PÄÄOMAN EHTOINEN INSTRUMENTTI"
    assert not t.on_venue  # "Kauppapaikan ulkopuolella"
    assert t.kind == "SUBSCRIPTION"
    assert t.volume == Decimal("6000000")
    assert t.price == Decimal("99.644")
    assert t.isin == "FI4000999990" and t.isin_valid


def test_two_blocks_award_and_off_venue():
    r = parse_release(EN_TWO_BLOCKS_AND_AWARD)
    n = r.notifications[0]
    assert len(n.transactions) == 2
    award, sale = n.transactions
    assert award.kind == "AWARD" and not award.on_venue and award.price == 0
    assert sale.kind == "SELL" and sale.on_venue
    assert sale.value == Decimal("4959.60")
    assert n.role == "OTHER_EXECUTIVE"


def test_flattened_html_text():
    r = parse_release(EN_FLATTENED)
    assert r.status == "OK", r
    _, t = only_tx(r)
    assert t.volume == Decimal("1000") and t.price == Decimal("8.069")


def test_mismatch_between_details_and_aggregate_flags_review():
    r = parse_release(EN_MISMATCH)
    assert r.status == "NEEDS_REVIEW"
    assert any("≠" in w for w in r.notifications[0].warnings)


def test_freeform_release_goes_to_fallback():
    assert parse_release(FREEFORM).status == "UNPARSED"


def test_unrelated_release_is_ignored():
    r = parse_release("Esimerkki Oyj julkaisee tammi-maaliskuun osavuosikatsauksen 24.4.2026.")
    assert r.status == "NOT_RELEVANT"


def test_to_rows():
    rows = to_rows(parse_release(EN_TWO_BLOCKS_AND_AWARD), release_id="test-1")
    assert [r["block_index"] for r in rows] == [1, 2]
    assert rows[1]["kind"] == "SELL" and rows[1]["reference_number"] == "REF-3"


@pytest.mark.parametrize("raw,kind,lang,expected,ambiguous", [
    ("1000", "volume", "en", "1000", False),
    ("1 000", "volume", "fi", "1000", False),
    ("100,200", "volume", "en", "100200", False),
    ("1.000", "volume", "en", "1000", True),
    ("1,856", "price", "fi", "1.856", False),
    ("1,856", "price", "en", "1.856", True),
    ("1,66510", "price", "en", "1.66510", False),
    ("11,332.69", "price", "en", "11332.69", False),
    ("11.332,69", "price", "en", "11332.69", False),
    ("1,234,567", "volume", "en", "1234567", False),
    ("107.15976", "price", "en", "107.15976", False),
])
def test_parse_number(raw, kind, lang, expected, ambiguous):
    assert parse_number(raw, kind, lang) == (Decimal(expected), ambiguous)


def test_isin_checksum():
    assert isin_is_valid("US0378331005")      # tunnettu oikea ISIN
    assert isin_is_valid("FI0009000012")
    assert not isin_is_valid("FI0009000013")
    assert not isin_is_valid("FI00090000")


@pytest.mark.parametrize("nature,kind", [
    ("ACQUISITION", "BUY"),
    ("HANKINTA", "BUY"),
    ("DISPOSAL", "SELL"),
    ("LUOVUTUS", "SELL"),
    ("RECEIPT OF A SHARE-BASED INCENTIVE OR REMUNERATION", "AWARD"),
    ("OSAKEPERUSTEISEN KANNUSTIMEN TAI PALKKION VASTAANOTTAMINEN", "AWARD"),
    ("EXERCISE OF STOCK OPTIONS", "OPTION_EXERCISE"),
    ("SUBSCRIPTION", "SUBSCRIPTION"),
    ("RECEIPT OF A GIFT", "GIFT_OR_INHERITANCE"),
    ("PLEDGE", "PLEDGE"),
    ("OTHER", "OTHER"),
])
def test_classify_nature(nature, kind):
    assert classify_nature(nature) == kind


@pytest.mark.parametrize("position,role", [
    ("President and CEO", "CEO"),
    ("Toimitusjohtaja", "CEO"),
    ("Executive Vice President, Sales", "OTHER_EXECUTIVE"),
    ("Hallituksen varapuheenjohtaja", "BOARD"),
    ("Member of the Board", "BOARD"),
    ("Talousjohtaja", "CFO"),
    ("Other senior manager", "OTHER_EXECUTIVE"),
    ("Muu ylin johto", "OTHER_EXECUTIVE"),
    ("Lähipiiriin kuuluva henkilö", "CLOSELY_ASSOCIATED"),
])
def test_normalize_role(position, role):
    assert normalize_role(position) == role


def test_display_name():
    assert display_name("VIRTANEN, MATTI") == "Matti Virtanen"
    assert display_name("Lahtinen, Aino") == "Aino Lahtinen"
    assert display_name("Esimerkki Holding Oy") == "Esimerkki Holding Oy"
