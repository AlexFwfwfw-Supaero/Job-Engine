from __future__ import annotations

import re
import unicodedata

from urllib.parse import urljoin

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "workday"
DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 10


def _job_url(external_path: str, employer: Employer) -> str:
    """Turn Workday's relative externalPath into a URL a human can open.

    The CXS endpoint the data comes from is not browsable, so links are built
    against the employer's public careers site instead.
    """
    base = employer.careers_url.rstrip("/")
    if not external_path:
        return employer.careers_url
    return urljoin(base + "/", external_path.lstrip("/"))


# "IT - Torino - C.so Francia". Only an uppercase two-letter code counts: the
# separator is common enough that "Rome - Via Tiburtina" would otherwise lose
# its city to a country that does not exist.
_COUNTRY_PREFIX_RE = re.compile(r"^(?P<country>[A-Z]{2})\s*-\s*(?P<rest>.+)$")


def _path_location(external_path: str) -> str:
    """`'/job/GB---Edinburgh/Systems-Engineer_R1'` -> `'GB - Edinburgh'`.

    Workday slugifies the location into the path with the separators tripled.
    """
    parts = [p for p in (external_path or "").split("/") if p]
    if len(parts) < 2 or parts[0] != "job":
        return ""
    # Order matters: a single "-" is a space inside a name ("Coldharbour-Lane"),
    # so the triple separators have to be taken out first or the pass for
    # single ones eats the separator this just created.
    return " - ".join(
        segment.replace("-", " ").strip() for segment in parts[1].split("---")
    ).strip()


# The sites a tenant names with no country in front of them. Airbus's tenant
# writes "Getafe Area" and "Stevenage" flat, so split_location finds nothing
# and every posting fell back to the employer's own DE — which put its Spanish
# and British sites into the results as German jobs, and landed a Stevenage
# graduate scheme on the shortlist behind the GB exclusion.
#
# Only unambiguous names are here, and only ones this watchlist actually sees.
# A town whose name is shared across countries (Newport, Melbourne, Toledo)
# stays out: the whole point of split_location is that a guess about country
# is worse than no answer, because the country exclusion acts on it.
_SITE_COUNTRY = {
    # United Kingdom
    "stevenage": "GB", "portsmouth": "GB", "yeovil": "GB", "basildon": "GB",
    "filton": "GB", "broughton": "GB", "newport": "GB", "luton": "GB",
    # Spain
    "getafe": "ES", "madrid": "ES", "sevilla": "ES", "seville": "ES",
    "albacete": "ES", "barajas": "ES", "tres cantos": "ES",
    # France
    "toulouse": "FR", "blagnac": "FR", "marignane": "FR", "elancourt": "FR",
    "marseille": "FR", "paris": "FR", "bordeaux": "FR", "nantes": "FR",
    # Germany
    "hamburg": "DE", "bremen": "DE", "manching": "DE", "ottobrunn": "DE",
    "friedrichshafen": "DE", "immenstaad": "DE", "backnang": "DE",
    "taufkirchen": "DE", "donauworth": "DE", "ulm": "DE", "munchen": "DE",
    # Elsewhere
    "montreal": "CA", "mirabel": "CA", "fort erie": "CA",
    "bangalore": "IN", "gdansk": "PL", "sevilla la nueva": "ES",
    "mobile": "US", "wichita": "US", "herndon": "US",
    # Safran's sites, for the same reason: its rows name a country in the
    # address, but the town is all that survives into storage, so a repair of
    # already-stored rows has only this to go on.
    "botany": "AU", "banbury": "GB", "cwmbran": "GB", "gloucester": "GB",
    "pitstone": "GB", "chennai": "IN", "hyderabad": "IN", "kirkland": "CA",
    "ajax": "CA", "redmond": "US", "irvine": "US", "rockford": "US",
    "garden grove": "US", "chihuahua": "MX", "queretaro": "MX",
    "shanghai": "CN", "murr": "DE", "herborn": "DE", "norderstedt": "DE",
    "heerbrugg": "CH", "herstal": "BE", "granada": "ES",
}

# "Getafe Area", "Mobile Area, AL", "Munchen Area" — Workday appends a region
# word and sometimes a state. The site name is what comes before it.
_SITE_SUFFIX_RE = re.compile(r"\s*(area|region)\b.*$", re.I)


