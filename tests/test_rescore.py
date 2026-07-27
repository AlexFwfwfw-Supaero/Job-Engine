import pytest

from jobhunt.config import City, CompConfig, RoleFamily, RoleModifiers, ScoringConfig
from jobhunt.models import Employer, Job, Stage
from jobhunt.rescore import rescore_all
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "jobs.db")
    s.initialize()
    s.upsert_employer(Employer(name="Thales", country="FR", city="Toulouse"))
    return s


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        role_modifiers=RoleModifiers(weight=0.2, keywords=["research"]),
    )


@pytest.fixture
def comp_cfg():
    return CompConfig(salary_by_country={}, effective_tax={}, pli={},
                      reference_purchasing_power=40000)


def add(store, title, role_fit=0.9, total=0.9):
    return store.upsert_job(Job(
        employer_id=1, title=title, url=f"https://x/{title}", city="Toulouse",
        country="FR", source="workday", first_seen="2026-07-01",
        last_seen="2026-07-01", role_fit=role_fit, total_score=total,
    ))


def test_rescore_updates_a_job_whose_score_changed(store, cfg, comp_cfg):
    job_id = add(store, "GNSS Research Engineer", role_fit=0.1, total=0.1)
    report = rescore_all(store, cfg, comp_cfg, {})
    updated = store.get_job(job_id)
    assert updated.role_fit == pytest.approx(0.7)
    assert report.rescored == 1


def test_rescore_zeroes_a_job_that_no_longer_matches(store, cfg, comp_cfg):
    """A config change must not leave stale high scores behind.

    Dropping the standalone research family left 'AI UX Researcher' sitting at
    0.50 in the list while the matcher considered it irrelevant.
    """
    job_id = add(store, "AI UX Researcher", role_fit=0.5, total=0.5)
    report = rescore_all(store, cfg, comp_cfg, {})
    assert store.get_job(job_id).role_fit == 0.0
    assert report.no_longer_matching == ["AI UX Researcher"]


def test_rescore_preserves_triage_state(store, cfg, comp_cfg):
    job_id = add(store, "GNSS Engineer")
    store.set_stage(job_id, Stage.SHORTLISTED, "2026-07-27")
    rescore_all(store, cfg, comp_cfg, {})
    assert store.get_job(job_id).stage == Stage.SHORTLISTED


def test_rescore_includes_dismissed_jobs(store, cfg, comp_cfg):
    """Dismissed jobs still show scores in the archive, so they must stay true."""
    job_id = add(store, "AI UX Researcher", role_fit=0.5)
    store.dismiss_job(job_id, "not interested", "2026-07-27")
    rescore_all(store, cfg, comp_cfg, {})
    assert store.get_job(job_id).role_fit == 0.0


def test_rescore_uses_city_quality_of_life(store, cfg, comp_cfg):
    add(store, "GNSS Engineer")
    cities = {"toulouse": City(name="Toulouse", country="FR",
                               sunshine_hours=2100, nature=7, rent_index=750)}
    rescore_all(store, cfg, comp_cfg, cities)
    assert store.list_jobs()[0].qol_score > 0.5


def test_rescore_reports_nothing_for_an_empty_database(store, cfg, comp_cfg):
    report = rescore_all(store, cfg, comp_cfg, {})
    assert report.rescored == 0
    assert report.no_longer_matching == []


def test_rescore_ranks_a_read_job_by_the_model_fit(store, cfg, comp_cfg):
    """Ranking uses the AI fit once it exists: the model reads the posting
    body, the matcher only ever sees the title."""
    from jobhunt.score import ranking_score

    job_id = add(store, "GNSS Engineer")
    store.save_enrichment(job_id, "text", 0.2, '{"domain_fit": 0.2}', "t")
    rescore_all(store, cfg, comp_cfg, {})
    job = store.get_job(job_id)
    assert job.role_fit == 0.5
    assert job.rank_score == pytest.approx(
        ranking_score(job.comp_score, job.qol_score, 0.5, 0.2, cfg.weights))
    assert job.rank_score < job.total_score


def test_rescore_ranks_an_unread_job_by_the_rule_fit(store, cfg, comp_cfg):
    job_id = add(store, "GNSS Engineer")
    rescore_all(store, cfg, comp_cfg, {})
    job = store.get_job(job_id)
    assert job.rank_score == pytest.approx(job.total_score)


def test_listing_sorts_by_the_ai_fit_not_the_keyword_fit(store, cfg, comp_cfg):
    strong_title = add(store, "GNSS Galileo Receiver Engineer", 0.9, 0.9)
    weak_title = add(store, "Systems Engineer", 0.1, 0.1)
    store.save_enrichment(strong_title, "t", 0.1, '{"domain_fit": 0.1}', "t")
    store.save_enrichment(weak_title, "t", 0.95, '{"domain_fit": 0.95}', "t")
    rescore_all(store, cfg, comp_cfg, {})
    assert [j.id for j in store.list_jobs()] == [weak_title, strong_title]
