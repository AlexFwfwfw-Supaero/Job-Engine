import pytest

from jobhunt.links import apply_results, check_jobs, check_link, LinkResult
from jobhunt.models import Employer, Job, Stage
from jobhunt.store import Store


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeClient:
    def __init__(self, by_url=None, raises=False):
        self.by_url = by_url or {}
        self.raises = raises
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if self.raises:
            raise ConnectionError("network down")
        return FakeResponse(self.by_url.get(url, 200))


def test_a_200_is_live():
    assert check_link("https://x/a", FakeClient()) == "live"


def test_a_404_is_dead():
    client = FakeClient({"https://x/gone": 404})
    assert check_link("https://x/gone", client) == "dead"


def test_a_connection_error_is_dead_not_an_exception():
    assert check_link("https://x/a", FakeClient(raises=True)) == "dead"


def test_check_jobs_returns_one_result_per_job():
    jobs = [
        Job(id=1, employer_id=1, title="A", url="https://x/a"),
        Job(id=2, employer_id=1, title="B", url="https://x/b"),
    ]
    client = FakeClient({"https://x/b": 410})
    results = check_jobs(jobs, client, now="2026-07-26T00:00:00Z")
    assert [(r.job_id, r.status) for r in results] == [(1, "live"), (2, "dead")]


def test_check_jobs_skips_jobs_without_an_id():
    jobs = [Job(employer_id=1, title="No id", url="https://x/a")]
    assert check_jobs(jobs, FakeClient(), now="2026-07-26T00:00:00Z") == []


def test_apply_results_writes_status_without_archiving(tmp_path):
    store = Store(tmp_path / "l.db")
    store.initialize()
    eid = store.upsert_employer(Employer(name="Septentrio"))
    jid = store.upsert_job(Job(employer_id=eid, title="A", url="https://x/a"))

    apply_results(store, [LinkResult(jid, "https://x/a", "dead")],
                  now="2026-07-26T00:00:00Z")

    job = store.get_job(jid)
    assert job.link_status == "dead"
    assert job.last_checked == "2026-07-26T00:00:00Z"
    assert job.dismissed is False
    assert job.stage is Stage.SPOTTED
    store.close()
