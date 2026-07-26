import textwrap

import pytest
from typer.testing import CliRunner

from jobhunt import cli as cli_module
from jobhunt.cli import app
from jobhunt.models import Employer, Job
from jobhunt.store import Store

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "scoring.yaml").write_text(textwrap.dedent("""
        weights: {comp: 0.3, qol: 0.2, fit: 0.5}
        qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}
        staleness: {applied: [14, 30], phd: [21, 45]}
        role_families:
          - {name: gnss, weight: 1.0, keywords: ["gnss"]}
    """))
    (config / "cities.yaml").write_text("cities: []\n")
    (config / "comp.yaml").write_text(
        "reference_purchasing_power: 40000\neffective_tax: {}\npli: {}\n"
        "salary_by_country: {}\n"
    )
    (config / "employers.yaml").write_text("employers: []\n")
    (config / "deadlines.yaml").write_text("deadlines: []\n")

    monkeypatch.setenv("JOBHUNT_CONFIG", str(config))
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("JOBHUNT_POSTINGS", str(tmp_path / "postings"))

    store = Store(tmp_path / "jobs.db")
    store.initialize()
    eid = store.upsert_employer(Employer(name="Septentrio"))
    store.upsert_job(Job(employer_id=eid, title="GNSS Engineer", url="https://x/1"))
    store.close()
    return tmp_path


def open_store(env):
    s = Store(env / "jobs.db")
    s.initialize()
    return s


def test_priority_sets_the_value(env):
    result = runner.invoke(app, ["priority", "1", "4"])
    assert result.exit_code == 0, result.output
    store = open_store(env)
    assert store.get_job(1).priority == 4
    store.close()


def test_priority_rejects_out_of_range(env):
    result = runner.invoke(app, ["priority", "1", "9"])
    assert result.exit_code != 0
    assert "1-5" in result.output or "0-5" in result.output


def test_note_sets_the_persistent_field(env):
    runner.invoke(app, ["note", "1", "team lead is ex-DLR"])
    store = open_store(env)
    assert store.get_job(1).notes == "team lead is ex-DLR"
    store.close()


def test_archive_then_restore(env):
    runner.invoke(app, ["archive", "1", "--reason", "wrong domain"])
    store = open_store(env)
    assert store.get_job(1).dismissed is True
    store.close()

    runner.invoke(app, ["restore", "1"])
    store = open_store(env)
    assert store.get_job(1).dismissed is False
    store.close()


def test_dismiss_alias_still_archives(env):
    runner.invoke(app, ["dismiss", "1", "--reason", "legacy"])
    store = open_store(env)
    assert store.get_job(1).dismissed is True
    store.close()


def test_refresh_marks_link_status(env, monkeypatch):
    class Dead:
        def raise_for_status(self):
            raise RuntimeError("HTTP 404")

    class Client:
        def get(self, url):
            return Dead()

        def close(self):
            return None

    monkeypatch.setattr(cli_module, "_http_client", lambda: Client())
    result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 0, result.output
    assert "dead" in result.output

    store = open_store(env)
    job = store.get_job(1)
    assert job.link_status == "dead"
    assert job.dismissed is False
    store.close()