def country_for_site(raw: str) -> str:
    """`'Getafe Area'` -> `'ES'`; `'Immenstaad am Bodensee'` -> `''`.

    Same contract as `split_location`: the ISO code when the site name is one
    this knows, an empty string otherwise, never a guess.
    """
    text = _SITE_SUFFIX_RE.sub("", (raw or "").strip()).strip(" ,-")
    if not text:
        return ""
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(ch)
    )
    return _SITE_COUNTRY.get(folded, "")


def split_location(raw: str) -> tuple[str, str]:
    """`'IT - Torino - C.so Francia'` -> `('Torino', 'IT')`.

    Workday carries no country field, so every posting on a tenant used to be
    stamped with the employer's own — which put Yeovil and Basildon in the
    results as Italian jobs, and Airbus's Getafe and Bristol postings in as
    German ones. Some tenants do state it, in the location string. Where they
    do it is believed; where they do not the caller keeps its default, because
    "Toulouse Area" says nothing about a country.
    """
    text = (raw or "").strip()
    match = _COUNTRY_PREFIX_RE.match(text)
    if not match:
        return text, ""
    # "DE - Darmstadt - ESOC": the site name after the town is not the town.
    return match.group("rest").split(" - ")[0].strip(), match.group("country")


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    """Turn a Workday CXS response into postings.

    Every field is treated as optional: live responses omit locationsText on
    some postings, and a missing field must not lose the whole job.
    """
    postings: list[RawPosting] = []
    for job in (payload or {}).get("jobPostings", []) or []:
        bullets = job.get("bulletFields") or []
        external_path = job.get("externalPath", "") or ""
        city, country = split_location(job.get("locationsText", "") or "")
        if not country:
            # "2 Locations" states nothing, but the path keeps the primary
            # site: /job/GB---Edinburgh/Systems-Engineer_R1. Five of
            # Leonardo's first ten stored jobs were British, filed as Italian.
            path_city, path_country = split_location(_path_location(external_path))
            if path_country:
                city, country = path_city, path_country
        if not country:
            # Neither the location nor the path names one. Some tenants never
            # do — Airbus writes "Stevenage" flat — so the site name itself is
            # the last piece of evidence before falling back to the employer.
            country = (country_for_site(city)
                       or country_for_site(_path_location(external_path)))
        postings.append(RawPosting(
            source=NAME,
            url=_job_url(external_path, employer),
            title=job.get("title", "") or "",
            employer_name=employer.name,
            city=city,
            country=country or employer.country,
            description=job.get("title", "") or "",
            external_id=bullets[0] if bullets else "",
        ))
    return postings


def _fetch_term(
    employer: Employer, client, term: str, page_size: int, max_pages: int
) -> list[RawPosting]:
    found: list[RawPosting] = []
    for page in range(max_pages):
        response = client.post(
            employer.ats_endpoint,
            json={
                "appliedFacets": {},
                "limit": page_size,
                "offset": page * page_size,
                "searchText": term,
            },
        )
        response.raise_for_status()
        batch = parse_jobs(response.json(), employer)
        found.extend(batch)
        if len(batch) < page_size:
            break
    return found


def fetch(
    employer: Employer,
    client,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    search_terms: list[str] | None = None,
) -> list[RawPosting]:
    """Collect postings from a Workday tenant, one query per search term.

    A large tenant holds thousands of jobs across every discipline, so pulling
    the feed broadly and filtering locally would need hundreds of requests and
    still miss the relevant roles — a live poll of the first 60 Airbus jobs
    returned cabin installers and no navigation roles at all.

    Querying per term lets the tenant narrow server-side on our vocabulary;
    the matcher then supplies precision, because Workday's own ranking puts
    'Manager Commercial and Contracts' near the top for 'navigation'.

    A term that fails is skipped rather than losing the whole sweep.
    """
    if not employer.ats_endpoint:
        return []

    terms = list(search_terms) if search_terms else [""]
    by_url: dict[str, RawPosting] = {}
    for term in terms:
        try:
            for posting in _fetch_term(
                employer, client, term, page_size, max_pages
            ):
                by_url.setdefault(posting.url, posting)
        except Exception:
            continue
    return list(by_url.values())
