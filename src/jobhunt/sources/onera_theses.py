"""ONERA's doctoral offers, one Drupal page per department.

These are not in the TalentLink feed that the ONERA employer entry polls. They
live under w3.onera.fr/formationparlarecherche, split by department — DEMR for
electromagnetics and radar, DTIS for information processing and systems, then
DOTA, DPHY and DMPE.

The reason this is its own module rather than a generic HTML parser is a
distinction the page makes and a scraper would miss: each department lists its
**filled** positions under "Pourvu" and its **open** ones under "A pourvoir",
and the filled block comes first. Taking every table row on the page returns
mostly closed positions, presented exactly like open ones — a list of PhDs you
cannot apply to, indistinguishable from ones you can. Only rows inside the
"A pourvoir" block are returned here.

Two smaller judgements. The reference ("PHY-DEMR-2026-02") is not a title and
tells the matcher nothing, so the subject line becomes the title and the
reference becomes the external id. And the supervisor's address is kept in the
description, because for a doctoral position that address *is* the application
route.

`ats_endpoint` holds the department page URLs, comma-separated.
"""

from __future__ import annotations

import html as html_module
import re
import unicodedata
from urllib.parse import urljoin

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "onera_theses"
OPEN_HEADING = "a pourvoir"

_BLOCK_RE = re.compile(
    r'<h2\s+class="title">(?P<heading>.*?)</h2>'
    r'(?P<body>.*?)(?=<h2\s+class="title">|\Z)',
    re.S,
)
_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<row>.*?)</tr>", re.S)
# Whitespace inside the tag is not decoration: the live page emits
# `<td  class="...">` with two spaces, and a regex demanding one matched the
# fixture perfectly while returning nothing from the real site.
_CELL_RE = re.compile(
    r'<td\s+class="views-field\s+views-field-(?P<field>[a-z-]+)"[^>]*>'
    r"(?P<cell>.*?)</td>",
    re.S,
)
_HREF_RE = re.compile(r'href="(?P<href>[^"]+)"')
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TAG_RE = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    text = _TAG_RE.sub(" ", html_module.unescape(raw or "").replace("\xa0", " "))
    return re.sub(r"\s+", " ", text).strip()


def _fold(raw: str) -> str:
    """Accent- and case-insensitive, because the site writes both À and A."""
    stripped = unicodedata.normalize("NFKD", _text(raw))
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


def _cells(row: str) -> dict[str, str]:
    return {m.group("field"): m.group("cell") for m in _CELL_RE.finditer(row)}


def _row_to_posting(row: str, employer: Employer, page_url: str) -> RawPosting | None:
    cells = _cells(row)
    subject = _text(cells.get("body", ""))
    if not subject:
        return None

    reference = _text(cells.get("title", ""))
    contact = _EMAIL_RE.search(cells.get("body", ""))
    # Drop the trailing "contact : x@onera.fr" from the title, keep it below.
    title = re.sub(r"\s*contact\s*:\s*\S*\s*$", "", subject, flags=re.I).strip()

    detail = _HREF_RE.search(cells.get("title", ""))
    # Without a detail page, the reference still has to make the URL unique:
    # the store keys jobs on it, so two theses sharing one would overwrite.
    url = (urljoin(page_url, detail.group("href")) if detail
           else f"{page_url}#{reference}")

    return RawPosting(
        source=NAME,
        url=url,
        title=title,
        employer_name=employer.name,
        city=_text(cells.get("field-centre", "")),
        country=employer.country,
        description=" · ".join(filter(None, [
            title, reference, contact.group(0) if contact else "",
        ])),
        external_id=reference,
        level="phd",
    )


def parse_jobs(html: str, employer: Employer,
               page_url: str = "") -> list[RawPosting]:
    page_url = page_url or employer.ats_endpoint.split(",")[0].strip()
    postings = []
    for block in _BLOCK_RE.finditer(html or ""):
        if _fold(block.group("heading")) != OPEN_HEADING:
            continue
        for row in _ROW_RE.finditer(block.group("body")):
            posting = _row_to_posting(row.group("row"), employer, page_url)
            if posting is not None:
                postings.append(posting)
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []

    found: list[RawPosting] = []
    for url in (u.strip() for u in employer.ats_endpoint.split(",")):
        if not url:
            continue
        try:
            response = client.get(url)
            response.raise_for_status()
        except Exception:
            # Five departments: one page failing must not read as ONERA
            # having no doctoral offers at all.
            continue
        found.extend(parse_jobs(response.text, employer, page_url=url))
    return found
