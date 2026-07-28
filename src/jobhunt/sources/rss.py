"""RSS job feeds (DLR).

The last resort, for a site that has no API and renders its listing in
JavaScript. DLR is exactly that: its SuccessFactors tenant answers /search/,
/searchjobs/ and /go/ with a page containing no job at all, so the category
RSS feed is the only machine-readable view of it.

The feed is capped at the ten newest postings. That makes this a change
detector rather than a complete source — polling it weekly sees only what was
published since the last look, and a quiet fortnight can hide older openings.
The employer entry says so, because a source that silently under-reports is
worse than no source.

It earns its place anyway. DLR houses the Institute of Communications and
Navigation, and the feed carries the **full advert text**, so what it does
catch arrives readable by the model with no second fetch.

`ats_endpoint` holds the feed URL.
"""

from __future__ import annotations

import html as html_module
import re
from xml.etree import ElementTree

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "rss"

# The feed has no location field. The site appends the site name in brackets
# at the end of the title — but German titles also carry "(w/m/d)" mid-string,
# so only a trailing bracket counts, and only one without slashes in it.
_TRAILING_PLACE_RE = re.compile(r"\s*\(([^()/]+)\)\s*$")
_ID_RE = re.compile(r"/(\d+)/?$")
_TAG_RE = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    stripped = _TAG_RE.sub(" ", html_module.unescape(raw or ""))
    return re.sub(r"[ \t]+", " ", stripped).strip()


def split_title(raw: str) -> tuple[str, str]:
    """`'Doktorand (w/m/d) - GNSS (Oberpfaffenhofen)'` -> title, city."""
    title = (raw or "").strip()
    match = _TRAILING_PLACE_RE.search(title)
    if not match:
        return title, ""
    return title[: match.start()].strip(), match.group(1).strip()


def parse_jobs(feed: str, employer: Employer) -> list[RawPosting]:
    try:
        root = ElementTree.fromstring((feed or "").strip())
    except ElementTree.ParseError as exc:
        raise ValueError(f"malformed RSS from {employer.ats_endpoint}: {exc}")

    postings = []
    for item in root.iter("item"):
        title, city = split_title((item.findtext("title") or ""))
        link = (item.findtext("link") or "").strip()
        body = _text(item.findtext("description") or "")
        node = _ID_RE.search(link)
        postings.append(RawPosting(
            source=NAME,
            url=link or employer.careers_url,
            title=title,
            employer_name=employer.name,
            city=city,
            # The feed carries no country, and these are single-country
            # institutions; the employer's own is the honest default.
            country=employer.country,
            description=" · ".join(filter(None, [title, body])),
            external_id=node.group(1) if node else "",
        ))
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []
    response = client.get(employer.ats_endpoint)
    response.raise_for_status()
    return parse_jobs(response.text, employer)
