"""Talentsoft career portals (Safran).

Safran was on the watchlist as `manual` with a note saying its portal was a
JS-rendered .aspx app with no machine-readable view. That was wrong. The app is
Talentsoft, and `liste-toutes-offres.aspx` is server-rendered HTML with a plain
`&page=` parameter — 3795 offers twenty at a time.

The flat listing cannot be walked to the end: page 51 serves page 1 again, so
an anonymous visitor sees 1000 of the 3795 offers and nothing says so. The way
round it is the `facet_JobFamily` parameter. Every list page carries a link per
family with its offer count — "Electronique et automatique (89)" — and no
family comes near the 1000 ceiling, so paging each one in turn covers the board
completely.

The families are read off the page rather than configured. Safran's taxonomy is
Safran's to change, and a hard-coded list of 35 numeric ids would rot silently
into the same under-reporting this exists to avoid. Every family is walked, not
a chosen few: a navigation role filed under "Essais" or "Logiciel" is exactly
what a curated list would miss, and the matcher already discards the rest.

This costs about 190 requests and some minutes. That is the price of a complete
view of a board this size; the sweep runs in the background and reports itself.

Rows carry title, URL, reference, contract and a postal address, but no advert
body — that comes from the detail page, which is also server-rendered. See
`posting_text.py` for the route that pulls the description out of it.

`ats_endpoint` holds the portal root, e.g. https://careers.safran-group.com.
"""

from __future__ import annotations

import html as html_module
import re
import unicodedata
from urllib.parse import urljoin

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "talentsoft"

# A total page budget across every family, not a per-family one. Safran's board
# needs about 190; the headroom is for it growing, and the cap is a stop rather
# than an expectation.
DEFAULT_MAX_PAGES = 260
PAGE_SIZE = 20
LIST_PATH = "/offre-de-emploi/liste-toutes-offres.aspx"

# A row is delimited by where the next one starts, not by </li>: it holds a
# nested <ul> of detail cells, so matching to the first closing tag stops
# before the address.
_ITEM_START_RE = re.compile(r'<li class="ts-offer-list-item[^"]*"')
_LINK_RE = re.compile(
    r'class="ts-offer-list-item__title-link[^"]*"\s+href="([^"]+)"', re.S)
