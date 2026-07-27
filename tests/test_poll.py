from datetime import date

import pytest

from jobhunt.config import City, CompConfig, RoleFamily, ScoringConfig
from jobhunt.models import Employer, Job, Stage
from jobhunt.poll import PollReport, poll_all, poll_employer
from jobhunt.sources.base import RawPosting
from jobhunt.store import Store


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss", "galileo", "navigation"])],
        negative_keywords=["land surveyor"],
        excluded_countries=["GB"],
        sunshine_range=[1500, 2500], rent_range=[500, 1500],
    )


@pytest.fixture
def comp_cfg():
    return CompConfig(
        salary_by_country={"DE": {"junior": 60000}},
        effective_tax={"DE": 0.4}, pli={"DE": 1.0},
        reference_purchasing_power=40000,
    )


@pytest.fixture
def cities():
    return {"munich": City("Munich", "DE", 1777, 9, 1400)}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "p.db")
    s.initialize()
    yield s
    s.close()


def posting(title, url, city="Munich", country="DE", description=""):
    return RawPosting(source="workday", url=url, title=title,
                      employer_name="Airbus", city=city, country=country,
                      description=description or title)


def fake_source(postings, calls=None):
    def _fetch(employer, client, **kwargs):
        if calls is not None:
            calls.append(employer.name)
        return list(postings)
    return _fetch


def test_relevant_postings_are_stored_and_scored(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus", ats="workday", country="DE")
    store.upsert_employer(employer)
    found = [posting("GNSS Navigation Engineer", "https://x/1")]

    report = poll_employer(store, employer, fake_source(found), None,
                           cfg, comp_cfg, cities, now="2026-07-26T00:00:00Z")

    assert report.seen == 1
    assert report.stored == 1
    job = store.list_jobs()[0]
    assert job.title == "GNSS Navigation Engineer"
    assert job.role_fit > 0
    assert job.total_score > 0
    assert job.source == "workday"


def test_irrelevant_postings_are_skipped_but_counted(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus", ats="workday", country="DE")
    store.upsert_employer(employer)
    found = [
        posting("GNSS Engineer", "https://x/1"),
        posting("Manager Commercial and Contracts", "https://x/2"),
        posting("Accountant", "https://x/3"),
    ]

    report = poll_employer(store, employer, fake_source(found), None,
                           cfg, comp_cfg, cities, now="2026-07-26T00:00:00Z")

    assert report.seen == 3
    assert report.stored == 1
    assert report.skipped == 2
    assert [j.title for j in store.list_jobs()] == ["GNSS Engineer"]


def test_repolling_does_not_duplicate_or_reset_stage(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus", ats="workday", country="DE")
    store.upsert_employer(employer)
    found = [posting("GNSS Engineer", "https://x/1")]
    source = fake_source(found)

    poll_employer(store, employer, source, None, cfg, comp_cfg, cities,
                  now="2026-07-26T00:00:00Z")
    job_id = store.list_jobs()[0].id
    store.set_stage(job_id, Stage.APPLIED, ts="2026-07-27T00:00:00Z")

    report = poll_employer(store, employer, source, None, cfg, comp_cfg, cities,
                           now="2026-07-28T00:00:00Z")

    assert len(store.list_jobs()) == 1
    assert store.get_job(job_id).stage is Stage.APPLIED
    assert report.new == 0


def test_archived_jobs_stay_archived_when_repolled(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus", ats="workday", country="DE")
    store.upsert_employer(employer)
    source = fake_source([posting("GNSS Engineer", "https://x/1")])

    poll_employer(store, employer, source, None, cfg, comp_cfg, cities,
                  now="2026-07-26T00:00:00Z")
    job_id = store.list_jobs()[0].id
    store.archive_job(job_id, "not for me", ts="2026-07-27T00:00:00Z")

    poll_employer(store, employer, source, None, cfg, comp_cfg, cities,
                  now="2026-07-28T00:00:00Z")

    assert store.list_jobs() == []
    assert store.get_job(job_id).dismissed is True


def test_new_count_distinguishes_first_sighting(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus", ats="workday", country="DE")
    store.upsert_employer(employer)
    source = fake_source([posting("GNSS Engineer", "https://x/1")])

    first = poll_employer(store, employer, source, None, cfg, comp_cfg, cities,
                          now="2026-07-26T00:00:00Z")
    second = poll_employer(store, employer, source, None, cfg, comp_cfg, cities,
                           now="2026-07-27T00:00:00Z")
    assert first.new == 1
    assert second.new == 0


def test_excluded_country_is_not_stored(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus UK", ats="workday", country="GB")
    store.upsert_employer(employer)
    found = [posting("GNSS Engineer", "https://x/uk", city="Bristol", country="GB")]

    report = poll_employer(store, employer, fake_source(found), None,
                           cfg, comp_cfg, cities, now="2026-07-26T00:00:00Z")
    assert report.stored == 0
    assert store.list_jobs() == []


def test_a_failing_source_is_recorded_not_raised(store, cfg, comp_cfg, cities):
    employer = Employer(name="Broken", ats="workday", country="DE")
    store.upsert_employer(employer)

    def boom(employer, client, **kwargs):
        raise RuntimeError("endpoint moved")

    report = poll_employer(store, employer, boom, None, cfg, comp_cfg, cities,
                           now="2026-07-26T00:00:00Z")
    assert report.error is not None
    assert "endpoint moved" in report.error
    assert report.stored == 0


def test_postings_without_a_url_are_skipped(store, cfg, comp_cfg, cities):
    employer = Employer(name="Airbus", ats="workday", country="DE")
    store.upsert_employer(employer)
    found = [posting("GNSS Engineer", "")]

    report = poll_employer(store, employer, fake_source(found), None,
                           cfg, comp_cfg, cities, now="2026-07-26T00:00:00Z")
    assert report.stored == 0


def test_poll_all_only_visits_pollable_employers(store, cfg, comp_cfg, cities):
    calls = []
    store.upsert_employer(Employer(name="Airbus", ats="workday",
                                   poll_enabled=True, country="DE"))
    store.upsert_employer(Employer(name="Small GmbH", ats="manual",
                                   poll_enabled=False, country="DE"))
    store.upsert_employer(Employer(name="Off", ats="workday",
                                   poll_enabled=False, country="DE"))

    registry = {"workday": fake_source([posting("GNSS Engineer", "https://x/1")],
                                       calls)}
    reports = poll_all(store, registry, None, cfg, comp_cfg, cities,
                       now="2026-07-26T00:00:00Z")

    assert calls == ["Airbus"]
    assert [r.employer for r in reports] == ["Airbus"]


def test_poll_all_records_an_unknown_ats_instead_of_crashing(store, cfg, comp_cfg,
                                                             cities):
    store.upsert_employer(Employer(name="Weird", ats="nosuchats",
                                   poll_enabled=True, country="DE"))
    reports = poll_all(store, {}, None, cfg, comp_cfg, cities,
                       now="2026-07-26T00:00:00Z")
    assert len(reports) == 1
    assert "nosuchats" in reports[0].error


def test_report_totals_add_up():
    reports = [
        PollReport(employer="A", source="workday", seen=10, stored=3, new=2),
        PollReport(employer="B", source="euraxess", seen=5, stored=1, new=1),
    ]
    assert sum(r.seen for r in reports) == 15
    assert sum(r.stored for r in reports) == 4
    assert sum(r.new for r in reports) == 3
