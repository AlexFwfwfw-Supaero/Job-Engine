import pytest

from jobhunt.models import Employer, EventKind, Job, Stage
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.initialize()
    yield s
    s.close()


def test_employer_roundtrip(store):
    eid = store.upsert_employer(
        Employer(name="Septentrio", country="BE", city="Leuven", tags=["gnss"])
    )
    got = store.get_employer(eid)
    assert got is not None
    assert got.name == "Septentrio"
    assert got.tags == ["gnss"]


def test_upsert_employer_by_name_updates_not_duplicates(store):
    first = store.upsert_employer(Employer(name="GMV", country="ES"))
    second = store.upsert_employer(Employer(name="GMV", country="ES", city="Madrid"))
    assert first == second
    assert len(store.list_employers()) == 1
    assert store.get_employer(first).city == "Madrid"


def test_job_roundtrip(store):
    eid = store.upsert_employer(Employer(name="u-blox"))
    jid = store.upsert_job(
        Job(employer_id=eid, title="GNSS Engineer", url="https://x/1",
            language_flags=["de"], first_seen="2026-07-26T10:00:00Z")
    )
    got = store.get_job(jid)
    assert got.title == "GNSS Engineer"
    assert got.stage is Stage.SPOTTED
    assert got.language_flags == ["de"]


def test_upsert_job_on_same_url_preserves_stage_and_updates_last_seen(store):
    eid = store.upsert_employer(Employer(name="DLR"))
    jid = store.upsert_job(
        Job(employer_id=eid, title="PNT Engineer", url="https://x/2",
            first_seen="2026-07-01T00:00:00Z", last_seen="2026-07-01T00:00:00Z")
    )
    store.set_stage(jid, Stage.APPLIED, ts="2026-07-10T00:00:00Z")

    again = store.upsert_job(
        Job(employer_id=eid, title="PNT Engineer", url="https://x/2",
            last_seen="2026-07-20T00:00:00Z")
    )
    assert again == jid
    job = store.get_job(jid)
    assert job.stage is Stage.APPLIED
    assert job.last_seen == "2026-07-20T00:00:00Z"
    assert job.first_seen == "2026-07-01T00:00:00Z"


def test_dismissed_jobs_hidden_by_default_and_survive_reupsert(store):
    eid = store.upsert_employer(Employer(name="Noise Corp"))
    jid = store.upsert_job(Job(employer_id=eid, title="Land Surveyor", url="https://x/3"))
    store.dismiss_job(jid, "surveying, not engineering", ts="2026-07-26T00:00:00Z")

    assert store.list_jobs() == []
    assert len(store.list_jobs(include_dismissed=True)) == 1

    store.upsert_job(Job(employer_id=eid, title="Land Surveyor", url="https://x/3"))
    assert store.list_jobs() == []
    assert store.get_job(jid).dismiss_reason == "surveying, not engineering"


def test_list_jobs_filters_by_stage(store):
    eid = store.upsert_employer(Employer(name="Thales"))
    a = store.upsert_job(Job(employer_id=eid, title="A", url="https://x/a"))
    store.upsert_job(Job(employer_id=eid, title="B", url="https://x/b"))
    store.set_stage(a, Stage.APPLIED, ts="2026-07-26T00:00:00Z")

    applied = store.list_jobs(stage=Stage.APPLIED)
    assert [j.title for j in applied] == ["A"]


def test_events_are_appended_and_ordered(store):
    eid = store.upsert_employer(Employer(name="ESA"))
    jid = store.upsert_job(Job(employer_id=eid, title="YGT", url="https://x/y"))
    store.add_event(jid, EventKind.NOTE, "emailed the group", ts="2026-07-02T00:00:00Z")
    store.add_event(jid, EventKind.NOTE, "no reply", ts="2026-07-20T00:00:00Z")

    events = store.list_events(jid)
    assert [e.text for e in events] == ["emailed the group", "no reply"]


def test_set_stage_records_an_event(store):
    eid = store.upsert_employer(Employer(name="Safran"))
    jid = store.upsert_job(Job(employer_id=eid, title="Nav Engineer", url="https://x/s"))
    store.set_stage(jid, Stage.APPLIED, ts="2026-07-26T00:00:00Z")

    events = store.list_events(jid)
    assert len(events) == 1
    assert events[0].kind is EventKind.STAGE
    assert "applied" in events[0].text
