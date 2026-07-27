from datetime import date

import pytest

from jobhunt.models import Employer, Job, Stage
from jobhunt.store import Store
from jobhunt.web.deps import Deps
from jobhunt.web.views import (
    archive_context, interested_context, overview_context, render_tab,
    search_context,
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
    (d / "cities.yaml").write_text("cities: []\n")
    (d / "comp.yaml").write_text(
        "reference_purchasing_power: 40000\neffective_tax: {}\npli: {}\n"
        "salary_by_country: {}\n"
    )
    (d / "employers.yaml").write_text("employers: []\n")
    (d / "deadlines.yaml").write_text("deadlines: []\n")
    return d


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.initialize()
    yield s
    s.close()


@pytest.fixture
def deps(tmp_path, config_dir):
    return Deps(store_factory=lambda: Store(tmp_path / "t.db"),
                config_dir=config_dir, today=lambda: date(2026, 7, 26),
                http_client=lambda: None)


def add(store, title="GNSS Engineer", url="https://x/1", stage=Stage.SPOTTED):
    eid = store.upsert_employer(Employer(name="Septentrio"))
    jid = store.upsert_job(Job(employer_id=eid, title=title, url=url))
    if stage is not Stage.SPOTTED:
        store.set_stage(jid, stage, ts="2026-07-01T00:00:00Z")
    return jid


def test_every_tab_renders_the_navigation(store, deps):
    html = render_tab("overview", overview_context(store, deps))
    for label in ["Overview", "Job Search", "Interested", "Applied",
                  "Archive", "Deadlines"]:
        assert label in html


def test_overview_explains_each_tab(store, deps):
    html = render_tab("overview", overview_context(store, deps))
    assert "Shortlisted jobs, ranked by the priority you set." in html


def test_search_tab_has_an_add_form(store, deps):
    html = render_tab("search", search_context(store, deps))
    assert 'action="/jobs"' in html
    assert 'name="url"' in html
    assert 'name="employer"' in html


def test_search_rows_offer_triage_buttons(store, deps):
    add(store)
    html = render_tab("search", search_context(store, deps))
    assert "Interested" in html
    assert "Applied" in html
    assert "Archive" in html
    assert 'action="/jobs/1/stage"' in html


def test_search_tab_says_when_no_employer_is_pollable(store, deps):
    html = render_tab("search", search_context(store, deps))
    assert "No employer has polling enabled" in html


def test_search_tab_names_the_polled_employers(store, deps):
    store.upsert_employer(Employer(name="Airbus", ats="workday",
                                   poll_enabled=True))
    store.upsert_employer(Employer(name="Small GmbH", ats="manual"))
    html = render_tab("search", search_context(store, deps))
    assert "Airbus" in html
    assert "not polled" in html


def test_interested_rows_have_priority_and_note_controls(store, deps):
    add(store, stage=Stage.SHORTLISTED)
    html = render_tab("interested", interested_context(store, deps))
    assert 'action="/jobs/1/priority"' in html
    assert 'action="/jobs/1/note"' in html


def test_archive_rows_offer_restore(store, deps):
    jid = add(store)
    store.archive_job(jid, "surveying", ts="2026-07-20T00:00:00Z")
    html = render_tab("archive", archive_context(store, deps))
    assert 'action="/jobs/1/restore"' in html
    assert "surveying" in html


def test_titles_are_escaped(store, deps):
    add(store, title="<script>alert(1)</script>")
    html = render_tab("search", search_context(store, deps))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_dead_links_are_flagged_not_hidden(store, deps):
    jid = add(store)
    store.set_link_status(jid, "dead", ts="2026-07-26T00:00:00Z")
    html = render_tab("search", search_context(store, deps))
    assert "GNSS Engineer" in html
    assert "dead link" in html
