"""SAP SuccessFactors recruiting sites (ESA, DLR).

SuccessFactors ships no public JSON API — the career sites are server-rendered
HTML — so this parses the search results page. The markup is stable in the
ways that matter: every posting is an anchor of class `jobTitle-link` whose
href carries the job id, and the workplace sits in a div keyed by that id.

Each job is emitted three times in the page, once per responsive layout, so
parsing deduplicates by job id rather than trusting the match count.
"""

from __future__ import annotations

import html as html_module
import re
from urllib.parse import unquote, urljoin

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "successfactors"
PAGE_SIZE = 25
DEFAULT_MAX_PAGES = 8

# The job path may or may not carry a site segment: ESA serves /job/…, while
# Beyond Gravity serves /BeyondGravity/job/…. Anchoring on '/job/' alone
# returned zero postings for a whole tenant, which is indistinguishable from
# an empty job market — hence the optional leading segment.
_JOB_RE = re.compile(
    r'class="jobTitle-link[^"]*"[^>]*'
    r'href="(?P<path>/(?:[A-Za-z0-9_-]+/)?job/[^"]*/(?P<id>\d+)/)"\s*>'
    r"\s*(?P<title>.*?)\s*</a>",
    re.S,
)
_TOTAL_RE = re.compile(r"of\s+(\d+)\s+Job", re.I)


def build_search_url(endpoint: str, startrow: int = 0) -> str:
    base = endpoint.rstrip("/")
    return (
        f"{base}/search/?q=&sortColumn=referencedate&sortDirection=desc"
        f"&startrow={startrow}"
    )


def total_jobs(page: str) -> int | None:
    match = _TOTAL_RE.search(page or "")
    return int(match.group(1)) if match else None


def _workplace(page: str, job_id: str) -> str:
    pattern = re.compile(
        rf'id="job-{job_id}-desktop-section-multilocation-value"\s*>\s*([^<\n]*)'
    )
    match = pattern.search(page)
    return html_module.unescape(match.group(1)).strip() if match else ""


def _split_workplace(workplace: str, employer: Employer) -> tuple[str, str]:
    """'Noordwijk, NL' -> ('Noordwijk', 'NL'); anything else keeps the default."""
    if "," in workplace:
        city, _, country = workplace.rpartition(",")
        return city.strip(), country.strip() or employer.country
    return workplace, employer.country


def parse_jobs(page: str, employer: Employer) -> list[RawPosting]:
    base = employer.ats_endpoint or employer.careers_url
    postings: dict[str, RawPosting] = {}
    for match in _JOB_RE.finditer(page or ""):
        job_id = match.group("id")
        if job_id in postings:
            continue
        title = html_module.unescape(re.sub(r"<[^>]+>", "", match.group("title")))
        city, country = _split_workplace(_workplace(page, job_id), employer)
        postings[job_id] = RawPosting(
            source=NAME,
            # The captured path is absolute and already carries the site
            # segment when there is one, so it is joined against the origin
            # rather than appended to the endpoint.
            url=urljoin(base, unquote(match.group("path"))),
            title=title.strip(),
            employer_name=employer.name,
            city=city,
            country=country,
            description=title.strip(),
            external_id=job_id,
        )
    return list(postings.values())


def fetch(
    employer: Employer, client, max_pages: int = DEFAULT_MAX_PAGES
) -> list[RawPosting]:
    """Walk the search results until a page yields nothing new.

    Stopping on an empty page rather than on a computed page count keeps the
    fetch correct when the advertised total and the rendered rows disagree,
    which SuccessFactors sites do whenever a posting expires mid-crawl.
    """
    if not employer.ats_endpoint:
        return []
    by_url: dict[str, RawPosting] = {}
    for page_number in range(max_pages):
        response = client.get(build_search_url(employer.ats_endpoint,
                                               page_number * PAGE_SIZE))
        response.raise_for_status()
        batch = parse_jobs(response.text, employer)
        fresh = [p for p in batch if p.url not in by_url]
        if not fresh:
            break
        for posting in fresh:
            by_url[posting.url] = posting
    return list(by_url.values())
