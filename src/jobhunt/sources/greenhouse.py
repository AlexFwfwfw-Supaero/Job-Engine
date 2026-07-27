"""Greenhouse job boards (Isar Aerospace, Rocket Factory Augsburg).

Greenhouse publishes every board at a documented, unauthenticated JSON
endpoint keyed by a board token, with no pagination — one request is the whole
board. `ats_endpoint` therefore holds the token, not a URL.

The one wrinkle is location: Greenhouse stores it as a single free-text string
like "Kiruna, Norrbotten, Sweden" rather than structured fields, so the trailing
country name is mapped back to an ISO code. Without that the matcher cannot
apply the excluded-country rule, and UK postings — which this search excludes —
would be stored as if they were German.
"""

from __future__ import annotations

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "greenhouse"
API_ROOT = "https://boards-api.greenhouse.io/v1/boards"

COUNTRY_CODES = {
    "austria": "AT", "belgium": "BE", "czechia": "CZ", "czech republic": "CZ",
    "denmark": "DK", "finland": "FI", "france": "FR", "germany": "DE",
    "greece": "GR", "ireland": "IE", "italy": "IT", "luxembourg": "LU",
    "netherlands": "NL", "the netherlands": "NL", "norway": "NO",
    "poland": "PL", "portugal": "PT", "romania": "RO", "spain": "ES",
    "sweden": "SE", "switzerland": "CH",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "united states": "US", "usa": "US",
}


def board_url(endpoint: str) -> str:
    if endpoint.startswith("http"):
        return endpoint
    return f"{API_ROOT}/{endpoint}/jobs"


def _split_location(raw: str, employer: Employer) -> tuple[str, str]:
    """'Kiruna, Norrbotten, Sweden' -> ('Kiruna', 'SE').

    Several locations may be joined by ';'; the first is taken, since the
    posting is one job and the extra sites do not change its relevance.
    """
    if not raw:
        return "", employer.country
    first = raw.split(";")[0]
    parts = [p.strip() for p in first.split(",") if p.strip()]
    if not parts:
        return "", employer.country
    country = COUNTRY_CODES.get(parts[-1].lower(), employer.country)
    city = parts[0] if len(parts) > 1 else parts[0]
    return city, country


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    postings = []
    for job in (payload or {}).get("jobs", []) or []:
        city, country = _split_location(
            (job.get("location") or {}).get("name", ""), employer
        )
        title = job.get("title", "") or ""
        postings.append(RawPosting(
            source=NAME,
            url=job.get("absolute_url") or employer.careers_url,
            title=title,
            employer_name=employer.name,
            city=city,
            country=country,
            description=title,
            external_id=str(job.get("id", "")),
        ))
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []
    response = client.get(board_url(employer.ats_endpoint))
    response.raise_for_status()
    return parse_jobs(response.json(), employer)
