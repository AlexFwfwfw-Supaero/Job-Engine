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
