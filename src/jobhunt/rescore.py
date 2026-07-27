"""Re-evaluate stored jobs against the current scoring configuration.

Polling only writes scores for postings the matcher still considers relevant,
so a job that stops matching keeps whatever score it had when it was stored.
After a config change that reads as a lie: dropping the standalone research
family left "AI UX Researcher" sitting at 0.50 in the ranked list while the
matcher considered it irrelevant. Rescoring closes that gap.

Only scores are touched. Stage, dismissal, notes and priority are the user's
triage decisions and are never rewritten by a scoring change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.config import City, CompConfig, ScoringConfig
from jobhunt.match import evaluate
from jobhunt.score import compensation, quality_of_life, total_score
from jobhunt.store import Store


@dataclass
class RescoreReport:
    rescored: int = 0
    no_longer_matching: list[str] = field(default_factory=list)


def rescore_all(
    store: Store,
    cfg: ScoringConfig,
    comp_cfg: CompConfig,
    cities: dict[str, City],
) -> RescoreReport:
    """Recompute every stored job's scores. Dismissed jobs are included, since
    the archive shows their scores too."""
    report = RescoreReport()
    for job in store.list_jobs(include_dismissed=True):
        if job.id is None:
            continue
        # Descriptions are not stored, so this matches on the title alone —
        # the same signal most sources give us at poll time anyway.
        match = evaluate(job.title, "", job.country, cfg)
        city = cities.get(job.city.lower()) if job.city else None
        breakdown = compensation(job.country, job.level, job.salary_stated,
                                 comp_cfg, city, cfg)
        qol = quality_of_life(city, cfg)

        job.role_fit = match.role_fit
        job.comp_score = breakdown.normalised
        job.qol_score = qol
        job.total_score = total_score(breakdown.normalised, qol,
                                      match.role_fit, cfg.weights)
        store.upsert_job(job)
        report.rescored += 1
        if not match.relevant:
            report.no_longer_matching.append(job.title)
    return report
