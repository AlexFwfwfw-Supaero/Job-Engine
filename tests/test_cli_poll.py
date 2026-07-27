import textwrap

import pytest
from typer.testing import CliRunner

from jobhunt import cli as cli_module
from jobhunt.cli import app
from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting
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
        excluded_countries: [GB]
        role_families:
          - {name: gnss, weight: 1.0, keywords: ["gnss", "navigation"]}
    """))
    (config / "cities.yaml").write_text("cities: []\n")
    (config / "comp.yaml").write_text(
        "reference_purchasing_power: 40000\neffective_tax: {DE: 0.4}\n"
        "pli: {DE: 1.0}\nsalary_by_country: {DE: {junior: 60000}}\n"
    )
    (config / "employers.yaml").write_text("employers: []\n")
    (config / "deadlines.yaml").write_text("deadlines: []\n")

    monkeypatch.setenv("JOBHUNT_CONFIG", str(config))
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "jobs.db"))

    store = Store(tmp_path / "jobs.db")
    store.initialize()
    store.upsert_employer(Employer(name="Airbus", ats="workday", country="DE",
                                   poll_enabled=True, ats_endpoint="https://x/api"))
    store.upsert_employer(Employer(name="Small GmbH", ats="manual", country="DE"))
    store.close()
    return tmp_path


@pytest.fixture
def fake_registry(monkeypatch):
    def fetch(employer, client, **kwargs):
        return [
            RawPosting(source="workday", url="https://x/1",
                       title="GNSS Navigation Engineer", city="Munich",
                       country="DE", description="GNSS work"),
            RawPosting(source="workday", url="https://x/2",
                       title="Accountant", city="Munich", country="DE",
                       description="bookkeeping"),
        ]

    monkeypatch.setattr(cli_module, "SOURCE_REGISTRY", {"workday": fetch})
    monkeypatch.setattr(cli_module, "_http_client", lambda: object())


def test_poll_stores_relevant_jobs_only(env, fake_registry):
    result = runner.invoke(app, ["poll"])
    assert result.exit_code == 0, result.output

    store = Store(env / "jobs.db")
    store.initialize()
    assert [j.title for j in store.list_jobs()] == ["GNSS Navigation Engineer"]
    store.close()


def test_poll_reports_seen_and_skipped(env, fake_registry):
    result = runner.invoke(app, ["poll"])
    assert "seen 2" in result.output
    assert "stored 1" in result.output


def test_poll_can_target_one_employer(env, fake_registry):
    result = runner.invoke(app, ["poll", "--employer", "Airbus"])
    assert result.exit_code == 0
    assert "Airbus" in result.output


def test_poll_reports_a_source_error_without_crashing(env, monkeypatch):
    def boom(employer, client, **kwargs):
        raise RuntimeError("endpoint moved")

    monkeypatch.setattr(cli_module, "SOURCE_REGISTRY", {"workday": boom})
    monkeypatch.setattr(cli_module, "_http_client", lambda: object())

    result = runner.invoke(app, ["poll"])
    assert result.exit_code == 0, result.output
    assert "endpoint moved" in result.output


def test_poll_with_nothing_enabled_says_so(env, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "SOURCE_REGISTRY", {})
    monkeypatch.setattr(cli_module, "_http_client", lambda: object())
    store = Store(tmp_path / "jobs.db")
    store.initialize()
    store.upsert_employer(Employer(name="Airbus", ats="workday", country="DE",
                                   poll_enabled=False))
    store.close()

    result = runner.invoke(app, ["poll"])
    assert "no employers" in result.output.lower()
