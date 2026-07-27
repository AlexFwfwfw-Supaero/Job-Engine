"""Recruitee boards (ATG Europe).

The ESA-contractor channel. Most of the engineering work physically done at
ESTEC, ESOC and ESRIN is advertised by support contractors rather than by ESA,
so a watchlist that stops at jobs.esa.int misses the majority of roles at ESA
sites. These contractors are small enough to run hosted boards, and Recruitee
publishes the whole board as unauthenticated JSON in one request.

The department field is worth keeping: on a contractor board "ESA/ESTEC" is
the strongest signal a posting carries about where the work happens.

`ats_endpoint` holds the company slug, not a URL.
"""

from __future__ import annotations

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "recruitee"


def board_url(endpoint: str) -> str:
    if endpoint.startswith("http"):
        return endpoint
    return f"https://{endpoint}.recruitee.com/api/offers/"


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    postings = []
    for entry in (payload or {}).get("offers", []) or []:
        title = entry.get("title") or ""
        postings.append(RawPosting(
            source=NAME,
            url=entry.get("careers_url") or employer.careers_url,
            title=title,
            employer_name=employer.name,
            city=entry.get("city") or "",
            country=entry.get("country_code") or employer.country,
            description=" · ".join(filter(None, [
                title, entry.get("department") or "",
                entry.get("employment_type_code") or "",
            ])),
            external_id=str(entry.get("id", "")),
        ))
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []
    response = client.get(board_url(employer.ats_endpoint))
    response.raise_for_status()
    return parse_jobs(response.json(), employer)