_TITLE_RE = re.compile(r'data-title="([^"]*)"')
_REFERENCE_RE = re.compile(r'data-reference="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")
# Reference, date, contract, address — the address is the last cell.
_DETAILS_RE = re.compile(
    r'<ul class="ts-offer-list-item__description[^"]*"\s*>(.*?)</ul>', re.S)
_CELL_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
# 'facet_JobFamily=4244" title="... : Electronique et automatique (89)"'
_FAMILY_RE = re.compile(r'facet_JobFamily=(\d+)"\s+title="[^"]*\((\d+)\)"')

# The country the row names, in the languages the portal writes it in. The
# board is group-wide: Safran has sites in Sydney, Bangalore, Redmond and
# Chihuahua, and stamping every row with the employer's own FR put sixty of
# them into the results as French jobs, where the country exclusion could not
# reach them. Where a row names its country it is believed; where it does not,
# the caller keeps its default, because a bare "Massy" says nothing and a bare
# "Granada" could be Spain or Nicaragua.
_COUNTRY_WORDS = {
    "france": "FR", "belgium": "BE", "belgique": "BE",
    "united kingdom": "GB", "royaume-uni": "GB", "royaume uni": "GB",
    "germany": "DE", "allemagne": "DE", "deutschland": "DE",
    "spain": "ES", "espagne": "ES", "espana": "ES",
    "italy": "IT", "italie": "IT", "italia": "IT",
    "switzerland": "CH", "suisse": "CH",
    "netherlands": "NL", "pays-bas": "NL", "pays bas": "NL",
    "poland": "PL", "pologne": "PL", "portugal": "PT",
    "australia": "AU", "australie": "AU",
    "india": "IN", "inde": "IN",
    "mexico": "MX", "mexique": "MX",
    "canada": "CA", "china": "CN", "chine": "CN",
    "singapore": "SG", "singapour": "SG",
    "japan": "JP", "japon": "JP",
    "brazil": "BR", "bresil": "BR",
    "morocco": "MA", "maroc": "MA",
    "united states": "US", "etats-unis": "US", "etats unis": "US", "usa": "US",
}
# Longest first, so "united kingdom" is tried before a bare country word that
# happens to be a prefix of it.
_COUNTRY_RE = re.compile(
    r"[,\s]+(" + "|".join(
        re.escape(w) for w in sorted(_COUNTRY_WORDS, key=len, reverse=True)
    ) + r")\s*$",
    re.I,
)

# "7330 Lincoln Way CA 92841 Garden Grove". The US rows name no country at all;
# a two-letter state code sitting immediately before the postcode is the only
# thing that distinguishes them, and no French address has that shape.
_US_STATE_RE = re.compile(r"\b(A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]"
                          r"|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]"
                          r"|T[NX]|UT|V[AT]|W[AIVY])\s+\d{5}\b")


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


def country_from_address(raw: str) -> str:
    """`'1 Marsh Street Botany Australia'` -> `'AU'`; `'Massy'` -> `''`.

    Returns the ISO code the address names, or an empty string when it names
    none — the same contract as `workday.split_location`, and for the same
    reason: a guess here is a posting filed under a country you cannot work in.
    """
    text = re.sub(r"\s+", " ", html_module.unescape(raw or "")).strip(" ,")
    if not text:
        return ""
    match = _COUNTRY_RE.search(_strip_accents(text))
    if match:
        return _COUNTRY_WORDS[match.group(1).lower()]
    if _US_STATE_RE.search(text):
        return "US"
    return ""


# A postcode is the reliable hinge in a French address; anything after the last
# one is the town. Italian and Spanish sites use four to five digits too.
_POSTCODE_RE = re.compile(r"\b\d{4,5}\b")
# "OX16 4X", "NP44 3HQ", "H9J 3K1": a letter-led outward code followed by an
# inward one. Anchored on the letters so a French "91344 Massy" cannot match.
# The Canadian rows put the province in front of it — "QC H9J 3K1" — and the
# province is not part of the town either, so it is taken with the code.
_OUTWARD_CODE_RE = re.compile(
    r"\b(?:[A-Z]{2}\s+)?[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]\d?[A-Z]?\b", re.I)

# The word that ends a street name in the addresses that carry no postcode.
# Only needed for the mixed-case foreign rows; French ones are settled by the
# postcode long before this.
_STREET_SUFFIX_RE = re.compile(
    r"\b(street|st|road|rd|avenue|ave|drive|dr|lane|ln|way|boulevard|blvd"
    r"|court|ct|place|pl|highway|hwy|parkway|pkwy|park|close|crescent"
    r"|terrace|square|estate|industrial estate|industrial park)\b\.?",
    re.I,
)

# No postcode: Talentsoft still upper-cases the town, so a trailing run of
# capitals is the town even when the street name is mixed case.
_TRAILING_CAPS_RE = re.compile(
    r"([A-ZÀ-Ý][A-ZÀ-Ý'\-]+(?:\s+[A-ZÀ-Ý'\-]+)*)\s*$")


def _text(raw: str) -> str:
    plain = _TAG_RE.sub(" ", html_module.unescape(raw or ""))
    return re.sub(r"\s+", " ", plain).strip()


def city_from_address(raw: str) -> str:
    """`'21 avenue du Gros Chene 95610 ERAGNY-SUR-OISE'` -> `'ERAGNY-SUR-OISE'`.

    The rows carry a full postal address where every other source carries a
    town. Left whole it would never match cities.yaml, so the town is pulled
    out: after the last postcode when there is one, otherwise the trailing run
    of capitals, otherwise the string as it stands for the many rows that are
    already just "Massy" or "Gloucester".
    """
    text = re.sub(r"\s+", " ", html_module.unescape(raw or "")).strip(" ,")
    # The country word is not part of the town: "Botany Australia" matches
    # nothing in cities.yaml. Cut it on the accent-folded copy so "Allemagne"
    # and "Bresil" are found, then cut the same span from the original.
    folded = _strip_accents(text)
    country = _COUNTRY_RE.search(folded)
    if country:
        text = text[:country.start()].strip(" ,")
    if not text:
        return ""

    last = None
    for last in _POSTCODE_RE.finditer(text):
        pass
    # UK and Canadian postcodes are alphanumeric and in two halves — "OX16 4X",
    # "H9J 3K1" — so the digits-only hinge lands inside them and leaves half
    # the postcode in the town. Where one is present it is the better hinge.
    outward = None
    for outward in _OUTWARD_CODE_RE.finditer(text):
        pass
    if outward is not None and (last is None or outward.end() >= last.end()):
        return text[outward.end():].strip(" ,-")
    if last is not None:
        return text[last.end():].strip(" ,-")
    if any(ch.isdigit() for ch in text):
        caps = _TRAILING_CAPS_RE.search(text)
        if caps:
            return caps.group(1).strip()
        # Non-French sites write the town in mixed case with no postcode —
        # "1 Marsh Street Botany" — so the run of capitals never fires. What
        # marks the end of the street there is the street-type word, not a
        # number: everything after the last one is the town. A word list is
        # crude, but the alternative is taking the last word, and
        # "Pitstone, Buckinghamshire" is two.
        street = _STREET_SUFFIX_RE.search(text)
        if street:
            return text[street.end():].strip(" ,.-") or text
        tail = re.split(r"\b\d[\w.\-/]*\b", text)[-1].strip(" ,.-")
        return tail or text
    return text


def _rows(page: str) -> list[str]:
    starts = [m.start() for m in _ITEM_START_RE.finditer(page or "")]
    bounds = starts[1:] + [len(page or "")]
    return [page[a:b] for a, b in zip(starts, bounds)]


def _address(block: str) -> str:
    details = _DETAILS_RE.search(block)
    if not details:
        return ""
    cells = [_text(c) for c in _CELL_RE.findall(details.group(1))]
    return cells[-1] if cells else ""


def parse_jobs(page: str, employer: Employer) -> list[RawPosting]:
    """Turn one list page into postings. A row missing a link is skipped."""
    postings: list[RawPosting] = []
    for block in _rows(page):
        link = _LINK_RE.search(block)
        if not link:
            continue
        title = _TITLE_RE.search(block)
        reference = _REFERENCE_RE.search(block)
        address = _address(block)
        postings.append(RawPosting(
            source=NAME,
            url=urljoin(employer.ats_endpoint or employer.careers_url,
                        link.group(1)),
            title=html_module.unescape(title.group(1)) if title else "",
            employer_name=employer.name,
            city=city_from_address(address),
            # The board is group-wide and carries Botany and Chihuahua
            # alongside Massy. Most rows name their country and those are
            # believed; the ones that do not really are French, so the
            # employer's own stays the default.
            country=country_from_address(address) or employer.country,
            # Rows carry no advert. Left empty so enrichment fetches the
            # detail page rather than scoring a title against itself.
            description="",
            external_id=reference.group(1) if reference else "",
        ))
    return postings


def discover_families(page: str) -> list[tuple[str, int]]:
    """Every job family on the page with how many offers it holds.

    Read off the markup rather than configured: the ids are Safran's to change,
    and the counts say exactly how many pages each family needs, so nothing is
    fetched to find out where the end is.
    """
    found: dict[str, int] = {}
    for match in _FAMILY_RE.finditer(page or ""):
        found[match.group(1)] = int(match.group(2))
    return sorted(found.items(), key=lambda pair: -pair[1])


def reset_session(client) -> None:
    """Forget the facets the portal is holding for us.

    The selected facets live in the session cookie, not in the URL, so the
    only way to ask for one family alone is to arrive as a new visitor. A
    client with no cookie jar has nothing to forget.
    """
    cookies = getattr(client, "cookies", None)
    if cookies is not None and hasattr(cookies, "clear"):
        cookies.clear()


def _page_url(root: str, page: int, family: str = "") -> str:
    base = f"{root.rstrip('/')}{LIST_PATH}"
    if family:
        return (f"{base}?changefacet=1&facet_JobFamily={family}"
                f"&mode=list&page={page}")
    return f"{base}?all=1&mode=list&page={page}"


def fetch(
    employer: Employer, client, max_pages: int = DEFAULT_MAX_PAGES,
) -> list[RawPosting]:
    """Walk the board one job family at a time.

    The first request does double duty: it is page one of the flat listing and
    it carries the family links the rest of the walk is driven by. A portal
    with no families falls back to paging the flat listing, which wraps to page
    one past the end, so repetition is the stop signal there.
    """
    root = employer.ats_endpoint or employer.careers_url
    if not root:
        return []

    by_url: dict[str, RawPosting] = {}

    def absorb(page_text: str) -> int:
        fresh = [p for p in parse_jobs(page_text, employer)
                 if p.url and p.url not in by_url]
        for posting in fresh:
            by_url[posting.url] = posting
        return len(fresh)

    def get(page: int, family: str = "") -> str:
        response = client.get(_page_url(root, page, family))
        response.raise_for_status()
        return response.text

    first = get(1)
    absorb(first)
    budget = max_pages - 1

    families = discover_families(first)
    if not families:
        page = 1
        while budget > 0:
            page += 1
            budget -= 1
            if not absorb(get(page)):
                break
        return list(by_url.values())

    for family, count in families:
        # `changefacet=1` adds a facet to the session rather than replacing
        # it, so without dropping the cookies every family after the first is
        # filtered by its predecessors as well. Live, that silently returned
        # 1133 of 3805 offers.
        reset_session(client)
        # The count is exact, so the last page is known before asking for it.
        for page in range(1, -(-count // PAGE_SIZE) + 1):
            if budget <= 0:
                return list(by_url.values())
            budget -= 1
            text = get(page, family)
            absorb(text)
            if not _rows(text):
                break
    return list(by_url.values())
