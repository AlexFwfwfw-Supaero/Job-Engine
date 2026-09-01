"""softgarden career widgets (Fraunhofer IIS).

Fraunhofer institutes publish through softgarden, and the institute's own
careers page embeds the board as a widget rather than listing it. The widget
is Apache Wicket — server-rendered — so the postings are in the HTML a plain
fetch returns, and no JavaScript has to run.

Two things this module deliberately does not do. It does not pass the
institute's own widget configuration, which is a base64 blob pinning an
audience and category filter; asking for the widget bare returns the same
board, and a filter copied from a marketing page would silently narrow the
search later. And it does not paginate: the widget answers with every posting
at once, which was checked by asking for pages, offsets and limits and
getting the identical 29 rows back each time.

The rows carry a title, a city and an **audience** — softgarden's own word for
seniority. That is better evidence than guessing from the title, so a
"Werkstudent*in" row is stored as a student rather than priced as a graduate
engineer.

`ats_endpoint` holds the tenant name (`iisfraunhofer`), or a full widget URL
for a tenant publishing under its own host.
"""

from __future__ import annotations

import html as html_module
import re

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "softgarden"
WIDGET_PATH = "/en/widgets/jobs"

# One row. Delimited by where the next one starts, because the block holds
# nested divs and matching to the first </div> stops inside the title.
_ROW_RE = re.compile(
    r'<div class="matchElement" id="job_id_(?P<id>\d+)">(?P<row>.*?)'
    r'(?=<div class="matchElement"|\Z)',
    re.S,
)
_LINK_RE = re.compile(r'href="(?P<href>[^"]*?job/\d+/[^"]*)"', re.S)
_TITLE_RE = re.compile(
    r'<div[^>]*class="matchValue title">.*?<a[^>]*>(?P<title>.*?)</a>', re.S)
_AUDIENCE_RE = re.compile(
    r'<div class="matchValue audience">(?P<audience>.*?)</div>', re.S)
_CITY_RE = re.compile(
    r'<span class="location-view-item">(?P<city>.*?)</span>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# softgarden's own audience labels. Anything else — "Young Professional",
# "Experienced Professional" — is an ordinary post and keeps the default.
_STUDENT_AUDIENCES = ("student", "pupil", "apprentice", "schüler", "praktikant")


def _text(raw: str) -> str:
    plain = _TAG_RE.sub(" ", html_module.unescape(raw or ""))
    return re.sub(r"\s+", " ", plain).strip()


def widget_url(endpoint: str) -> str:
    """`'iisfraunhofer'` -> the tenant's widget URL; a URL passes through."""
    raw = (endpoint or "").strip().rstrip("/")
    if raw.startswith("http"):
        return raw
    return f"https://{raw}.softgarden.io{WIDGET_PATH}"


def _level(audience: str) -> str:
    folded = audience.lower()
    return "student" if any(a in folded for a in _STUDENT_AUDIENCES) else "junior"


def parse_jobs(html: str, employer: Employer) -> list[RawPosting]:
    origin = widget_url(employer.ats_endpoint).split("/en/")[0]
    postings = []
    for match in _ROW_RE.finditer(html or ""):
        row = match.group("row")
        title = _TITLE_RE.search(row)
        link = _LINK_RE.search(row)
        if not title or not link:
            continue

        # The href is relative to the widget and carries a tracking id. The
        # id changes between fetches, so leaving it on would make every poll
        # look like it had found a new job.
        path = html_module.unescape(link.group("href")).lstrip("./")
        city = _CITY_RE.search(row)
        audience = _AUDIENCE_RE.search(row)
        postings.append(RawPosting(
            source=NAME,
            url=f"{origin}/{path.split('?')[0]}",
            title=_text(title.group("title")),
            employer_name=employer.name,
            city=_text(city.group("city")) if city else employer.city,
            country=employer.country,
            # The row has no advert; the detail page is server-rendered and
            # `enrich` reads it through the plain-HTML route.
            description="",
            external_id=match.group("id"),
            level=_level(_text(audience.group("audience")) if audience else ""),
        ))
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []
    response = client.get(widget_url(employer.ats_endpoint))
    response.raise_for_status()
    return parse_jobs(response.text, employer)
