"""ISAE-SUPAERO's doctoral offers, one WordPress archive page.

Toulouse, and the reason it is worth polling is the company it keeps: its
doctoral students sit in teams shared with ONERA, and its industrial chairs are
Airbus DS, ArianeGroup, Thales and Safran. A thesis here is frequently the same
work the Thales and CNES postings describe, funded differently.

The archive does something most listings do not: it inlines every offer in
full instead of linking to it. One fetch carries the subject, the contract
type, the salary band and the whole body, so nothing needs a second request and
the matcher sees the text rather than a title. That matters more for theses
than for jobs — "Deterministic QoS Guarantees in Next-Generation LEO Satellite
Constellations" matches no role family on its title, and the navigation
content is three paragraphs down.

Two judgements, both borrowed from onera_theses:

The URL stored is the PDF, not the page. For a doctoral offer the PDF is the
subject description and the application route, the same way the supervisor's
address is at ONERA. An offer with no PDF keeps the archive URL.

The level is read off the card rather than inferred from the title. The cards
state "PhD offer" outright, and a thesis priced as a graduate job reads as a
derisory salary.

`ats_endpoint` holds the archive URL.
"""

from __future__ import annotations

import html as html_module
import re

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "isae_theses"
DEFAULT_URL = "https://www.isae-supaero.fr/en/type-contrat/phd-offer/"

# Each offer opens with its own <h1>. The page is an archive, so there are
# several on it; splitting on the heading is what separates one from the next.
_HEADING = '<h1 class="titleSection__title">'
_TITLE_RE = re.compile(r"^(?P<title>.*?)</h1>", re.S)
_PDF_RE = re.compile(r'href="(?P<url>[^"]+\.pdf)"', re.I)
_CONTRACT_RE = re.compile(r'cardEmploi__contrat">(?P<contract>.*?)</p>', re.S)
_BODY_RE = re.compile(
    r'<div class="contenus-offre-empoloi wysiwyg">(?P<body>.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def _text(raw: str) -> str:
    plain = _TAG_RE.sub(" ", html_module.unescape(_SCRIPT_RE.sub(" ", raw or "")))
    return re.sub(r"\s+", " ", plain).strip()


def parse_offers(page: str, employer: Employer) -> list[RawPosting]:
    """Turn the archive into postings, one per offer block."""
    postings: list[RawPosting] = []
    for block in (page or "").split(_HEADING)[1:]:
        title_match = _TITLE_RE.match(block)
        if not title_match:
            continue
        title = _text(title_match.group("title"))
        if not title:
            continue

        pdf = _PDF_RE.search(block)
        body = _BODY_RE.search(block)
        # The body div is the advert. Where the markup changes and it is not
        # found, the block's own text is a worse but honest fallback, which is
        # better than storing a thesis with no description for the matcher.
        description = _text(body.group("body")) if body else _text(block)

        contract = _CONTRACT_RE.search(block)
        stated = _text(contract.group("contract")) if contract else ""

        postings.append(RawPosting(
            source=NAME,
            url=pdf.group("url") if pdf else (
                employer.ats_endpoint or employer.careers_url or DEFAULT_URL),
            title=title,
            employer_name=employer.name,
            city=employer.city,
            country=employer.country,
            description=description,
            external_id="",
            # The card says so; nothing here has to read the wording.
            level="phd" if "phd" in stated.lower() else "",
        ))
    return postings


def fetch(employer: Employer, client) -> list[RawPosting]:
    """Read the archive. One page, one request, every open offer."""
    url = employer.ats_endpoint or employer.careers_url or DEFAULT_URL
    response = client.get(url)
    response.raise_for_status()
    return parse_offers(response.text, employer)
