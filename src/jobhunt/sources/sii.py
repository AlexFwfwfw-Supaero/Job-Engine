"""SII Group's Drupal careers site.

SII has no job API. What it does have — unlike most consultancy sites, which
render their listings in JavaScript — is a server-side listing, so the
postings are already in the HTML a plain fetch returns. This module reads that
public page rather than leaving a French aerospace consultancy invisible.

Being HTML rather than JSON, it is the most fragile source here: a theme
change breaks it. It fails by returning nothing, which the poll report shows
as `seen 0` — the same signal that caught the Beyond Gravity regex bug.

`ats_endpoint` holds the listing URL.
"""

from __future__ import annotations

import html as html_module
import re

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "sii"
DEFAULT_MAX_PAGES = 60

# Countries the site spells out in full, in its own wording.
COUNTRY_CODES = {
    "france": "FR", "germany": "DE", "spain": "ES", "italy": "IT",
    "belgium": "BE", "netherlands": "NL", "luxembourg": "LU",
    "switzerland": "CH", "austria": "AT", "poland": "PL", "romania": "RO",
    "czech republic": "CZ", "czechia": "CZ", "slovakia": "SK",
    "hungary": "HU", "portugal": "PT", "morocco": "MA", "tunisia": "TN",
    "united kingdom": "GB", "uk": "GB", "canada": "CA", "chile": "CL",
    "colombia": "CO", "mexico": "MX", "united states": "US",
}

_ROW_RE = re.compile(r'<div class="views-row">(?P<row>.*?)(?=<div class="views-row">|\Z)', re.S)
_LINK_RE = re.compile(r'<a href="(?P<href>[^"]+)"\s*>\s*<h3>(?P<title>.*?)</h3>', re.S)
_ID_RE = re.compile(r"/node/(\d+)")


def _field(row: str, name: str) -> str:
    """The text of one Drupal field block, e.g. field--name-field-location."""
    match = re.search(
        rf'field--name-field-{name}\b.*?<div class="field__item">(?P<v>.*?)</div>',
        row, re.S,
    )
    if not match:
        return ""
    return html_module.unescape(re.sub(r"<[^>]+>", " ", match.group("v"))).strip()


def _origin(endpoint: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


def _location(raw: str, employer: Employer) -> tuple[str, str]:
    """'Toulouse, France' -> ('Toulouse', 'FR')."""
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        return "", employer.country
    country = COUNTRY_CODES.get(parts[-1].lower(), employer.country)
    return parts[0], country


def parse_jobs(html: str, employer: Employer) -> list[RawPosting]:
    origin = _origin(employer.ats_endpoint or employer.careers_url)
    postings = []
    for row_match in _ROW_RE.finditer(html or ""):
        row = row_match.group("row")
        link = _LINK_RE.search(row)
        if not link:
            continue
        title = html_module.unescape(link.group("title")).strip()
        href = link.group("href")
        city, country = _location(_field(row, "location"), employer)
        node = _ID_RE.search(href)
        postings.append(RawPosting(
            source=NAME,
            url=href if href.startswith("http") else f"{origin}{href}",
            title=title,
            employer_name=employer.name,
            city=city,
            country=country,
            description=" · ".join(filter(None, [
                title, _field(row, "job"), _field(row, "profile"),
                _field(row, "contract"),
            ])),
            external_id=node.group(1) if node else "",
        ))
    return postings


def fetch(employer: Employer, client,
          max_pages: int = DEFAULT_MAX_PAGES) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []

    found: list[RawPosting] = []
    # Drupal's infinite scroll is ?page=N, zero-based, and an exhausted
    # listing renders no rows at all rather than reporting a total.
    for page in range(max_pages):
        response = client.get(employer.ats_endpoint, params={"page": page})
        response.raise_for_status()
        batch = parse_jobs(response.text, employer)
        if not batch:
            break
        found.extend(batch)
    return found
