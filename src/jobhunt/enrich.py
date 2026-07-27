"""Fetch posting text on demand and have the model read it.

Fetching is deliberately lazy. Most spotted jobs are archived unread, so
crawling every posting at poll time would spend hundreds of requests on jobs
nobody will look at. Text is fetched once per posting and kept, so a
re-analysis costs an API call but never another crawl.

Enrichment writes only to the LLM columns. The deterministic scores stay
exactly as the matcher computed them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from jobhunt.llm import analyse
from jobhunt.models import Job
from jobhunt.store import Store


# Each call is a separate headless process, so this is bounded by how many
# model sessions are reasonable to have in flight, not by CPU.
DEFAULT_WORKERS = 4


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
    workers: int = DEFAULT_WORKERS,
) -> EnrichReport:
    """Analyse each job, fetching its posting text first if we do not have it.

    Fetching and analysis run concurrently: each posting costs around twelve
    seconds of headless model startup and generation, and the jobs are
    independent, so running them one at a time wasted almost all of it. Only
    the database write is serial — the SQLite connection belongs to the
    calling thread.

    A job that fails — dead link, unparseable reply — is recorded and the run
    continues. One broken posting must not abandon the rest of the batch.
    """
    report = EnrichReport()

    pending = []
    for job in jobs:
        if job.id is None:
            continue
        if job.llm_checked and not force:
            report.skipped += 1
            continue
        pending.append(job)
        if limit is not None and len(pending) >= limit:
            break

    if not pending:
        return report

    def work(job: Job):
        text = job.description or fetch_text(job, client)
        return text, analyse(job, text, profile, llm)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, job): job for job in pending}
        for future in as_completed(futures):
            job = futures[future]
            try:
                text, verdict = future.result()
            except Exception as exc:
                report.failed.append(f"{job.url}: {type(exc).__name__}: {exc}")
                continue
            store.save_enrichment(job.id, text, verdict.domain_fit,
                                  verdict.to_json(), now)
            report.analysed += 1
    return report
