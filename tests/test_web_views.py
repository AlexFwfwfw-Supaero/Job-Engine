from datetime import date

import pytest

from jobhunt.models import Employer, Job, Stage
from jobhunt.store import LAST_SEARCH_AT, Store
from jobhunt.web.deps import Deps
from jobhunt.web.views import (
    TABS, applied_context, archive_context, deadlines_context,
    interested_context, is_new, overview_context, search_context,
)


@pytest.fixture
def config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "scoring.yaml").write_text(
        "weights: {comp: 0.3, qol: 0.2, fit: 0.5}\n"
        "qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}\n"
        "staleness: {applied: [14, 30], phd: [21, 45]}\n"
        "role_families: [{name: gnss, weight: 1.0, keywords: [gnss]}]\n"
    )
    (d / "cities.yaml").write_text(
        "cities: [{name: Munich, country: DE, sunshine_hours: 1777, "
        "nature: 9, rent_index: 1400}]\n"
    )
    (d / "comp.yaml").write_text(
        "reference_purchasing_power: 40000\n"
        "effective_tax: {DE: 0.4}\npli: {DE: 1.0}\n"
        "salary_by_country: {DE: {junior: 60000}}\n"
    )
    (d / "employers.yaml").write_text("employers: []\n")
    (d / "deadlines.yaml").write_text(
        "deadlines: [{name: ESA EGT, employer: ESA, closes: 2026-08-15, "
        "lead_days: 60, url: 'https://example.com'}]\n"
    )
    return d


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "w.db")
    s.initialize()
    yield s
    s.close()


@pytest.fixture
def deps(tmp_path, config_dir):
    return Deps(
        store_factory=lambda: Store(tmp_path / "w.db"),
        config_dir=config_dir,
        today=lambda: date(2026, 7, 26),
        http_client=lambda: None,
    )


def add(store, title, url, stage=Stage.SPOTTED, priority=0):
    eid = store.upsert_employer(Employer(name="Septentrio", country="DE"))
    jid = store.upsert_job(Job(employer_id=eid, title=title, url=url,
                               country="DE", city="Munich"))
    if stage is not Stage.SPOTTED:
        store.set_stage(jid, stage, ts="2026-06-01T00:00:00Z")
    if priority:
        store.set_priority(jid, priority, ts="2026-07-01T00:00:00Z")
    return jid


def test_tabs_start_with_overview():
    assert TABS[0][0] == "overview"
    slugs = [slug for slug, _, _ in TABS]
    assert slugs == ["overview", "search", "interested", "applied",
                     "archive", "deadlines", "advice"]
    assert all(blurb for _, _, blurb in TABS)


def test_overview_counts_every_tab(store, deps):
    add(store, "A", "https://x/a")
    add(store, "B", "https://x/b", stage=Stage.SHORTLISTED)
    add(store, "C", "https://x/c", stage=Stage.APPLIED)
    ctx = overview_context(store, deps)
    counts = ctx["counts"]
    assert counts["search"] == 1
    assert counts["interested"] == 1
    assert counts["applied"] == 1
    assert counts["archive"] == 0


def test_overview_includes_due_and_deadlines(store, deps):
    jid = add(store, "Old", "https://x/old")
    store.set_stage(jid, Stage.APPLIED, ts="2026-06-01T00:00:00Z")
    ctx = overview_context(store, deps)
    assert ctx["due"][0].level == "dormant"
    assert ctx["deadlines"][0].name == "ESA EGT"


def test_search_tab_holds_only_spotted(store, deps):
    add(store, "Spotted", "https://x/s")
    add(store, "Shortlisted", "https://x/i", stage=Stage.SHORTLISTED)
    rows = search_context(store, deps)["rows"]
    assert [r.job.title for r in rows] == ["Spotted"]


def test_search_tab_reports_that_polling_is_unavailable(store, deps):
    assert search_context(store, deps)["search_available"] is False


def test_interested_sorted_by_priority(store, deps):
    add(store, "Low", "https://x/l", stage=Stage.SHORTLISTED, priority=1)
    add(store, "High", "https://x/h", stage=Stage.SHORTLISTED, priority=5)
    rows = interested_context(store, deps)["rows"]
    assert [r.job.title for r in rows] == ["High", "Low"]


