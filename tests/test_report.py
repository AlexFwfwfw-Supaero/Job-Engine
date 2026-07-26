from datetime import date

import pytest

from jobhunt.config import City, CompConfig, RoleFamily, ScoringConfig
from jobhunt.models import Deadline, Employer, EventKind, Job, Stage
from jobhunt.report import build_context, render
from jobhunt.store import Store


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        staleness={"applied": [14, 30], "phd": [21, 45]},
        sunshine_range=[1500, 2500], rent_range=[500, 1500],
    )


@pytest.fixture
def comp_cfg():
    return CompConfig(
        salary_by_country={"DE": {"junior": 60000}},
        effective_tax={"DE": 0.40}, pli={"DE": 1.0},
        reference_purchasing_power=40000,
    )


@pytest.fixture
def cities():
    return {"munich": City("Munich", "DE", 1777, 9, 1400)}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "d.db")
    s.initialize()
    yield s
    s.close()


def test_context_groups_jobs_by_stage(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Rohde & Schwarz", country="DE"))
    a = store.upsert_job(Job(employer_id=eid, title="GNSS Engineer",
                             url="https://x/a", country="DE", city="Munich",
                             first_seen="2026-07-25T00:00:00Z"))
    store.upsert_job(Job(employer_id=eid, title="Radar Engineer",
                         url="https://x/b", country="DE", city="Munich",
                         first_seen="2026-07-25T00:00:00Z"))
    store.set_stage(a, Stage.APPLIED, ts="2026-07-25T00:00:00Z")

    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert [j.job.title for j in ctx["by_stage"]["applied"]] == ["GNSS Engineer"]
    assert [j.job.title for j in ctx["by_stage"]["spotted"]] == ["Radar Engineer"]


def test_context_resolves_employer_names(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Septentrio"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer", url="https://x/c"))
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["by_stage"]["spotted"][0].employer_name == "Septentrio"


def test_unknown_compensation_reads_as_no_data(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Unknown Co"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer",
                         url="https://x/d", country="JP"))
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["by_stage"]["spotted"][0].comp_label == "no data"


def test_known_compensation_shows_figure_and_provenance(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="R&S"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer",
                         url="https://x/e", country="DE", city="Munich"))
    label = build_context(store, [], cfg, comp_cfg, cities,
                          date(2026, 7, 26))["by_stage"]["spotted"][0].comp_label
    assert "60" in label
    assert "estimated" in label


def test_dismissed_jobs_are_excluded(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Noise"))
    jid = store.upsert_job(Job(employer_id=eid, title="Surveyor", url="https://x/f"))
    store.dismiss_job(jid, "not engineering", ts="2026-07-26T00:00:00Z")
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["by_stage"]["spotted"] == []


def test_unwatched_employers_are_listed(store, cfg, comp_cfg, cities):
    store.upsert_employer(Employer(name="Small GmbH", ats="manual", poll_enabled=False))
    store.upsert_employer(Employer(name="Big SA", ats="greenhouse", poll_enabled=True))
    ctx = build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26))
    assert [e.name for e in ctx["unwatched_employers"]] == ["Small GmbH"]


def test_due_and_deadlines_are_included(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="ESA"))
    jid = store.upsert_job(Job(employer_id=eid, title="YGT", url="https://x/g"))
    store.set_stage(jid, Stage.APPLIED, ts="2026-06-01T00:00:00Z")
    deadlines = [Deadline(name="YGT round", closes="2026-08-15", lead_days=60)]

    ctx = build_context(store, deadlines, cfg, comp_cfg, cities, date(2026, 7, 26))
    assert ctx["due"][0].level == "dormant"
    assert ctx["deadlines"][0].name == "YGT round"


def test_render_produces_html_containing_the_jobs(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="Thales Alenia Space"))
    store.upsert_job(Job(employer_id=eid, title="Navigation Engineer",
                         url="https://x/h", country="FR", city="Toulouse"))
    html = render(build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26)))
    assert "<html" in html.lower()
    assert "Navigation Engineer" in html
    assert "Thales Alenia Space" in html


def test_render_escapes_employer_and_title_text(store, cfg, comp_cfg, cities):
    eid = store.upsert_employer(Employer(name="<script>alert(1)</script>"))
    store.upsert_job(Job(employer_id=eid, title="GNSS", url="https://x/i"))
    html = render(build_context(store, [], cfg, comp_cfg, cities, date(2026, 7, 26)))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
