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


def test_analyse_without_a_model_explains_itself(client, db_path, monkeypatch):
    """The AI layer is optional; its absence must read as a message, not a crash."""
    from jobhunt.models import Employer, Job

    store = Store(db_path)
    store.initialize()
    store.upsert_employer(Employer(name="Thales", country="FR"))
    job_id = store.upsert_job(Job(employer_id=1, title="GNSS Engineer",
                                  url="https://x/1"))
    response = client.post(f"/jobs/{job_id}/analyse", data={"return_to": "search"})
    assert response.status_code == 503
    assert "claude" in response.json()["detail"]


def test_analyse_stores_the_verdict_when_a_model_is_available(deps, db_path):
    import json as _json

    from fastapi.testclient import TestClient

    from jobhunt.models import Employer, Job
    from jobhunt.web.app import create_app

    store = Store(db_path)
    store.initialize()
    store.upsert_employer(Employer(name="Thales", country="FR"))
    job_id = store.upsert_job(Job(employer_id=1, title="GNSS Engineer",
                                  url="https://x/1", role_fit=0.5))

    class FakeLLM:
        def complete(self, prompt):
            return _json.dumps({"relevant": True, "domain_fit": 0.9,
                                "reason": "receiver work", "angle": "thesis"})

    deps.llm = lambda: FakeLLM()
    deps.profile = lambda: "GNSS graduate"
    store.save_enrichment(job_id, "Long posting text about Galileo receiver "
                          "signal processing in Toulouse.", None, "", "")
    client = TestClient(create_app(deps), follow_redirects=False)
    response = client.post(f"/jobs/{job_id}/analyse", data={"return_to": "search"})

    assert response.status_code == 303
    job = store.get_job(job_id)
    assert job.llm_fit == 0.9
    assert job.role_fit == 0.5


def _seed(db_path, titles):
    from jobhunt.models import Employer, Job

    store = Store(db_path)
    store.initialize()
    store.upsert_employer(Employer(name="Thales", country="FR", city="Toulouse"))
    ids = []
    for i, title in enumerate(titles):
        ids.append(store.upsert_job(Job(
            employer_id=1, title=title, url=f"https://x/{i}", city="Toulouse",
            country="FR", role_fit=0.9, total_score=0.9,
            description="A long posting body about navigation work in Toulouse.",
        )))
    return store, ids


class _StubLLM:
    def __init__(self, fit=0.2):
        import json as _json

        self.reply = _json.dumps({"relevant": True, "domain_fit": fit,
                                  "reason": "read", "angle": "a"})
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return self.reply


def test_rescore_action_also_has_the_model_read_unread_jobs(deps, db_path):
    """'Read new' means the model reads too, not just a rule pass."""
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    store, ids = _seed(db_path, ["GNSS Engineer", "Radar Engineer"])
    llm = _StubLLM()
    deps.llm = lambda: llm
    deps.profile = lambda: "GNSS graduate"
    deps.background = lambda fn: fn()  # run inline so the test is not a race

    client = TestClient(create_app(deps), follow_redirects=False)
    response = client.post("/actions/rescore", data={"return_to": "search"})

    assert response.status_code == 303
    assert llm.calls == 2
    assert store.get_job(ids[0]).llm_fit == 0.2


def test_rescore_action_leaves_already_read_jobs_alone(deps, db_path):
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    store, ids = _seed(db_path, ["GNSS Engineer"])
    store.save_enrichment(ids[0], "text", 0.4, '{"domain_fit": 0.4}', "t")
    llm = _StubLLM()
    deps.llm = lambda: llm
    deps.background = lambda fn: fn()

    TestClient(create_app(deps), follow_redirects=False).post(
        "/actions/rescore", data={"return_to": "search"})
    assert llm.calls == 0


def test_rescore_action_without_a_model_still_applies_the_rules(deps, db_path):
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    store, ids = _seed(db_path, ["Totally Unrelated Role"])
    client = TestClient(create_app(deps), follow_redirects=False)
    assert client.post("/actions/rescore",
                       data={"return_to": "search"}).status_code == 303
    assert store.get_job(ids[0]).role_fit == 0.0


# --- the advice tab -----------------------------------------------------

class _StubAdvisor:
    def __init__(self) -> None:
        self.calls = 0
        self.max_tokens = None

    def complete(self, prompt, max_tokens=None):
        self.calls += 1
        self.max_tokens = max_tokens
        return "TOP PICKS\n[1] Apply here first."


def test_advice_tab_renders_without_a_briefing(client):
    response = client.get("/advice")
    assert response.status_code == 200
    assert "<nav>" in response.text


def test_advice_action_writes_a_briefing_and_redirects(deps, db_path):
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    seed(db_path)
    advisor = _StubAdvisor()
    deps.llm = lambda: advisor
    deps.profile = lambda: "GNSS graduate"
    deps.background = lambda fn: fn()

    client = TestClient(create_app(deps), follow_redirects=False)
    response = client.post("/actions/advise", data={"scope": "all"})

    assert response.status_code == 303
    assert advisor.calls == 1
    store = Store(db_path)
    assert "Apply here first" in store.latest_briefing().text


def test_the_briefing_is_shown_on_the_advice_tab(deps, db_path, client):
    store = Store(db_path)
    store.initialize()
    store.save_briefing("2026-07-27T10:00:00Z", "Apply to [3] first",
                        scope="all", job_count=9)
    store.close()

    body = client.get("/advice").text
    assert "Apply to [3] first" in body
    assert "2026-07-27" in body


