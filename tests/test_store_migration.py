import sqlite3

import pytest

from jobhunt.models import Employer, EventKind, Job, Stage
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "m.db")
    s.initialize()
    yield s
    s.close()


def make_job(store, title="GNSS Engineer", url="https://x/1"):
    eid = store.upsert_employer(Employer(name="Septentrio"))
    return store.upsert_job(Job(employer_id=eid, title=title, url=url))


def test_new_columns_default_sensibly(store):
    job = store.get_job(make_job(store))
    assert job.notes == ""
    assert job.priority == 0
    assert job.link_status == "unknown"
    assert job.last_checked is None


def test_migration_adds_columns_to_a_preexisting_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE employers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            country TEXT DEFAULT '', city TEXT DEFAULT '', ats TEXT DEFAULT 'manual',
            ats_endpoint TEXT DEFAULT '', careers_url TEXT DEFAULT '',
            tags TEXT DEFAULT '[]', poll_enabled INTEGER DEFAULT 0, last_polled TEXT,
            last_manual_check TEXT, notes TEXT DEFAULT '');
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, employer_id INTEGER NOT NULL,
            title TEXT NOT NULL, url TEXT NOT NULL UNIQUE, city TEXT DEFAULT '',
            country TEXT DEFAULT '', source TEXT DEFAULT 'manual',
            snapshot_path TEXT DEFAULT '', first_seen TEXT, last_seen TEXT,
            stage TEXT DEFAULT 'spotted', dismissed INTEGER DEFAULT 0,
            dismiss_reason TEXT DEFAULT '', role_fit REAL DEFAULT 0,
            comp_score REAL DEFAULT 0, qol_score REAL DEFAULT 0,
            total_score REAL DEFAULT 0, salary_stated REAL, level TEXT DEFAULT 'junior',
            language_flags TEXT DEFAULT '[]', tags TEXT DEFAULT '[]',
            base_cv TEXT DEFAULT '', angle TEXT DEFAULT '', applied_on TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
            ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT DEFAULT '');
        INSERT INTO employers (name) VALUES ('Old Co');
        INSERT INTO jobs (employer_id, title, url) VALUES (1, 'Legacy', 'https://x/legacy');
    """)
    conn.commit()
    conn.close()

    store = Store(path)
    store.initialize()
    job = store.list_jobs()[0]
    assert job.title == "Legacy"
    assert job.priority == 0
    assert job.link_status == "unknown"
    store.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    for _ in range(3):
        s = Store(path)
        s.initialize()
        s.close()
    s = Store(path)
    s.initialize()
    assert s.list_jobs() == []
    s.close()


def test_set_note_replaces_and_logs_an_event(store):
    jid = make_job(store)
    store.set_note(jid, "team lead is ex-DLR", ts="2026-07-26T00:00:00Z")
    assert store.get_job(jid).notes == "team lead is ex-DLR"

    store.set_note(jid, "actually ex-Fraunhofer", ts="2026-07-27T00:00:00Z")
    assert store.get_job(jid).notes == "actually ex-Fraunhofer"

    events = store.list_events(jid)
    assert len(events) == 2
    assert all(e.kind is EventKind.NOTE for e in events)


def test_set_priority_stores_and_validates(store):
    jid = make_job(store)
    store.set_priority(jid, 4, ts="2026-07-26T00:00:00Z")
    assert store.get_job(jid).priority == 4

    with pytest.raises(ValueError):
        store.set_priority(jid, 6, ts="2026-07-26T00:00:00Z")
    with pytest.raises(ValueError):
        store.set_priority(jid, -1, ts="2026-07-26T00:00:00Z")


def test_priority_survives_a_stage_change(store):
    jid = make_job(store)
    store.set_priority(jid, 5, ts="2026-07-26T00:00:00Z")
    store.set_stage(jid, Stage.APPLIED, ts="2026-07-27T00:00:00Z")
    assert store.get_job(jid).priority == 5


def test_set_link_status_records_the_check_time(store):
    jid = make_job(store)
    store.set_link_status(jid, "dead", ts="2026-07-26T09:00:00Z")
    job = store.get_job(jid)
    assert job.link_status == "dead"
    assert job.last_checked == "2026-07-26T09:00:00Z"


def test_dead_link_does_not_archive_the_job(store):
    jid = make_job(store)
    store.set_link_status(jid, "dead", ts="2026-07-26T09:00:00Z")
    assert store.get_job(jid).dismissed is False
    assert len(store.list_jobs()) == 1


def test_archive_and_restore_round_trip(store):
    jid = make_job(store)
    store.archive_job(jid, "wrong domain", ts="2026-07-26T00:00:00Z")
    assert store.list_jobs() == []
    assert store.get_job(jid).dismiss_reason == "wrong domain"

    store.restore_job(jid, ts="2026-07-27T00:00:00Z")
    restored = store.get_job(jid)
    assert restored.dismissed is False
    assert restored.dismiss_reason == ""
    assert len(store.list_jobs()) == 1


def test_dismiss_job_still_works_as_an_alias(store):
    jid = make_job(store)
    store.dismiss_job(jid, "legacy caller", ts="2026-07-26T00:00:00Z")
    assert store.get_job(jid).dismissed is True


def test_list_jobs_filters_by_multiple_stages(store):
    eid = store.upsert_employer(Employer(name="Thales"))
    a = store.upsert_job(Job(employer_id=eid, title="A", url="https://x/a"))
    b = store.upsert_job(Job(employer_id=eid, title="B", url="https://x/b"))
    store.upsert_job(Job(employer_id=eid, title="C", url="https://x/c"))
    store.set_stage(a, Stage.APPLIED, ts="2026-07-26T00:00:00Z")
    store.set_stage(b, Stage.INTERVIEW, ts="2026-07-26T00:00:00Z")

    got = store.list_jobs(stages=[Stage.APPLIED, Stage.INTERVIEW])
    assert sorted(j.title for j in got) == ["A", "B"]
