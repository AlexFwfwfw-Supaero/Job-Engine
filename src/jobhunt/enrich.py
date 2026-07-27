"""Fetch posting text on demand and have the model read it.

Fetching is deliberately lazy. Most spotted jobs are archived unread, so
crawling every posting at poll time would spend hundreds of requests on jobs
nobody will look at. Text is fetched once per posting and kept, so a
re-analysis costs an API call but never another crawl.

Enrichment writes only to the LLM columns. The deterministic scores stay
exactly as the matcher computed them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.llm import analyse
from jobhunt.models import Job
from jobhunt.store import Store


@dataclass
class EnrichReport:
    analysed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


def enrich_jobs(
    store: Store,
    jobs: list[Job],
    profile: str,
    llm,
    fetch_text,
    now: str,
    *,
    limit: int | None = None,
    force: bool = False,
    client=None,
) -> EnrichReport:
    """Analyse each job, fetching its posting text first if we do not have it.

    A job that fails — dead link, unparseable reply — is recorded and the run
    continues. One broken posting must not abandon the rest of the batch.
    """
    report = EnrichReport()
    for job in jobs:
        if limit is not None and report.analysed >= limit:
            break
        if job.id is None:
            continue
        if job.llm_checked and not force:
            report.skipped += 1
            continue

        try:
            text = job.description or fetch_text(job.url, client)
            verdict = analyse(job, text, profile, llm)
        except Exception as exc:
            report.failed.append(f"{job.url}: {type(exc).__name__}: {exc}")
            continue

        store.save_enrichment(job.id, text, verdict.domain_fit,
                              verdict.to_json(), now)
        report.analysed += 1
    return report
