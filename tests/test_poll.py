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


def test_source_kwargs_drops_max_pages_for_unpaginated_sources():
    """Breezy.fetch takes no max_pages; passing it would raise TypeError."""
    from jobhunt.poll import source_kwargs

    cfg = ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[],
    )
    assert "max_pages" not in source_kwargs("breezy", cfg, {"max_pages": 3})
    assert source_kwargs("successfactors", cfg, {"max_pages": 3})["max_pages"] == 3


def test_every_registered_source_accepts_the_kwargs_it_is_given():
    """Guards the registry against a source added without its kwargs wired up."""
    import inspect

    from jobhunt.poll import SOURCE_REGISTRY, source_kwargs

    cfg = ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[],
        poll_search_terms=["gnss"],
    )
    for ats, fetch in SOURCE_REGISTRY.items():
        accepted = set(inspect.signature(fetch).parameters)
        passed = set(source_kwargs(ats, cfg, {"max_pages": 3}))
        assert passed <= accepted, f"{ats} cannot accept {passed - accepted}"


def test_the_consulting_and_contractor_sources_are_registered():
    """Consultancies and ESA contractors are where the postings LinkedIn
    monopolises actually originate."""
    from jobhunt.poll import SOURCE_REGISTRY, UNPAGINATED

    assert "smartrecruiters" in SOURCE_REGISTRY
    assert "recruitee" in SOURCE_REGISTRY
    # SmartRecruiters boards run to a thousand postings and must be paged.
    assert "smartrecruiters" not in UNPAGINATED
    assert "recruitee" in UNPAGINATED


def test_smartrecruiters_gets_a_page_budget_that_covers_its_board():
    """A page is 100 postings here and 20 on Workday, so the shared --max-pages
    of 5 would stop at 500 of a 1100-posting board — the live poll did exactly
    that and silently saw less than half of ALTEN."""
    from jobhunt.config import ScoringConfig
    from jobhunt.poll import source_kwargs
    from jobhunt.sources import smartrecruiters

    cfg = ScoringConfig(weights={}, qol_weights={}, role_families=[])
    kwargs = source_kwargs("smartrecruiters", cfg, {"max_pages": 5})
    assert kwargs["max_pages"] >= smartrecruiters.DEFAULT_MAX_PAGES


def test_an_explicit_deeper_page_budget_is_respected():
    from jobhunt.config import ScoringConfig
    from jobhunt.poll import source_kwargs

    cfg = ScoringConfig(weights={}, qol_weights={}, role_families=[])
    assert source_kwargs("smartrecruiters", cfg, {"max_pages": 40})["max_pages"] == 40


def test_the_new_sources_are_registered():
    from jobhunt.poll import SOURCE_REGISTRY

    for name in ("cornerstone", "capgemini", "sii"):
        assert name in SOURCE_REGISTRY, name


def test_a_source_that_carries_the_posting_text_saves_it(store, tmp_path):
    """Capgemini returns the whole advert. Storing it means the model never
    has to fetch that posting — a fetch that fails on half these sites."""
    from jobhunt.config import CompConfig, RoleFamily, ScoringConfig
    from jobhunt.models import Employer
    from jobhunt.poll import _score_and_store
    from jobhunt.sources.base import RawPosting

    cfg = ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5}, qol_weights={},
        role_families=[RoleFamily(name="gnss", weight=1.0, keywords=["gnss"])])
    comp = CompConfig(salary_by_country={}, effective_tax={}, pli={},
                      reference_purchasing_power=40000)
    eid = store.upsert_employer(Employer(name="Capgemini Engineering"))
    employer = store.get_employer(eid)

    body = "Vous travaillerez sur les récepteurs GNSS. " * 12
    assert _score_and_store(store, employer, RawPosting(
        source="capgemini", url="https://x/1", title="Ingénieur GNSS",
        description=body), cfg, comp, {}, "2026-07-27T00:00:00Z")
    assert "récepteurs GNSS" in store.list_jobs()[0].description


def test_a_source_that_only_echoes_the_title_saves_no_text(store):
    """Workday's 'description' is the title again. Storing that would make
    enrichment skip the fetch and analyse a title as if it were the advert."""
    from jobhunt.config import CompConfig, RoleFamily, ScoringConfig
    from jobhunt.models import Employer
    from jobhunt.poll import _score_and_store
    from jobhunt.sources.base import RawPosting

    cfg = ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5}, qol_weights={},
        role_families=[RoleFamily(name="gnss", weight=1.0, keywords=["gnss"])])
    comp = CompConfig(salary_by_country={}, effective_tax={}, pli={},
                      reference_purchasing_power=40000)
    eid = store.upsert_employer(Employer(name="Airbus"))
    employer = store.get_employer(eid)

    _score_and_store(store, employer, RawPosting(
        source="workday", url="https://x/2", title="GNSS Engineer",
        description="GNSS Engineer"), cfg, comp, {}, "2026-07-27T00:00:00Z")
    assert store.list_jobs()[0].description == ""


def test_a_doctoral_posting_is_not_priced_as_a_junior_engineer(store):
    """Polled jobs were all assumed junior. A PhD stipend is not a graduate
    salary, so the compensation score was wrong for every doctoral offer."""
    from jobhunt.config import CompConfig, RoleFamily, ScoringConfig
    from jobhunt.models import Employer
    from jobhunt.poll import _score_and_store
    from jobhunt.sources.base import RawPosting

    cfg = ScoringConfig(
        weights={"comp": 1.0, "qol": 0.0, "fit": 0.0}, qol_weights={},
        role_families=[RoleFamily(name="radar", weight=1.0, keywords=["radar"])])
    comp = CompConfig(salary_by_country={"FR": {"junior": 40000, "phd": 24000}},
                      effective_tax={"FR": 0.25}, pli={"FR": 1.0},
                      reference_purchasing_power=40000)
    eid = store.upsert_employer(Employer(name="ONERA Doctoral", country="FR"))
    employer = store.get_employer(eid)

    _score_and_store(store, employer, RawPosting(
        source="onera_theses", url="https://x/t1", title="Thèse radar SAR",
        country="FR", level="phd"), cfg, comp, {}, "2026-07-28T00:00:00Z")

    job = store.list_jobs()[0]
    assert job.level == "phd"


def test_a_posting_with_no_level_is_still_treated_as_junior(store):
    from jobhunt.config import CompConfig, RoleFamily, ScoringConfig
    from jobhunt.models import Employer
    from jobhunt.poll import _score_and_store
    from jobhunt.sources.base import RawPosting

    cfg = ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5}, qol_weights={},
        role_families=[RoleFamily(name="radar", weight=1.0, keywords=["radar"])])
    comp = CompConfig(salary_by_country={}, effective_tax={}, pli={},
                      reference_purchasing_power=40000)
    eid = store.upsert_employer(Employer(name="Thales", country="FR"))
    _score_and_store(store, store.get_employer(eid), RawPosting(
        source="workday", url="https://x/t2", title="Radar Engineer"),
        cfg, comp, {}, "2026-07-28T00:00:00Z")
    assert store.list_jobs()[0].level == "junior"
