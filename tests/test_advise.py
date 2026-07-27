"""The briefing: one model pass across every job, stored so both the CLI and
the browser show the same text instead of each paying for its own."""

import pytest

from jobhunt.models import Employer, Job
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a.db")
    s.initialize()
    return s


class StubLLM:
    def __init__(self, reply="1. TOP PICKS\n[1] Apply here first."):
        self.reply = reply
        self.prompts = []
        self.max_tokens = None

    def complete(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        self.max_tokens = max_tokens
        return self.reply


def seed(store, title="GNSS Engineer", employer="Thales"):
    eid = store.upsert_employer(Employer(name=employer))
    jid = store.upsert_job(Job(employer_id=eid, title=title,
                               url=f"https://x/{title}"))
    return store.get_job(jid)


# --- storing a briefing -------------------------------------------------

def test_a_saved_briefing_comes_back_with_its_timestamp(store):
    store.save_briefing("2026-07-27T10:00:00Z", "the advice", scope="all",
                        job_count=12)
    latest = store.latest_briefing()
    assert latest.text == "the advice"
    assert latest.ts == "2026-07-27T10:00:00Z"
    assert latest.job_count == 12
    assert latest.scope == "all"


def test_latest_briefing_is_the_newest_one(store):
    store.save_briefing("2026-07-01T00:00:00Z", "old")
    store.save_briefing("2026-07-27T00:00:00Z", "new")
    assert store.latest_briefing().text == "new"


def test_no_briefing_yet_is_none_not_an_error(store):
    assert store.latest_briefing() is None


# --- building one -------------------------------------------------------

def test_building_a_briefing_stores_the_model_reply(store):
    from jobhunt.advise import build_briefing

    seed(store)
    llm = StubLLM("here is the advice")
    briefing = build_briefing(store, store.list_jobs(), "profile", "", llm,
                              "2026-07-27T10:00:00Z")

    assert briefing.text == "here is the advice"
    assert store.latest_briefing().text == "here is the advice"


def test_the_briefing_prompt_names_the_employers(store):
    """The employer name lives in another table; without it the model cannot
    say anything about the company."""
    from jobhunt.advise import build_briefing

    seed(store, employer="Beyond Gravity")
    llm = StubLLM()
    build_briefing(store, store.list_jobs(), "profile", "", llm, "t")

    assert "Beyond Gravity" in llm.prompts[0]


def test_the_briefing_asks_for_room_to_answer(store):
    """A per-job verdict fits in a few hundred tokens. Four sections across
    sixty jobs does not, and a truncated briefing loses its last section."""
    from jobhunt.advise import build_briefing

    seed(store)
    llm = StubLLM()
    build_briefing(store, store.list_jobs(), "profile", "", llm, "t")

    assert llm.max_tokens is not None and llm.max_tokens >= 4000


def test_the_briefing_records_how_many_jobs_it_saw(store):
    from jobhunt.advise import build_briefing

    seed(store, title="GNSS Engineer")
    seed(store, title="Radar Engineer")
    llm = StubLLM()
    build_briefing(store, store.list_jobs(), "profile", "", llm, "t",
                   scope="shortlisted")

    latest = store.latest_briefing()
    assert latest.job_count == 2
    assert latest.scope == "shortlisted"


def test_no_jobs_means_no_model_call(store):
    """Never spend a call to be told there is nothing to advise on."""
    from jobhunt.advise import build_briefing

    llm = StubLLM()
    assert build_briefing(store, [], "profile", "", llm, "t") is None
    assert llm.prompts == []
