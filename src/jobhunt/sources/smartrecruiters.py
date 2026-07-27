"""SmartRecruiters boards (ALTEN, Assystem, Scalian).

This is the consulting channel. Engineering consultancies place people into
Airbus, Thales, ESA and the OEMs, and they advertise most heavily on LinkedIn
— which has no public API and forbids scraping. The same postings are on their
own SmartRecruiters board, and that has an unauthenticated JSON API with real
structured fields, so the consulting market is reachable without touching
LinkedIn at all.

Two things matter here. These boards are large — a thousand postings each,
almost all irrelevant — so fetch pages through them and let the matcher
discard the rest, exactly as with any other source. And the location is
per-posting: a consultancy staffs across a dozen countries, so falling back to
the employer's headquarters would file UK and Indian postings as French ones.

`ats_endpoint` holds the company identifier, not a URL.
"""

from __future__ import annotations

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "smartrecruiters"
API_ROOT = "https://api.smartrecruiters.com/v1/companies"
BOARD_ROOT = "https://jobs.smartrecruiters.com"
PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 15


def postings_url(endpoint: str) -> str:
    return f"{API_ROOT}/{endpoint}/postings"


def _label(entry: dict, key: str) -> str:
    return ((entry.get(key) or {}).get("label") or "").strip()


def _apply_url(entry: dict, employer: Employer) -> str:
    """The public board page. The API's own `ref` is a JSON document, not
    something a person can apply on."""
    identifier = (entry.get("company") or {}).get("identifier", "")
    job_id = entry.get("id", "")
    if identifier and job_id:
        return f"{BOARD_ROOT}/{identifier}/{job_id}"
    return employer.careers_url


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    postings = []
    for entry in (payload or {}).get("content", []) or []:
        location = entry.get("location") or {}
        title = entry.get("name") or ""
        # Country arrives lower-case ("fr"); the rest of the tool uses ISO codes.
        country = (location.get("country") or "").upper() or employer.country
        description = " · ".join(filter(None, [
            title, _label(entry, "department"), _label(entry, "function"),
            _label(entry, "experienceLevel"), _label(entry, "typeOfEmployment"),
        ]))
        postings.append(RawPosting(
            source=NAME,
            url=_apply_url(entry, employer),
            title=title,
            employer_name=employer.name,
            city=location.get("city") or "",
            country=country,
            description=description,
            external_id=str(entry.get("id", "")),
        ))
    return postings


def fetch(employer: Employer, client, max_pages: int = DEFAULT_MAX_PAGES
          ) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []

    url = postings_url(employer.ats_endpoint)
    found: list[RawPosting] = []
    for page in range(max_pages):
        response = client.get(
            url, params={"limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
        )
        response.raise_for_status()
        payload = response.json()
        batch = parse_jobs(payload, employer)
        found.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    return found
