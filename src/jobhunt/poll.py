from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from jobhunt.config import City, CompConfig, ScoringConfig
from jobhunt.match import NO_FAMILY_MATCHED, evaluate
from jobhunt.models import Employer, Job
from jobhunt.posting_text import talentsoft_text
from jobhunt.score import compensation, quality_of_life, total_score
from jobhunt.sources import (
    breezy, capgemini, cornerstone, euraxess, greenhouse, onera_theses, recruitee, rss,
    sii, sitemap, smartrecruiters, softgarden, successfactors, talentlink, talentsoft,
    workday,
)
from jobhunt.sources.base import RawPosting
from jobhunt.store import LAST_SEARCH_AT, Store

# ats name -> fetch(employer, client, **kwargs) -> list[RawPosting]
SOURCE_REGISTRY: dict[str, Callable] = {
    workday.NAME: workday.fetch,
    euraxess.NAME: euraxess.fetch,
    successfactors.NAME: successfactors.fetch,
    breezy.NAME: breezy.fetch,
    greenhouse.NAME: greenhouse.fetch,
    smartrecruiters.NAME: smartrecruiters.fetch,
    recruitee.NAME: recruitee.fetch,
    cornerstone.NAME: cornerstone.fetch,
    capgemini.NAME: capgemini.fetch,
    sii.NAME: sii.fetch,
    rss.NAME: rss.fetch,
    talentlink.NAME: talentlink.fetch,
    onera_theses.NAME: onera_theses.fetch,
    talentsoft.NAME: talentsoft.fetch,
    sitemap.NAME: sitemap.fetch,
    softgarden.NAME: softgarden.fetch,
}

# Sources that return their whole board in one unpaginated response and so
# take no max_pages; passing one would raise TypeError.
UNPAGINATED = frozenset({breezy.NAME, greenhouse.NAME, recruitee.NAME,
                        rss.NAME, onera_theses.NAME, sitemap.NAME,
                         softgarden.NAME})


def source_kwargs(ats: str, cfg: ScoringConfig, common: dict) -> dict:
    """Arguments for one source's fetch().

    Sources take different parameters — Workday searches server-side on our
    terms, EURAXESS ignores keyword parameters entirely and filters by facet —
    so passing one kwargs blob to every source would raise TypeError.
    """
    kwargs = dict(common)
    if ats == workday.NAME:
        kwargs["search_terms"] = list(cfg.poll_search_terms)
    if ats == sii.NAME:
        # Six rows a page against Workday's twenty, and the board runs past
        # 360: the shared count would have seen 30 of them.
        kwargs["max_pages"] = max(
            int(kwargs.get("max_pages") or 0), sii.DEFAULT_MAX_PAGES
        )
    if ats == talentsoft.NAME:
        # Twenty rows a page against a board of 3795. The shared count would
        # have walked 200 of them and called that the whole market.
        kwargs["max_pages"] = max(
            int(kwargs.get("max_pages") or 0), talentsoft.DEFAULT_MAX_PAGES
        )
    if ats == smartrecruiters.NAME:
        # A page is 100 postings here against Workday's 20, and these boards
        # run past a thousand. Sharing one page count meant the first live
        # poll stopped at 500 of ALTEN's 1104 without saying so.
        kwargs["max_pages"] = max(
            int(kwargs.get("max_pages") or 0), smartrecruiters.DEFAULT_MAX_PAGES
        )
    if ats in UNPAGINATED:
        kwargs.pop("max_pages", None)
    return kwargs


@dataclass
class PollReport:
    employer: str
    source: str
    seen: int = 0
    stored: int = 0
    new: int = 0
    skipped: int = 0
    # Adverts fetched one page at a time, for boards whose rows carry none.
    read: int = 0
    error: str | None = None


# Below this a "description" is the title echoed back, not an advert. Sources
# that carry the real text (Capgemini, and manual entry) let the model read a
# posting with no second fetch; sources that do not must leave the column empty
# so enrichment still goes and gets the page.
MIN_STORED_DESCRIPTION = 200


def _stored_description(posting: RawPosting) -> str:
    text = (posting.description or "").strip()
    if len(text) < MIN_STORED_DESCRIPTION or text == (posting.title or "").strip():
        return ""
    return text


