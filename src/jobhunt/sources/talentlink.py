"""TalentLink / Lumesse career portals (ONERA).

ONERA's careers page embeds a TalentLink widget, so none of its postings are
in the HTML of rejoindre.onera.fr — the page ships an empty container and the
widget fills it. The widget's own REST call is reachable, and the guest login
it uses is published in the widget's JavaScript: every visitor gets the same
one. Nothing here is a private credential.

Two details make this fail quietly if you get them wrong. The credentials go
in literal `username` and `password` headers — sending them as HTTP Basic
answers 403. And the search answers 500 unless both `sortBy` and `sortOrder`
are present, which reads like a server fault rather than a bad request.

The response carries the whole advert in `customFields`, so a posting from
here needs no second fetch before the model can read it.

`ats_endpoint` holds the site technical id, optionally prefixed with a host
for portals on another TalentLink shard: `emea5.recruitmentplatform.com|<id>`.
"""

from __future__ import annotations

import html as html_module
import re

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "talentlink"
DEFAULT_HOST = "emea3.recruitmentplatform.com"
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 20
# Any value works; the API rejects the request when either is missing.
SORT_BY = "publicationDate"
SORT_ORDER = "desc"

_TAG_RE = re.compile(r"<[^>]+>")


def parse_endpoint(endpoint: str) -> tuple[str, str]:
    """`'<id>'` or `'<host>|<id>'` -> (host, site technical id)."""
    raw = (endpoint or "").strip()
    if not raw:
        raise ValueError("talentlink needs a site technical id in ats_endpoint")
    host, _, tech_id = raw.rpartition("|")
    return (host or DEFAULT_HOST), tech_id


def guest_headers(tech_id: str, language: str = "fr") -> dict:
    """The anonymous login the widget itself uses, as plain headers."""
    return {
        "username": f"{tech_id}:guest:FO",
        "password": "guest",
        "lumesse-language": language,
        "Content-Type": "application/json",
    }


def _text(raw: str) -> str:
    stripped = _TAG_RE.sub(" ", html_module.unescape(raw or ""))
    return re.sub(r"\s+", " ", stripped).strip()


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    postings = []
    for entry in (payload or {}).get("jobs", []) or []:
        fields = entry.get("jobFields") or {}
        title = fields.get("jobTitle") or fields.get("SJOBTITLE") or ""
        body = " ".join(
            _text(f.get("content", "")) for f in entry.get("customFields") or []
        )
        postings.append(RawPosting(
            source=NAME,
            url=fields.get("applicationUrl") or employer.careers_url,
            title=title,
            employer_name=employer.name,
            city=fields.get("SLOCATION") or "",
            # The record carries a region label, not a country code. These are
            # single-country institutions, so the employer's own is honest.
            country=employer.country,
            description=" · ".join(filter(None, [
                title, fields.get("CONTRACTTYPLABEL") or "",
                fields.get("REGLABEL") or "", body,
            ])),
            external_id=str(entry.get("id", "")),
        ))
    return postings


def fetch(
    employer: Employer,
    client,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []

    host, tech_id = parse_endpoint(employer.ats_endpoint)
    url = f"https://{host}/fo/rest/jobs"
    headers = guest_headers(tech_id)

    found: list[RawPosting] = []
    for page in range(max_pages):
        response = client.post(
            url,
            params={"firstResult": page * page_size, "maxResults": page_size,
                    "sortBy": SORT_BY, "sortOrder": SORT_ORDER},
            json={"searchCriteria": {"criteria": []}},
            headers=headers,
        )
        response.raise_for_status()
        batch = parse_jobs(response.json(), employer)
        found.extend(batch)
        if len(batch) < page_size:
            break
    return found