def test_advice_action_without_a_model_says_so_rather_than_crashing(deps, db_path):
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    seed(db_path)
    client = TestClient(create_app(deps), follow_redirects=False)
    assert client.post("/actions/advise", data={"scope": "all"}).status_code == 503


def test_advice_action_on_an_empty_database_spends_no_call(deps):
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    advisor = _StubAdvisor()
    deps.llm = lambda: advisor
    deps.background = lambda fn: fn()
    client = TestClient(create_app(deps), follow_redirects=False)

    assert client.post("/actions/advise", data={"scope": "all"}).status_code == 303
    assert advisor.calls == 0


def test_advice_action_can_be_limited_to_the_shortlist(deps, db_path):
    """Sixty jobs of digest is a lot of prompt; advising on the shortlist
    alone is the cheaper, sharper run."""
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    seed(db_path, title="GNSS Engineer", url="https://x/1")
    seed(db_path, title="Radar Engineer", url="https://x/2",
         stage=Stage.SHORTLISTED)
    advisor = _StubAdvisor()
    deps.llm = lambda: advisor
    deps.background = lambda fn: fn()

    client = TestClient(create_app(deps), follow_redirects=False)
    client.post("/actions/advise", data={"scope": "shortlisted"})

    prompt = advisor  # the stub keeps only the last call
    assert prompt.calls == 1
    store = Store(db_path)
    assert store.latest_briefing().job_count == 1
    assert store.latest_briefing().scope == "shortlisted"


# --- manual entry: title and pasted text -------------------------------

def test_a_manual_entry_can_carry_its_own_title(client, db_path):
    """The fetched page gives a title only when the site is fetchable and
    sensibly structured. For a posting you paste by hand it usually is not."""
    client.post("/jobs", data={
        "url": "https://example.com/job/7", "employer": "Septentrio",
        "title": "Ingénieur Navigation GNSS", "description": "",
        "city": "", "country": "", "level": "junior", "tags": "",
    })
    store = Store(db_path)
    assert store.list_jobs()[0].title == "Ingénieur Navigation GNSS"


def test_a_pasted_description_is_stored_for_the_model_to_read(deps, db_path):
    """Without this the model has nothing to read: it re-fetches the URL, and
    the postings you enter by hand are exactly the ones that cannot be
    fetched."""
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    body = ("We are looking for a GNSS receiver engineer to work on Galileo "
            "signal processing, integrity monitoring and RTK positioning.")
    client = TestClient(create_app(deps), follow_redirects=False)
    client.post("/jobs", data={
        "url": "https://example.com/job/8", "employer": "Septentrio",
        "title": "GNSS Receiver Engineer", "description": body,
        "city": "", "country": "", "level": "junior", "tags": "",
    })

    job = Store(db_path).list_jobs()[0]
    assert "integrity monitoring" in job.description


def test_a_pasted_description_is_what_gets_scored(deps, db_path):
    """The fake client serves an unrelated page. If the pasted text were
    ignored, the score would come from that page instead."""
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    client = TestClient(create_app(deps), follow_redirects=False)
    client.post("/jobs", data={
        "url": "https://example.com/job/9", "employer": "Septentrio",
        "title": "Systems Engineer", "description": "Galileo GNSS work.",
        "city": "", "country": "", "level": "junior", "tags": "",
    })
    assert Store(db_path).list_jobs()[0].role_fit > 0


def test_a_dead_url_does_not_lose_a_hand_entered_posting(deps, db_path):
    """A LinkedIn posting cannot be fetched at all. Losing the entry because
    the snapshot failed would defeat the point of manual entry."""
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    deps.http_client = DeadClient
    client = TestClient(create_app(deps), follow_redirects=False)
    response = client.post("/jobs", data={
        "url": "https://www.linkedin.com/jobs/view/123", "employer": "Expleo",
        "title": "Ingénieur GNSS", "description": "Galileo receiver work.",
        "city": "Toulouse", "country": "FR", "level": "junior", "tags": "",
    })

    assert response.status_code == 303
    jobs = Store(db_path).list_jobs()
    assert jobs[0].title == "Ingénieur GNSS"
    assert jobs[0].snapshot_path == ""


def test_a_dead_url_with_nothing_pasted_still_fails_loudly(deps):
    """Silently storing an empty job would be worse than an error."""
    import pytest as _pytest
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    deps.http_client = DeadClient
    client = TestClient(create_app(deps), follow_redirects=False)
    with _pytest.raises(ConnectionError):
        client.post("/jobs", data={
            "url": "https://www.linkedin.com/jobs/view/123",
            "employer": "Expleo", "title": "", "description": "",
            "city": "", "country": "", "level": "junior", "tags": "",
        })


def test_a_fetched_page_is_stored_as_text_not_markup(deps, db_path):
    """The description column feeds the model. Handing it raw HTML wastes the
    budget on tags."""
    from fastapi.testclient import TestClient

    from jobhunt.web.app import create_app

    client = TestClient(create_app(deps), follow_redirects=False)
    client.post("/jobs", data={
        "url": "https://example.com/job/10", "employer": "Septentrio",
        "title": "", "description": "",
        "city": "", "country": "", "level": "junior", "tags": "",
    })
    job = Store(db_path).list_jobs()[0]
    assert "<h1>" not in job.description
    assert "Galileo receiver work" in job.description