# Sources whose listing rows carry a title and nothing else, mapped to the
# route that reads one posting's own page. Safran's board is 3803 rows of
# title-only, so without this the matcher judged "Ingénieur études F/H" — the
# ordinary way a French board titles real work — on four words.
DETAIL_TEXT: dict[str, Callable[[str, object], str]] = {
    talentsoft.NAME: talentsoft_text,
}

# How many adverts one poll will go and read. Screening is remembered, so a
# board drains over a few polls instead of turning the first one into a
# half-hour sweep.
DEFAULT_DETAIL_BUDGET = 600


# A funded thesis says so in its title, in whichever language the board uses.
# "these" is deliberately absent: it is the English word, and it would make a
# doctoral position of anything titled "these systems".
_DOCTORAL_RE = re.compile(
    r"\b(ph\.?d|doctoral|doctorant|doctorante|doktorand(?:in)?|"
    r"promotionsstelle|cifre)\b|thèse|these cifre",
    re.I,
)


def infer_level(title: str, stated: str = "") -> str:
    """Doctoral or graduate, read off the title.

    Only the ONERA source ever set a level, so Airbus's "PHD Position in
    Spacecraft GNC Engineering" was stored as a graduate job and priced
    against a graduate salary. A source that knows better is believed; this is
    the fallback for the boards that carry no such field, which is all of them.
    """
    if stated and stated != "junior":
        return stated
    return "phd" if _DOCTORAL_RE.search(title or "") else "junior"


def _worth_reading(posting: RawPosting, employer: Employer,
                   cfg: ScoringConfig) -> bool:
    """Whether this posting's own page is worth a request.

    Only for the undecided. A title that already matches needs no help, and a
    title the negative list or the country filter has rejected cannot be
    rescued by its advert — reading those would spend thousands of requests to
    confirm what is already known.
    """
    if posting.description.strip():
        return False
    match = evaluate(posting.title, "", posting.country or employer.country,
                     cfg, infer_level(posting.title, posting.level))
    return not match.relevant and match.reasons[:1] == [NO_FAMILY_MATCHED]


def _score_and_store(
    store: Store,
    employer: Employer,
    posting: RawPosting,
    cfg: ScoringConfig,
    comp_cfg: CompConfig,
    cities: dict[str, City],
    now: str,
    keep_unmatched: bool = False,
) -> bool:
    """Store a posting if the matcher considers it relevant. True when stored.

    `keep_unmatched` keeps the ones the keyword list merely failed to
    recognise, for boards small enough that the model can read them all. It
    does not keep the ones the negative list or the country filter rejected:
    those are decisions, not gaps in vocabulary.
    """
    country = posting.country or employer.country
    # Before the match, not after: the country exclusion needs to know whether
    # this is employment or a doctorate.
    level = infer_level(posting.title, posting.level)
    match = evaluate(posting.title, posting.description, country, cfg, level)
    if not match.relevant:
        undecided = match.reasons[:1] == [NO_FAMILY_MATCHED]
        if not (keep_unmatched and undecided):
            return False

    city_entry = cities.get(posting.city.lower()) if posting.city else None
    breakdown = compensation(country, level, posting.salary_stated,
                             comp_cfg, city_entry, cfg)
    qol = quality_of_life(city_entry, cfg)
    total = total_score(breakdown.normalised, qol, match.role_fit, cfg.weights)

    store.upsert_job(Job(
        employer_id=employer.id,
        title=posting.title,
        url=posting.url,
        city=posting.city,
        country=country,
        source=posting.source,
        first_seen=now,
        last_seen=now,
        role_fit=match.role_fit,
        comp_score=breakdown.normalised,
        qol_score=qol,
        total_score=total,
        language_flags=match.language_flags,
        level=level,
        tags=list(employer.tags),
        description=_stored_description(posting),
    ))
    return True


