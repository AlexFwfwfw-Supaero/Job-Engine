"""Breezy HR job boards (Telespazio Belgium).

Breezy publishes the whole board as JSON at `<company>.breezy.hr/json` with no
pagination and no authentication, so a single request is the entire fetch.
Every field is treated as optional: the feed omits department and salary on
some positions, and a missing field must not lose the posting.
"""

from __future__ import annotations

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "breezy"


def _location(entry: dict, employer: Employer) -> tuple[str, str]:
    location = entry.get("location") or {}
    country = (location.get("country") or {}).get("id") or employer.country
    return location.get("city") or "", country


def parse_jobs(payload, employer: Employer) -> list[RawPosting]:
    if not isinstance(payload, list):
        return []
    postings = []
    for entry in payload:
        city, country = _location(entry, employer)
        title = entry.get("name") or ""
        postings.append(RawPosting(
            source=NAME,
            url=entry.get("url") or employer.careers_url,
            title=title,
            employer_name=employer.name,
            city=city,
            country=country,
            description=" ".join(filter(None, [title, entry.get("department") or ""])),
            external_id=entry.get("id") or "",
        ))
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []
    response = client.get(employer.ats_endpoint)
    response.raise_for_status()
    return parse_jobs(response.json(), employer)
