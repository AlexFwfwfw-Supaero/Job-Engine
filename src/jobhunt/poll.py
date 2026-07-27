from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from jobhunt.config import City, CompConfig, ScoringConfig
from jobhunt.match import evaluate
from jobhunt.models import Employer, Job
from jobhunt.score import compensation, quality_of_life, total_score
from jobhunt.sources import euraxess, workday
from jobhunt.sources.base import RawPosting
from jobhunt.store import Store

# ats name -> fetch(employer, client, **kwargs) -> list[RawPosting]
SOURCE_REGISTRY: dict[str, Callable] = {
    workday.NAME: workday.fetch,
    euraxess.NAME: euraxess.fetch,
}


def source_kwargs(ats: str, cfg: ScoringConfig, common: dict) -> dict:
    """Arguments for one source's fetch().

    Sources take different parameters — Workday searches server-side on our
    terms, EURAXESS ignores keyword parameters entirely and filters by facet —
    so passing one kwargs blob to every source would raise TypeError.
    """
    kwargs = dict(common)
    if ats == workday.NAME:
        kwargs["search_terms"] = list(cfg.poll_search_terms)
    return kwargs


@dataclass
class PollReport:
    employer: str
    source: str
    seen: int = 0
    stored: int = 0
    new: int = 0
    skipped: int = 0
    error: str | None = None


def _score_and_store(
    store: Store,
    employer: Employer,
    posting: RawPosting,
    cfg: ScoringConfig,
    comp_cfg: CompConfig,
    cities: dict[str, City],
    now: str,
) -> bool:
    """Store a posting if the matcher considers it relevant. True when stored."""
    country = posting.country or employer.country
    match = evaluate(posting.title, posting.description, country, cfg)
    if not match.relevant:
        return False

    city_entry = cities.get(posting.city.lower()) if posting.city else None
    breakdown = compensation(country, "junior", posting.salary_stated,
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
        tags=list(employer.tags),
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
    **source_kwargs,
) -> PollReport:
    """Fetch one employer's postings, match them, and store what is relevant.

    Only relevant postings are stored. A broad Workday tenant returns
    thousands of jobs, and keeping every one would bury the pipeline; the
    report carries the seen/skipped counts so the filtering stays visible
    rather than looking like an empty market.
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
    report.seen = len(postings)

    for posting in postings:
        if not posting.url:
            report.skipped += 1
            continue
        was_known = posting.url in known
        if _score_and_store(store, employer, posting, cfg, comp_cfg, cities, now):
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
    **common_kwargs,
) -> list[PollReport]:
    """Poll every employer with polling enabled.

    A source that fails is recorded against that employer and the sweep
    continues — one broken parser must not look like a quiet market.
    """
    reports: list[PollReport] = []
    for employer in store.list_employers():
        if only and employer.name != only:
            continue
        if not employer.poll_enabled:
            continue

        source = registry.get(employer.ats)
        if source is None:
            reports.append(PollReport(
                employer=employer.name, source=employer.ats,
                error=f"no source module for ats '{employer.ats}'",
            ))
            continue

        reports.append(poll_employer(
            store, employer, source, client, cfg, comp_cfg, cities, now,
            **source_kwargs(employer.ats, cfg, common_kwargs),
        ))
    return reports
