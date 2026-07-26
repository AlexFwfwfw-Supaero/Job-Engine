from datetime import date

import pytest

from jobhunt.config import RoleFamily, ScoringConfig
from jobhunt.models import Event, EventKind, Job, Stage
from jobhunt.staleness import due_jobs


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 1, "qol": 1, "fit": 1}, qol_weights={},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        staleness={"applied": [14, 30], "phd": [21, 45]},
    )


def applied_job(job_id, tags=None):
    return Job(id=job_id, employer_id=1, title=f"Job {job_id}",
               url=f"https://x/{job_id}", stage=Stage.APPLIED, tags=tags or [])


def event_on(job_id, day):
    return Event(job_id=job_id, kind=EventKind.STAGE, text="stage -> applied",
                 ts=f"{day}T00:00:00Z")


def test_recent_application_is_not_due(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-07-20")]}
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26)) == []


def test_application_past_the_first_threshold_is_a_nudge(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-07-01")]}
    due = due_jobs(jobs, events, cfg, date(2026, 7, 26))
    assert len(due) == 1
    assert due[0].level == "nudge"
    assert due[0].days_since == 25


def test_application_past_the_second_threshold_is_dormant(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-06-01")]}
    due = due_jobs(jobs, events, cfg, date(2026, 7, 26))
    assert due[0].level == "dormant"


def test_phd_tagged_jobs_use_the_slower_thresholds(cfg):
    jobs = [applied_job(1, tags=["phd"])]
    events = {1: [event_on(1, "2026-07-08")]}  # 18 days
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26)) == []

    events = {1: [event_on(1, "2026-07-01")]}  # 25 days
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26))[0].level == "nudge"


def test_the_most_recent_event_resets_the_clock(cfg):
    jobs = [applied_job(1)]
    events = {1: [event_on(1, "2026-06-01"),
                  Event(job_id=1, kind=EventKind.NOTE, text="they replied",
                        ts="2026-07-24T00:00:00Z")]}
    assert due_jobs(jobs, events, cfg, date(2026, 7, 26)) == []


def test_jobs_not_in_applied_stage_are_ignored(cfg):
    job = applied_job(1)
    job.stage = Stage.INTERVIEW
    assert due_jobs([job], {1: [event_on(1, "2026-01-01")]}, cfg, date(2026, 7, 26)) == []


def test_jobs_with_no_events_are_ignored(cfg):
    assert due_jobs([applied_job(1)], {}, cfg, date(2026, 7, 26)) == []


def test_results_are_sorted_most_stale_first(cfg):
    jobs = [applied_job(1), applied_job(2)]
    events = {1: [event_on(1, "2026-07-05")], 2: [event_on(2, "2026-06-01")]}
    due = due_jobs(jobs, events, cfg, date(2026, 7, 26))
    assert [d.job_id for d in due] == [2, 1]
