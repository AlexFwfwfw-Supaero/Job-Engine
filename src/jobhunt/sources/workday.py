from __future__ import annotations

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


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    """Turn a Workday CXS response into postings.

    Every field is treated as optional: live responses omit locationsText on
    some postings, and a missing field must not lose the whole job.
    """
    postings: list[RawPosting] = []
    for job in (payload or {}).get("jobPostings", []) or []:
        bullets = job.get("bulletFields") or []
        postings.append(RawPosting(
            source=NAME,
            url=_job_url(job.get("externalPath", ""), employer),
            title=job.get("title", "") or "",
            employer_name=employer.name,
            city=job.get("locationsText", "") or "",
            country=employer.country,
            description=job.get("title", "") or "",
            external_id=bullets[0] if bullets else "",
        ))
    return postings


def fetch(
    employer: Employer,
    client,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    search_text: str = "",
) -> list[RawPosting]:
    """Page through a Workday tenant's public job feed.

    Stops at the first short page, so a tenant with few jobs costs one request.
    Pulls broadly and leaves relevance to the matcher — Workday's own search
    ranks 'Manager Commercial and Contracts' highly for 'navigation'.
    """
    if not employer.ats_endpoint:
        return []

    found: list[RawPosting] = []
    for page in range(max_pages):
        response = client.post(
            employer.ats_endpoint,
            json={
                "appliedFacets": {},
                "limit": page_size,
                "offset": page * page_size,
                "searchText": search_text,
            },
        )
        response.raise_for_status()
        payload = response.json()
        batch = parse_jobs(payload, employer)
        found.extend(batch)
        if len(batch) < page_size:
            break
    return found
