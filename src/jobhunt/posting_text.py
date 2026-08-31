"""Get the full text of a posting, source by source.

Fetching the stored URL is not enough. Workday's job pages render in
JavaScript, so the HTML behind them contains navigation chrome and no
description at all — a first live run fed the model blank pages and collected
confident verdicts about nothing. Each source needs the route that actually
carries the text.

A posting that yields no description raises rather than returning "". Silence
here would produce an analysis of an empty page, which is worse than a
recorded failure.
"""

from __future__ import annotations

import html as html_module
import re
from urllib.parse import urlparse

from jobhunt.models import Job
from jobhunt.snapshot import fetch_text

# Below this, whatever came back is chrome, not a posting.
MIN_USEFUL_CHARS = 40

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_WORKDAY_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$")
# Everything from the job-description heading to the application form.
_TALENTSOFT_BODY_RE = re.compile(
    r'<h2 class="JobDescription".*?(?=<div[^>]+class="[^"]*ts-offer-page-cta|$)',
    re.S)


def strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = html_module.unescape(text)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def cxs_detail_url(url: str) -> str | None:
    """Turn a browsable Workday job URL into its CXS JSON endpoint.

    https://thales.wd3.myworkdayjobs.com/en-US/Careers/job/Roma/Title_R1
      -> https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/job/Roma/Title_R1
    """
    parsed = urlparse(url)
    host = _WORKDAY_HOST_RE.match(parsed.netloc or "")
    if not host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    # An optional locale segment ("en-US") precedes the site name.
    if segments and re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", segments[0]):
        segments = segments[1:]
    if not segments:
        return None
    site, rest = segments[0], segments[1:]
    tail = "/".join(rest)
    return (f"{parsed.scheme}://{parsed.netloc}/wday/cxs/"
            f"{host.group('tenant')}/{site}/{tail}")


def _workday_text(url: str, client) -> str:
    response = client.get(cxs_detail_url(url))
    response.raise_for_status()
    description = (response.json() or {}).get("jobPostingInfo", {}).get(
        "jobDescription", ""
    )
    if not isinstance(description, str):
        raise ValueError(f"unexpected jobDescription shape for {url}")
    return strip_html(description)


# Everything from here down is the form Talentsoft prints on every page:
# location, required degree, languages, then the page's own JavaScript. The
# advert stops here.
_TALENTSOFT_TAIL_RE = re.compile(r"\bLocalisation du poste\b", re.I)


def _drop_talentsoft_furniture(text: str) -> str:
    """Cut the template block off the end of a Talentsoft advert.

    Not cosmetic. The heading is literally "Localisation du poste", and
    `localisation` is a French navigation keyword — one of the ones that make
    ONERA's theses match. Left in, every posting on Safran's 3803-row board
    scores as navigation work, forklift drivers included. The row already
    carries the town, so nothing is lost by cutting here.
    """
    match = _TALENTSOFT_TAIL_RE.search(text or "")
    return (text[: match.start()] if match else text or "").strip()


def talentsoft_text(url: str, client) -> str:
    """The advert out of a Talentsoft detail page.

    The page is server-rendered but 24k characters of it are language pickers,
    login forms and legal boilerplate, and the advert sits near the end. Handed
    over whole it would be truncated to the prompt's 6000-character budget
    before the job description began. The body starts at the "Description du
    poste" heading, which Talentsoft marks with a class.
    """
    response = client.get(url)
    response.raise_for_status()
    match = _TALENTSOFT_BODY_RE.search(response.text or "")
    return _drop_talentsoft_furniture(strip_html(match.group(0) if match else ""))


def _cornerstone_reader(url: str, client) -> str:
    # Imported here, not at module scope: cornerstone.py needs strip_html from
    # this module, and importing it back at the top would be a cycle.
    from jobhunt.sources.cornerstone import posting_text

    return posting_text(url, client)


def fetch_posting_text(job: Job, client, html_fetcher=fetch_text,
                       cornerstone_reader=None) -> str:
    """The posting's full text, by whichever route that source exposes it."""
    if job.source == "workday" and cxs_detail_url(job.url):
        text = _workday_text(job.url, client)
    elif job.source == "talentsoft":
        text = talentsoft_text(job.url, client)
    elif job.source == "cornerstone":
        text = (cornerstone_reader or _cornerstone_reader)(job.url, client)
    else:
        text = strip_html(html_fetcher(job.url, client))

    if len(text.strip()) < MIN_USEFUL_CHARS:
        raise ValueError(f"no description found at {job.url}")
    return text
