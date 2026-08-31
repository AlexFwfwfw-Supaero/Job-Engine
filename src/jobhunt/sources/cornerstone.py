"""Cornerstone OnDemand career sites (GMV, OHB).

GMV is the largest dedicated GNSS house on the watchlist and its board sat
unpolled for weeks because every request answered 401. The missing piece is an
Authorization header — but the token is not a credential. The career-site page
embeds an anonymous JWT in a `csod.context` block and hands it to every
visitor; this module reads the page the same way a browser does, then uses the
token for the search call. Nothing is logged into and nothing is bypassed.

Two details cost real time to find. The search endpoint is POST, not GET — a
GET returns 405 — and it rejects any body without `cultureName`, which is the
sort of thing the API tells you only if you read the 400 it sends back.

`ats_endpoint` holds the career-site home URL, because the corp code and the
site id both live in it and asking for them separately invites mismatches.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from jobhunt.models import Employer
from jobhunt.posting_text import strip_html
from jobhunt.sources.base import RawPosting

NAME = "cornerstone"
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 20
# The API is culture-scoped and 400s without this.
CULTURE_NAME = "en-US"
CULTURE_ID = 1

_SITE_RE = re.compile(r"/ux/ats/careersite/(?P<site>\d+)/")
_CONTEXT_RE = re.compile(r"csod\.context\s*=\s*(\{.*?\})\s*;", re.S)


@dataclass(frozen=True)
class Site:
    host: str
    corp: str
    site_id: int

    @property
    def home_url(self) -> str:
        return (f"https://{self.host}/ux/ats/careersite/{self.site_id}/home"
                f"?c={self.corp}")

    @property
    def search_url(self) -> str:
        return f"https://{self.host}/services/x/career-site/v1/search"

    def requisition_url(self, requisition_id) -> str:
        return (f"https://{self.host}/ux/ats/careersite/{self.site_id}"
                f"/home/requisition/{requisition_id}?c={self.corp}")


def parse_endpoint(url: str) -> Site:
    """Read host, corp code and site id out of a career-site URL."""
    parsed = urlparse(url)
    match = _SITE_RE.search(parsed.path or "")
    corp = (parse_qs(parsed.query or "").get("c") or [""])[0]
    if not match or not parsed.netloc or not corp:
        raise ValueError(
            f"not a Cornerstone career-site URL (need /ux/ats/careersite/<id>/ "
            f"and ?c=<corp>): {url}"
        )
    return Site(host=parsed.netloc, corp=corp, site_id=int(match.group("site")))


def token_from_page(html: str) -> str:
    """The anonymous JWT the career-site page gives every visitor."""
    match = _CONTEXT_RE.search(html or "")
    if match:
        try:
            token = (json.loads(match.group(1)) or {}).get("token")
        except json.JSONDecodeError:
            token = None
        if token:
            return str(token)
    raise ValueError("no csod.context token in the career-site page")


def _location(entry: dict, employer: Employer) -> tuple[str, str]:
    locations = entry.get("locations") or []
    first = locations[0] if locations else {}
    return first.get("city") or "", first.get("country") or employer.country


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    site = parse_endpoint(employer.ats_endpoint)
    data = (payload or {}).get("data") or {}
    postings = []
    for entry in data.get("requisitions", []) or []:
        city, country = _location(entry, employer)
        title = entry.get("displayJobTitle") or ""
        requisition_id = entry.get("requisitionId", "")
        postings.append(RawPosting(
            source=NAME,
            url=site.requisition_url(requisition_id),
            title=title,
            employer_name=employer.name,
            city=city,
            country=country,
            description=title,
            external_id=str(requisition_id),
        ))
    return postings


_REQUISITION_RE = re.compile(
    r"^(?P<host>[a-z0-9-]+\.csod\.com)/ux/ats/careersite/\d+/home/requisition/"
    r"(?P<id>\d+)")


def job_details_url(url: str) -> str | None:
    """Turn a browsable Cornerstone job URL into its detail endpoint.

    The advert is not in the page. Cornerstone renders it in JavaScript, so
    fetching the stored URL returns twenty kilobytes of chrome and no
    description — the same trap Workday sets, and the reason fourteen OHB
    postings sat unread. The service the page itself calls is under
    `services/x/job-requisition`, which is not the base the search uses.
    """
    parsed = urlparse(url)
    match = _REQUISITION_RE.match(f"{parsed.netloc}{parsed.path}")
    if not match:
        return None
    return (f"https://{match.group('host')}/services/x/job-requisition/v2/"
            f"requisitions/{match.group('id')}/jobDetails"
            f"?cultureId={CULTURE_ID}")


def posting_text(url: str, client) -> str:
    """The advert for one posting, as plain text.

    Takes the anonymous token the same way `fetch` does: the detail service
    401s without it, and it is the same JWT the career site hands to every
    visitor.
    """
    site = parse_endpoint(url)
    page = client.get(site.home_url)
    page.raise_for_status()

    response = client.get(
        job_details_url(url),
        headers={"Authorization": f"Bearer {token_from_page(page.text)}"},
    )
    response.raise_for_status()
    payload = response.json() or {}
    data = payload.get("data", payload) or {}
    return strip_html(data.get("externalDescription") or "")


def fetch(
    employer: Employer,
    client,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []

    site = parse_endpoint(employer.ats_endpoint)
    page = client.get(site.home_url)
    page.raise_for_status()
    headers = {"Authorization": f"Bearer {token_from_page(page.text)}",
               "Content-Type": "application/json"}

    found: list[RawPosting] = []
    for number in range(1, max_pages + 1):
        response = client.post(
            site.search_url,
            params={"c": site.corp},
            json={"careerSiteId": site.site_id, "cultureName": CULTURE_NAME,
                  "cultureId": CULTURE_ID, "pageNumber": number,
                  "pageSize": page_size, "searchText": ""},
            headers=headers,
        )
        response.raise_for_status()
        batch = parse_jobs(response.json(), employer)
        found.extend(batch)
        if len(batch) < page_size:
            break
    return found