def poll_employer(
    store: Store,
    employer: Employer,
    source: Callable,
    client,
    cfg: ScoringConfig,
    comp_cfg: CompConfig,
    cities: dict[str, City],
    now: str,
    detail_text: Callable[[str, object], str] | None = None,
    detail_budget: int = 0,
    keep_unmatched: bool = False,
    **source_kwargs,
) -> PollReport:
    """Fetch one employer's postings, match them, and store what is relevant.

    Only relevant postings are stored. A broad Workday tenant returns
    thousands of jobs, and keeping every one would bury the pipeline; the
    report carries the seen/skipped counts so the filtering stays visible
    rather than looking like an empty market.

    `detail_text` is for boards whose rows carry no advert. Where the title
    alone says too little to decide, that posting's own page is read before
    judging it, up to `detail_budget` pages in one poll. Which pages were read
    is remembered, so the cost falls to the newly published ones.
    """
    report = PollReport(employer=employer.name, source=employer.ats)

    # Callers may hand over an Employer built in memory; upsert_employer
    # returns the id rather than mutating the object, so resolve it here.
    if employer.id is None:
        known_employer = store.find_employer_by_name(employer.name)
        if known_employer is None:
            report.error = f"employer '{employer.name}' is not in the database"
            return report
        employer.id = known_employer.id

    try:
        postings = source(employer, client, **source_kwargs)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        return report

    known = {j.url for j in store.list_jobs(include_dismissed=True)}
    screened = store.screened_urls() if detail_text else set()
    report.seen = len(postings)

    for posting in postings:
        if not posting.url:
            report.skipped += 1
            continue
        if (detail_text and detail_budget > 0 and posting.url not in screened
                and _worth_reading(posting, employer, cfg)):
            detail_budget -= 1
            report.read += 1
            try:
                posting.description = detail_text(posting.url, client)
            except Exception:
                # One dead advert out of thousands. Marked screened all the
                # same: retrying it every poll forever costs more than the one
                # posting is worth, and `enrich` re-reads anything stored.
                pass
            store.mark_screened(posting.url, employer.id, now)
        was_known = posting.url in known
        if _score_and_store(store, employer, posting, cfg, comp_cfg, cities,
                            now, keep_unmatched=keep_unmatched):
            report.stored += 1
            if not was_known:
                report.new += 1
        else:
            report.skipped += 1

    return report


def poll_all(
    store: Store,
    registry: dict[str, Callable],
    client,
    cfg: ScoringConfig,
    comp_cfg: CompConfig,
    cities: dict[str, City],
    now: str,
    only: str | None = None,
    on_start: Callable[[str], None] | None = None,
    on_done: Callable[[PollReport], None] | None = None,
    detail_readers: dict[str, Callable] | None = None,
    detail_budget: int = DEFAULT_DETAIL_BUDGET,
    **common_kwargs,
) -> list[PollReport]:
    """Poll every employer with polling enabled.

    A source that fails is recorded against that employer and the sweep
    continues — one broken parser must not look like a quiet market.

    A sweep takes minutes, so callers who want to say what is happening pass
    on_start (about to fetch this employer) and on_done (here is its report).
    Both fire for a failing source too, otherwise the count would stall on
    whichever employer broke.
    """
    # Stamped before anything is fetched, with the same `now` that becomes each
    # new job's first_seen. That is what makes "found by this sweep" a simple
    # first_seen >= last_search_at, and it clears the previous sweep's "new"
    # marks the moment this one starts — even if this one finds nothing.
    store.set_meta(LAST_SEARCH_AT, now)

    reports: list[PollReport] = []

    def record(report: PollReport) -> None:
        reports.append(report)
        if on_done:
            on_done(report)

    for employer in store.list_employers():
        if only and employer.name != only:
            continue
        if not employer.poll_enabled:
            continue

        if on_start:
            on_start(employer.name)

        source = registry.get(employer.ats)
        if source is None:
            record(PollReport(
                employer=employer.name, source=employer.ats,
                error=f"no source module for ats '{employer.ats}'",
            ))
            continue

        readers = DETAIL_TEXT if detail_readers is None else detail_readers
        record(poll_employer(
            store, employer, source, client, cfg, comp_cfg, cities, now,
            detail_text=readers.get(employer.ats),
            detail_budget=detail_budget,
            keep_unmatched=employer.read_everything,
            **source_kwargs(employer.ats, cfg, common_kwargs),
        ))
    return reports
