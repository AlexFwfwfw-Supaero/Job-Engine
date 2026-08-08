"""The live status lines under 'Search now' and 'Rescore all'."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from jobhunt.models import Employer
from jobhunt.poll import PollReport
from jobhunt.store import Store
from jobhunt.web.app import create_app
from jobhunt.web.deps import Deps
from jobhunt.web.progress import POLL, PROGRESS, AiProgress, PollProgress


# --- the sentences ------------------------------------------------------

def test_a_record_that_never_ran_says_nothing():
    assert PollProgress().text == ""
    assert AiProgress().text == ""


def test_a_running_sweep_names_the_employer_and_the_counts():
    p = PollProgress()
    p.start(17)
    p.starting("ALTEN")
    p.completed(PollReport(employer="GMV", source="workday", stored=34, new=2))
    assert p.text == "Searching ALTEN… 1/17 employers, 34 matched, 2 new"


def test_a_finished_sweep_summarises_until_the_next_one():
    p = PollProgress()
    p.start(2)
    p.completed(PollReport(employer="GMV", source="workday", stored=3, new=1))
    p.completed(PollReport(employer="OHB", source="workday", stored=1, new=0))
    p.finish()
    assert p.text == "Polled 2 employer(s), 4 matched, 1 new."


def test_a_finished_sweep_names_what_broke():
    """A parser that stopped working used to read as a quiet market."""
    p = PollProgress()
    p.start(1)
    p.completed(PollReport(employer="OHB", source="workday",
                           error="HTTPError: 503"))
    p.finish()
    assert p.text == ("Polled 1 employer(s), 0 matched, 0 new, "
                      "1 failed (OHB: HTTPError: 503).")


def test_the_read_counts_up_during_the_run():
    p = AiProgress()
    p.start(39)
    assert p.text == "0/39 analysed"
    for _ in range(5):
        p.step()
    assert p.text == "5/39 analysed"


def test_a_finished_read_reports_what_it_managed():
    p = AiProgress()
    p.start(3)
    p.finish(2, ["https://x/1: ValueError: no description found"])
    assert p.text == "Model read 2 posting(s), 1 failed."


def test_a_finished_read_keeps_why_each_one_failed():
    """"28 failed" and nothing else gives you nowhere to start."""
    p = AiProgress()
    p.start(2)
    p.finish(1, ["https://gmv.csod.com/4818: ValueError: no description found"])
    assert p.as_dict()["failures"] == [
        "https://gmv.csod.com/4818: ValueError: no description found"]


# --- the endpoint and the route ----------------------------------------

@pytest.fixture(autouse=True)
def clean_records():
    """The records are module-level singletons; tests must not inherit state."""
    for record in (POLL, PROGRESS):
        record.__init__()
    yield
    for record in (POLL, PROGRESS):
        record.__init__()


class FakeClient:
    """Refuses every request. Workday skips a term it cannot fetch, so the
    sweep completes with nothing found rather than erroring."""

    def get(self, *args, **kwargs):
        raise ConnectionError("no network in tests")

    post = get

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
    path = tmp_path / "p.db"
    s = Store(path)
    s.initialize()
    s.upsert_employer(Employer(name="GMV", ats="workday", poll_enabled=True,
                               country="ES",
                               ats_endpoint="https://gmv.example/jobs"))
    s.close()
    return path


@pytest.fixture
def deps(db_path, config_dir):
    def factory():
        s = Store(db_path)
        s.initialize()
        return s

    return Deps(store_factory=factory, config_dir=config_dir,
                today=lambda: date(2026, 8, 8), http_client=FakeClient,
                background=lambda fn: fn())


@pytest.fixture
def client(deps):
    return TestClient(create_app(deps), follow_redirects=False)


def test_progress_endpoint_carries_both_records(client):
    body = client.get("/api/progress").json()
    assert set(body) == {"poll", "ai"}
    assert body["poll"]["running"] is False
    assert body["poll"]["text"] == ""


def test_the_tab_route_does_not_swallow_the_endpoint(client):
    """/{slug} is a catch-all; /api/progress must not resolve to a 404 tab."""
    assert client.get("/api/progress").status_code == 200
    assert client.get("/nosuchtab").status_code == 404


def test_search_returns_at_once_and_records_the_sweep(client):
    response = client.post("/actions/search", data={"return_to": "search"})
    assert response.status_code == 303
    assert response.headers["location"] == "/search"

    # background runs inline here, so by now the sweep is over.
    poll = client.get("/api/progress").json()["poll"]
    assert poll["running"] is False
    assert (poll["done"], poll["total"]) == (1, 1)
    assert poll["text"] == "Polled 1 employer(s), 0 matched, 0 new."


def test_a_second_search_while_one_is_running_is_refused(deps):
    """Two sweeps at once would hammer every board twice."""
    started = []
    deps.background = lambda fn: started.append(fn)  # never actually runs
    client = TestClient(create_app(deps), follow_redirects=False)

    client.post("/actions/search", data={"return_to": "search"})
    client.post("/actions/search", data={"return_to": "search"})
    assert len(started) == 1


def test_search_does_nothing_when_no_employer_is_polled(deps, db_path):
    store = Store(db_path)
    store.upsert_employer(Employer(name="GMV", ats="workday",
                                   poll_enabled=False, country="ES"))
    store.close()
    started = []
    deps.background = lambda fn: started.append(fn)
    client = TestClient(create_app(deps), follow_redirects=False)

    assert client.post("/actions/search",
                       data={"return_to": "search"}).status_code == 303
    assert started == []
    assert POLL.running is False


def test_the_spotted_table_can_be_sorted_three_ways(deps, db_path):
    """The default blend buries a strong reading under a weak salary band."""
    from jobhunt.models import Job
    from jobhunt.web.views import search_context

    store = Store(db_path)
    store.initialize()

    def spot(title, url, total, fit=None, rank=None):
        jid = store.upsert_job(Job(
            employer_id=1, title=title, url=url, country="FR",
            source="workday", first_seen="t", last_seen="t",
            total_score=total))
        if fit is not None:
            store.save_enrichment(jid, "text", fit, "{}", "t", rank)
        return jid

    # The real shape from the live database: a technician role the model rated
    # highest, outranking a research role with a much better base score.
    weak_read = spot("Technicien essais", "https://x/1", 0.31, 0.95, 0.78)
    strong_base = spot("Ingénieur recherche radar", "https://x/2", 0.81,
                       0.90, 0.76)
    unread = spot("Navigation Engineer", "https://x/3", 0.70)
    store.close()

    def ids(sort):
        return [r.job.id for r in search_context(
            deps.store_factory(), deps, sort=sort)["rows"]]

    assert ids("rank") == [weak_read, strong_base, unread]
    assert ids("ai") == [weak_read, strong_base, unread]
    assert ids("score") == [strong_base, unread, weak_read]


def test_an_unread_job_sorts_last_by_ai_fit(deps, db_path):
    """No reading is not the same as a reading of zero."""
    from jobhunt.models import Job
    from jobhunt.web.views import search_context

    store = Store(db_path)
    store.initialize()
    unread = store.upsert_job(Job(
        employer_id=1, title="Unread", url="https://x/1", country="FR",
        source="workday", first_seen="t", last_seen="t", total_score=0.9))
    rejected = store.upsert_job(Job(
        employer_id=1, title="Read and rejected", url="https://x/2",
        country="FR", source="workday", first_seen="t", last_seen="t",
        total_score=0.1))
    store.save_enrichment(rejected, "text", 0.0, "{}", "t")
    store.close()

    rows = search_context(deps.store_factory(), deps, sort="ai")["rows"]
    assert [r.job.id for r in rows] == [rejected, unread]


def test_an_unknown_sort_falls_back_instead_of_erroring(client):
    """The value arrives from a query string."""
    assert client.get("/search?sort=../../etc/passwd").status_code == 200


def test_the_search_page_renders_the_status_lines(client):
    client.post("/actions/search", data={"return_to": "search"})
    page = client.get("/search").text
    assert 'id="poll-status"' in page
    assert 'id="ai-status"' in page
    # Rendered server-side, so the status is right with JavaScript off.
    assert "Polled 1 employer(s)" in page