def test_applied_groups_by_stage(store, deps):
    add(store, "A", "https://x/a", stage=Stage.APPLIED)
    add(store, "B", "https://x/b", stage=Stage.INTERVIEW)
    groups = applied_context(store, deps)["groups"]
    assert [r.job.title for r in groups["applied"]] == ["A"]
    assert [r.job.title for r in groups["interview"]] == ["B"]


def test_applied_rows_carry_staleness(store, deps):
    add(store, "Old", "https://x/o", stage=Stage.APPLIED)
    row = applied_context(store, deps)["groups"]["applied"][0]
    assert row.stale_days == 55


def test_archive_lists_archived_with_reasons(store, deps):
    jid = add(store, "Noise", "https://x/n")
    store.archive_job(jid, "surveying", ts="2026-07-20T00:00:00Z")
    rows = archive_context(store, deps)["rows"]
    assert [r.job.dismiss_reason for r in rows] == ["surveying"]


def test_archive_includes_terminal_stages(store, deps):
    jid = add(store, "Rejected", "https://x/r")
    store.set_stage(jid, Stage.REJECTED, ts="2026-07-20T00:00:00Z")
    titles = [r.job.title for r in archive_context(store, deps)["rows"]]
    assert "Rejected" in titles


def test_comp_label_says_no_data_when_unknown(store, deps):
    eid = store.upsert_employer(Employer(name="Unknown Co"))
    store.upsert_job(Job(employer_id=eid, title="X", url="https://x/u", country="JP"))
    rows = search_context(store, deps)["rows"]
    assert rows[0].comp_label == "no data"


def test_deadlines_context_lists_cycles(store, deps):
    assert deadlines_context(store, deps)["deadlines"][0].name == "ESA EGT"


# --- "new since the last search" ------------------------------------------

def test_nothing_is_new_before_the_first_search_has_ever_run():
    assert is_new(Job(employer_id=1, title="A", url="https://x/a",
                      first_seen="2026-08-10T09:00:00Z"), None) is False


def test_a_job_first_seen_by_the_last_search_is_new():
    job = Job(employer_id=1, title="A", url="https://x/a",
              first_seen="2026-08-10T09:00:00Z")
    assert is_new(job, "2026-08-10T09:00:00Z") is True


def test_a_job_added_by_hand_after_the_last_search_is_new():
    job = Job(employer_id=1, title="A", url="https://x/a",
              first_seen="2026-08-10T11:30:00Z")
    assert is_new(job, "2026-08-10T09:00:00Z") is True


def test_a_job_from_before_the_last_search_is_not_new():
    job = Job(employer_id=1, title="A", url="https://x/a",
              first_seen="2026-08-09T09:00:00Z")
    assert is_new(job, "2026-08-10T09:00:00Z") is False


def test_a_job_with_no_first_seen_is_not_new():
    job = Job(employer_id=1, title="A", url="https://x/a", first_seen=None)
    assert is_new(job, "2026-08-10T09:00:00Z") is False


def test_rows_mark_the_jobs_the_last_search_found(store, deps):
    eid = store.upsert_employer(Employer(name="Septentrio", country="DE"))
    store.upsert_job(Job(employer_id=eid, title="Old", url="https://x/old",
                         country="DE", first_seen="2026-08-09T09:00:00Z"))
    store.upsert_job(Job(employer_id=eid, title="Fresh", url="https://x/new",
                         country="DE", first_seen="2026-08-10T09:00:00Z"))
    store.set_meta(LAST_SEARCH_AT, "2026-08-10T09:00:00Z")

    marked = {row.job.title: row.is_new for row in search_context(store, deps)["rows"]}
    assert marked == {"Old": False, "Fresh": True}


def test_the_mark_survives_triage_into_another_tab(store, deps):
    """Only the next search clears it — moving a job on does not."""
    eid = store.upsert_employer(Employer(name="Septentrio", country="DE"))
    jid = store.upsert_job(Job(employer_id=eid, title="Fresh",
                               url="https://x/new", country="DE",
                               first_seen="2026-08-10T09:00:00Z"))
    store.set_stage(jid, Stage.SHORTLISTED, ts="2026-08-10T10:00:00Z")
    store.set_meta(LAST_SEARCH_AT, "2026-08-10T09:00:00Z")

    assert interested_context(store, deps)["rows"][0].is_new is True
