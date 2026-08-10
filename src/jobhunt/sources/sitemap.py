"""Sitemap job boards (CNES, Telespazio France, Sirius, PLD Space).

For employers with no reachable API. Four of them publish every open advert as
a URL in sitemap.xml — the file they hand search engines so their jobs get
indexed — and that file is plain XML, served without a token, and allowed by
their own robots.txt. It is the least clever source here and the widest: one
parser reaches four boards that otherwise needed four.

What it gets is thin. A sitemap carries URLs and nothing else: no location, no
contract, no advert. The title is recovered from the URL slug, which is what
these sites put there for the same indexing reason, and everything else waits
for enrichment to fetch the page. That is enough, because the slug is what the
matcher reads and the model gets the real page later; all four detail pages
are server-rendered and come out readable through the ordinary route in
`posting_text.py`, so no per-site extraction was needed.

The filter is the whole design. A sitemap lists the entire site, so something
has to say which URLs are adverts — without that, Exotrail's 93 marketing
pages would arrive as 93 jobs. `ats_endpoint` carries the sitemap URL with the
job path as its fragment:

    https://recrutement.cnes.fr/sitemap.xml#/fr/annonce/

A fragment is never sent to the server, so this costs nothing at fetch time and
keeps the whole configuration for a board on one readable line. A missing
fragment is an error rather than a permissive default: getting it wrong should
show up in the poll report, not as a pipeline full of privacy policies.

Sitemap indexes are followed one level, which is how Sirius separates its
French and English boards — and why the fragment picks one of the two rather
than storing every job twice.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "sitemap"

# A stop, not an expectation: the four boards here need one or two. A site that
# shards its sitemap into hundreds is not a job board and should not be walked.
MAX_SITEMAPS = 25

# Leading advert id: "8104417-ingenieur-systemes..." on Telespazio and CNES.
# Three digits minimum so a real word beginning in a number survives.
_ID_PREFIX_RE = re.compile(r"^(\d{3,})[-_]")
# French and German gender markers, which every one of these boards appends and
# none of which is part of the job title: -f-h, (f/h), -h-f, -m-w-d, and CNES's
# unpunctuated -hf. Only a whole trailing token counts, so a real word that
# happens to end in these letters is not truncated.
_GENDER_SUFFIX_RE = re.compile(
    r"[-_(]\(?(?:[fhmwdx](?:[-_/]?[fhmwdx])+)\)?[-_]*$", re.I)
# CNES ends its slugs with the workplace: "...-31400-toulouse".
_POSTCODE_CITY_RE = re.compile(r"[-_](\d{5})[-_]([a-z][a-z-]*)$", re.I)
_EXTENSION_RE = re.compile(r"\.(?:html?|aspx|php)$", re.I)
_SEPARATORS_RE = re.compile(r"[-_\s]+")


def _slug(url: str) -> str:
    """The last path segment, percent-decoded and stripped of its extension.

    Decoded after the split, not before: Sirius writes `f/h` as `%2F` inside a
    segment, and unquoting first would turn one advert into two path segments.
    """
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if not segments:
        return ""
    return _EXTENSION_RE.sub("", unquote(segments[-1]))


def external_id(url: str) -> str:
    """The advert id where the slug carries one, otherwise empty."""
    match = _ID_PREFIX_RE.match(_slug(url))
    return match.group(1) if match else ""


def city_from_url(url: str) -> str:
    """`'...-serveurs-mcp-et-n8n-31400-toulouse'` -> `'Toulouse'`.

    Only the postcode form is read. A slug's trailing word is usually part of
    the title, so guessing without a postcode to anchor on would file half the
    board in imaginary cities; when there is no postcode the city is left empty
    and the model reads the real one off the posting.
    """
    match = _POSTCODE_CITY_RE.search(_slug(url))
    if not match:
        return ""
    return _SEPARATORS_RE.sub("-", match.group(2)).title()


def title_from_url(url: str) -> str:
    """`'/jobs/8104417-ingenieur-systemes-spatiales-f-h'` -> the title.

    Only the first letter is capitalised. Title-casing the whole string reads
    better on `propulsion analysis engineer` and lies about every acronym these
    postings are named after — `Gnss`, `Sar`, `Gnc` — and those are exactly the
    words worth reading correctly here.
    """
    slug = _ID_PREFIX_RE.sub("", _slug(url))
    slug = _POSTCODE_CITY_RE.sub("", slug)
    slug = _GENDER_SUFFIX_RE.sub("", slug)
    text = _SEPARATORS_RE.sub(" ", slug).strip(" -_")
    return text[:1].upper() + text[1:]


def parse_locations(xml: str, url: str) -> tuple[list[str], bool]:
    """Every <loc> in a sitemap, and whether the document is an index.

    Tags are compared on their local name. Every one of these files declares a
    namespace and they do not agree on which; matching the qualified name would
    return nothing at all, and quietly, which is the failure worth avoiding in
    a source whose whole job is a list of URLs.
    """
    try:
        root = ElementTree.fromstring((xml or "").strip())
    except ElementTree.ParseError as exc:
        raise ValueError(f"malformed sitemap at {url}: {exc}")

    def local(tag: str) -> str:
        return str(tag).rsplit("}", 1)[-1]

    locations = [(node.text or "").strip()
                 for node in root.iter() if local(node.tag) == "loc"]
    return [loc for loc in locations if loc], local(root.tag) == "sitemapindex"


def is_job_url(url: str, path_prefix: str) -> bool:
    """True when the URL sits under the job path and names an advert.

    The board's own index page sits at the prefix exactly — PLD Space lists
    `/posiciones-abiertas/` alongside the adverts under it — so a URL has to
    carry a slug of its own to count.
    """
    path = urlsplit(url).path
    if not path.startswith(path_prefix):
        return False
    return bool(path[len(path_prefix):].strip("/"))


def fetch(employer: Employer, client) -> list[RawPosting]:
    endpoint = (employer.ats_endpoint or "").strip()
    if not endpoint:
        return []
    sitemap_url, _, path_prefix = endpoint.partition("#")
    if not path_prefix:
        raise ValueError(
            f"{employer.name}: ats_endpoint needs the job path as a fragment, "
            f"e.g. {sitemap_url}#/jobs/")

    def read(url: str) -> tuple[list[str], bool]:
        response = client.get(url)
        response.raise_for_status()
        return parse_locations(response.text, url)

    locations, is_index = read(sitemap_url)
    if is_index:
        nested: list[str] = []
        for child in locations[:MAX_SITEMAPS]:
            # One bad child sitemap must not lose the others: an index is a
            # list of independent files and the rest are still good.
            try:
                child_locations, _ = read(child)
            except Exception:
                continue
            nested.extend(child_locations)
        locations = nested

    postings: list[RawPosting] = []
    seen: set[str] = set()
    for url in locations:
        if url in seen or not is_job_url(url, path_prefix):
            continue
        seen.add(url)
        title = title_from_url(url)
        if not title:
            continue
        postings.append(RawPosting(
            source=NAME,
            url=url,
            title=title,
            employer_name=employer.name,
            city=city_from_url(url),
            # A sitemap says nothing about where the work is. These are
            # single-country employers, so their own country is the honest
            # default and enrichment corrects it from the posting.
            country=employer.country,
            # No advert here at all — left empty so enrichment fetches the
            # page rather than scoring a title against itself.
            description="",
            external_id=external_id(url),
        ))
    return postings
