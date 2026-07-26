from __future__ import annotations

from dataclasses import dataclass

from jobhunt.models import Job
from jobhunt.store import Store


@dataclass
class LinkResult:
    job_id: int
    url: str
    status: str  # "live" | "dead"


def check_link(url: str, client) -> str:
    """Report whether a posting URL still resolves.

    Any failure — HTTP error status, timeout, DNS failure — reads as 'dead'.
    The distinction between 'gone' and 'unreachable' is not worth modelling:
    either way the operator needs to look at it themselves.
    """
    try:
        response = client.get(url)
        response.raise_for_status()
    except Exception:
        return "dead"
    return "live"


def check_jobs(jobs: list[Job], client, now: str) -> list[LinkResult]:
    """Check every job's URL. Pure: returns results, writes nothing."""
    results: list[LinkResult] = []
    for job in jobs:
        if job.id is None:
            continue
        results.append(LinkResult(job.id, job.url, check_link(job.url, client)))
    return results


def apply_results(store: Store, results: list[LinkResult], now: str) -> None:
    """Persist link check results. Flags only — never archives."""
    for result in results:
        store.set_link_status(result.job_id, result.status, ts=now)
