from __future__ import annotations

import html as html_lib
import re
from urllib.parse import quote, urljoin

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "euraxess"
BASE_URL = "https://euraxess.ec.europa.eu"
SEARCH_PATH = "/jobs/search"
DEFAULT_MAX_PAGES = 5

# Facet ids read off the live search page, 2026-07-26. EURAXESS ignores a
# plain ?keys= keyword parameter — verified by getting identical results for
# "GNSS" and for nonsense — so narrowing has to happen through facets, and
# final relevance is decided locally by the matcher.
ENGINEERING_FIELD = 164
TECHNOLOGY_FIELD = 401
FIRST_STAGE_RESEARCHER = 447

_ARTICLE_SPLIT = '<article class="ecl-content-item">'

_TITLE_RE = re.compile(
    r'ecl-content-block__title"><a\s+href="(/jobs/(\d+))"[^>]*?>\s*<span>(.*?)</span>',
    re.S,
)
_ORG_RE = re.compile(
    r'primary-meta-item"><a href="/partnering/organisations/[^"]*"[^>]*>(.*?)</a>',
    re.S,
)
_DESC_RE = re.compile(
    r'ecl-content-block__description">\s*<p>(.*?)</p>', re.S
)


def _text(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def build_search_url(
    page: int = 0,
    field: int = ENGINEERING_FIELD,
    first_stage_only: bool = False,
) -> str:
    """Build a faceted search URL.

    Facets are index-numbered (f[0], f[1]) in EURAXESS's Drupal search, and the
    brackets must stay percent-encoded or the filter is silently dropped.
    """
    facets = [f"f{quote('[0]')}=job_research_field{quote(':')}{field}"]
    if first_stage_only:
        facets.append(
            f"f{quote('[1]')}=job_research_profile{quote(':')}{FIRST_STAGE_RESEARCHER}"
        )
    if page:
        facets.append(f"page={page}")
    return f"{BASE_URL}{SEARCH_PATH}?" + "&".join(facets)


def parse_results(page_html: str, employer: Employer) -> list[RawPosting]:
    """Extract postings from a EURAXESS search results page.

    Each result is one <article class="ecl-content-item"> block. A block that
    does not yield a title and id is skipped rather than half-parsed.
    """
    postings: list[RawPosting] = []
    for block in (page_html or "").split(_ARTICLE_SPLIT)[1:]:
        title_match = _TITLE_RE.search(block)
        if not title_match:
            continue
        path, job_id, raw_title = title_match.groups()

        org_match = _ORG_RE.search(block)
        desc_match = _DESC_RE.search(block)
        description = _text(desc_match.group(1)) if desc_match else ""
        title = _text(raw_title)

        postings.append(RawPosting(
            source=NAME,
            url=urljoin(BASE_URL, path),
            title=title,
            employer_name=_text(org_match.group(1)) if org_match else employer.name,
            description=f"{title}\n\n{description}".strip(),
            external_id=job_id,
        ))
    return postings


def fetch(
    employer: Employer,
    client,
    max_pages: int = DEFAULT_MAX_PAGES,
    field: int = ENGINEERING_FIELD,
    first_stage_only: bool = False,
) -> list[RawPosting]:
    """Walk the faceted result pages until one comes back empty."""
    found: list[RawPosting] = []
    for page in range(max_pages):
        response = client.get(
            build_search_url(page=page, field=field, first_stage_only=first_stage_only)
        )
        response.raise_for_status()
        batch = parse_results(response.text, employer)
        if not batch:
            break
        found.extend(batch)
    return found
