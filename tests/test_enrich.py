import json

import pytest

from jobhunt.enrich import enrich_jobs
from jobhunt.models import Employer, Job
from jobhunt.store import Store

PROFILE = "GNSS graduate, no German."


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "jobs.db")
    s.initialize()
    s.upsert_employer(Employer(name="Thales", country="FR", city="Toulouse"))
    return s


def add(store, title="GNSS Engineer", url="https://x/1", description=""):
    return store.upsert_job(Job(
        employer_id=1, title=title, url=url, city="Toulouse", country="FR",
        source="workday", first_seen="2026-07-01", last_seen="2026-07-01",
        description=description,
    ))


VERDICT = json.dumps({
    "relevant": True, "domain_fit": 0.9, "reason": "Galileo receiver work",
    "seniority": "junior", "contract": "permanent",
    "german_required": "not needed", "salary_stated": None,
    "angle": "Lead with the thesis.",
})


class FakeLLM:
    def __init__(self, reply=VERDICT):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return self.reply


class FakeFetcher:
    def __init__(self, text="Full posting text about Galileo receivers."):
        self.text = text
        self.urls = []

    def __call__(self, url, client=None):
        self.urls.append(url)
        return self.text


def test_enrich_fetches_text_and_stores_the_verdict(store):
    job_id = add(store)
    fetch = FakeFetcher()
    report = enrich_jobs(store, store.list_jobs(), PROFILE, FakeLLM(), fetch,
                         now="2026-07-27T00:00:00Z")
    job = store.get_job(job_id)
    assert job.description == fetch.text
    assert job.llm_fit == 0.9
    assert json.loads(job.llm_json)["angle"] == "Lead with the thesis."
    assert job.llm_checked == "2026-07-27T00:00:00Z"
    assert report.analysed == 1


def test_enrich_reuses_stored_text_instead_of_refetching(store):
    """Text is fetched once per posting; re-analysis must not re-crawl."""
    add(store, description="already fetched")
    fetch = FakeFetcher()
    enrich_jobs(store, store.list_jobs(), PROFILE, FakeLLM(), fetch, now="t")
    assert fetch.urls == []


def test_enrich_skips_jobs_already_analysed(store):
    job_id = add(store)
    llm = FakeLLM()
    enrich_jobs(store, store.list_jobs(), PROFILE, llm, FakeFetcher(), now="t")
    report = enrich_jobs(store, [store.get_job(job_id)], PROFILE, llm,
                         FakeFetcher(), now="t")
    assert llm.calls == 1
    assert report.skipped == 1


def test_enrich_reanalyses_when_forced(store):
    job_id = add(store)
    llm = FakeLLM()
    enrich_jobs(store, store.list_jobs(), PROFILE, llm, FakeFetcher(), now="t")
    enrich_jobs(store, [store.get_job(job_id)], PROFILE, llm, FakeFetcher(),
                now="t", force=True)
    assert llm.calls == 2


def test_enrich_records_a_failure_without_stopping_the_run(store):
    """One dead link or one bad reply must not abandon the remaining jobs."""
    add(store, url="https://x/1")
    add(store, title="Radar Engineer", url="https://x/2")

    class Flaky(FakeFetcher):
        def __call__(self, url, client=None):
            if url == "https://x/1":
                raise RuntimeError("404")
            return self.text

    report = enrich_jobs(store, store.list_jobs(), PROFILE, FakeLLM(), Flaky(),
                         now="t")
    assert report.analysed == 1
    assert len(report.failed) == 1
    assert "https://x/1" in report.failed[0]


def test_enrich_never_overwrites_the_rule_based_score(store):
    job_id = store.upsert_job(Job(
        employer_id=1, title="GNSS Engineer", url="https://x/9", city="Toulouse",
        country="FR", role_fit=0.5, total_score=0.5,
    ))
    enrich_jobs(store, store.list_jobs(), PROFILE, FakeLLM(), FakeFetcher(),
                now="t")
    job = store.get_job(job_id)
    assert job.role_fit == 0.5
    assert job.total_score == 0.5
    assert job.llm_fit == 0.9


def test_enrich_respects_a_limit(store):
    for i in range(5):
        add(store, url=f"https://x/{i}")
    llm = FakeLLM()
    report = enrich_jobs(store, store.list_jobs(), PROFILE, llm, FakeFetcher(),
                         now="t", limit=2)
    assert llm.calls == 2
    assert report.analysed == 2
