from datetime import date

import pytest
from fastapi.testclient import TestClient

from jobhunt.models import Employer, Job, Stage
from jobhunt.store import Store
from jobhunt.web.app import create_app
from jobhunt.web.deps import Deps


class FakeResponse:
    text = "<h1>GNSS Engineer</h1><p>Galileo receiver work.</p>"

    def raise_for_status(self):
        return None


class FakeClient:
    def get(self, url):
        return FakeResponse()

    def close(self):
        return None


class DeadClient:
    def get(self, url):
        raise ConnectionError("gone")

    def close(self):
        return None


@pytest.fixture
def config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "scoring.yaml").write_text(
        "weights: {comp: 0.3, qol: 0.2, fit: 0.5}\n"
        "qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}\n"
        "staleness: {applied: [14, 30], phd: [21, 45]}\n"
        "role_families: [{name: gnss, weight: 1.0, keywords: [gnss, galileo]}]\n"
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
def db_path(tmp_path):
    path = tmp_path / "r.db"
    s = Store(path)
    s.initialize()
    s.close()
    return path


@pytest.fixture
def deps(db_path, config_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUNT_POSTINGS", str(tmp_path / "postings"))

    def factory():
        s = Store(db_path)
        s.initialize()
        return s

    return Deps(store_factory=factory, config_dir=config_dir,
                today=lambda: date(2026, 7, 26), http_client=FakeClient)


@pytest.fixture
def client(deps):
    return TestClient(create_app(deps), follow_redirects=False)


def seed(db_path, title="GNSS Engineer", url="https://x/1", stage=Stage.SPOTTED):
    s = Store(db_path)
    s.initialize()
    eid = s.upsert_employer(Employer(name="Septentrio"))
    jid = s.upsert_job(Job(employer_id=eid, title=title, url=url))
    if stage is not Stage.SPOTTED:
        s.set_stage(jid, stage, ts="2026-07-01T00:00:00Z")
    s.close()
    return jid


def test_root_redirects_to_overview(client):
    response = client.get("/")
    assert response.status_code in (302, 303, 307)
    assert response.headers["location"] == "/overview"


@pytest.mark.parametrize(
    "slug", ["overview", "search", "interested", "applied", "archive", "deadlines"]
)
def test_every_tab_returns_html(client, slug):
    response = client.get(f"/{slug}")
    assert response.status_code == 200
    assert "<nav>" in response.text


def test_unknown_tab_is_404(client):
    assert client.get("/nonsense").status_code == 404


def test_post_jobs_adds_and_scores(client, db_path):
    response = client.post("/jobs", data={
        "url": "https://example.com/job/1", "employer": "Septentrio",
        "city": "", "country": "", "level": "junior", "tags": "",
    })
    assert response.status_code in (302, 303, 307)

    s = Store(db_path)
    jobs = s.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].title == "GNSS Engineer"
    assert jobs[0].role_fit > 0
    s.close()


def test_post_stage_moves_the_job_and_redirects_back(client, db_path):
    jid = seed(db_path)
    response = client.post(f"/jobs/{jid}/stage",
                           data={"stage": "applied", "return_to": "search"})
    assert response.headers["location"] == "/search"

    s = Store(db_path)
    assert s.get_job(jid).stage is Stage.APPLIED
    s.close()


def test_post_stage_rejects_an_unknown_stage(client, db_path):
    jid = seed(db_path)
    response = client.post(f"/jobs/{jid}/stage",
                           data={"stage": "nonsense", "return_to": "search"})
    assert response.status_code == 400


def test_post_note_sets_the_field(client, db_path):
    jid = seed(db_path)
    client.post(f"/jobs/{jid}/note",
                data={"text": "ex-DLR team lead", "return_to": "interested"})
    s = Store(db_path)
    assert s.get_job(jid).notes == "ex-DLR team lead"
    s.close()


def test_post_priority_sets_the_value(client, db_path):
    jid = seed(db_path)
    client.post(f"/jobs/{jid}/priority", data={"value": "4", "return_to": "interested"})
    s = Store(db_path)
    assert s.get_job(jid).priority == 4
    s.close()


def test_post_priority_rejects_out_of_range(client, db_path):
    jid = seed(db_path)
    response = client.post(f"/jobs/{jid}/priority",
                           data={"value": "42", "return_to": "interested"})
    assert response.status_code == 400


def test_archive_then_restore(client, db_path):
    jid = seed(db_path)
    client.post(f"/jobs/{jid}/archive",
                data={"reason": "surveying", "return_to": "search"})
    s = Store(db_path)
    assert s.get_job(jid).dismissed is True
    s.close()

    client.post(f"/jobs/{jid}/restore", data={"return_to": "archive"})
    s = Store(db_path)
    assert s.get_job(jid).dismissed is False
    s.close()


def test_refresh_marks_dead_links_without_archiving(db_path, config_dir, tmp_path):
    jid = seed(db_path)

    def factory():
        s = Store(db_path)
        s.initialize()
        return s

    deps = Deps(store_factory=factory, config_dir=config_dir,
                today=lambda: date(2026, 7, 26), http_client=DeadClient)
    client = TestClient(create_app(deps), follow_redirects=False)
    client.post("/actions/refresh", data={"return_to": "search"})

    s = Store(db_path)
    job = s.get_job(jid)
    assert job.link_status == "dead"
    assert job.dismissed is False
    s.close()


def test_search_action_reports_that_polling_is_unavailable(client):
    response = client.post("/actions/search", data={"return_to": "search"})
    assert response.status_code in (302, 303, 307)


def test_return_to_only_accepts_known_tabs(client, db_path):
    jid = seed(db_path)
    response = client.post(
        f"/jobs/{jid}/stage",
        data={"stage": "applied", "return_to": "https://evil.example.com"},
    )
    assert response.headers["location"] == "/overview"


def test_rescore_action_updates_scores_and_redirects(client, db_path):
    from jobhunt.models import Employer, Job

    store = Store(db_path)
    store.initialize()
    store.upsert_employer(Employer(name="Thales", country="FR", city="Toulouse"))
    job_id = store.upsert_job(Job(
        employer_id=1, title="Totally Unrelated Role", url="https://x/1",
        city="Toulouse", country="FR", source="workday",
        first_seen="2026-07-01", last_seen="2026-07-01",
        role_fit=0.9, total_score=0.9,
    ))
    response = client.post("/actions/rescore", data={"return_to": "search"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/search"
    assert store.get_job(job_id).role_fit == 0.0
