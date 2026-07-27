import textwrap

import pytest
from typer.testing import CliRunner

from jobhunt import cli as cli_module
from jobhunt.cli import app
from jobhunt.models import Stage
from jobhunt.store import Store

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "scoring.yaml").write_text(textwrap.dedent("""
        weights: {comp: 0.3, qol: 0.2, fit: 0.5}
        qol_weights: {sunshine: 0.5, nature: 0.3, rent: 0.2}
        sunshine_range: [1500, 2500]
        rent_range: [500, 1500]
        excluded_countries: [GB]
        known_languages: [en, fr]
        language_keywords: {de: ["fluent german"]}
        negative_keywords: ["land surveyor"]
        staleness: {applied: [14, 30], phd: [21, 45]}
        role_families:
          - {name: gnss, weight: 1.0, keywords: ["gnss", "galileo"]}
    """))
    (config / "cities.yaml").write_text(textwrap.dedent("""
        cities:
          - {name: Munich, country: DE, sunshine_hours: 1777, nature: 9, rent_index: 1400}
    """))
    (config / "comp.yaml").write_text(textwrap.dedent("""
        reference_purchasing_power: 40000
        effective_tax: {DE: 0.4}
        pli: {DE: 1.0}
        salary_by_country: {DE: {junior: 60000}}
    """))
    (config / "employers.yaml").write_text(textwrap.dedent("""
        employers:
          - {name: Rohde & Schwarz, country: DE, city: Munich, ats: manual,
             careers_url: "https://example.com/careers", tags: [gnss]}
    """))
    (config / "deadlines.yaml").write_text(textwrap.dedent("""
        deadlines:
          - {name: ESA YGT, employer: ESA, closes: 2026-08-15, lead_days: 60,
             url: "https://example.com/ygt"}
    """))

    monkeypatch.setenv("JOBHUNT_CONFIG", str(config))
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("JOBHUNT_POSTINGS", str(tmp_path / "postings"))
    monkeypatch.setenv("JOBHUNT_DASHBOARD", str(tmp_path / "dashboard.html"))
    monkeypatch.setattr(
        cli_module, "_today", lambda: __import__("datetime").date(2026, 7, 26)
    )
    return tmp_path


class FakeResponse:
    text = "<h1>GNSS Engineer</h1><p>Galileo receiver work in Munich.</p>"

    def raise_for_status(self):
        return None


class FakeClient:
    def get(self, url):
        return FakeResponse()

    def close(self):
        return None


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(cli_module, "_http_client", lambda: FakeClient())


def test_sync_employers_loads_the_yaml(env):
    result = runner.invoke(app, ["sync-employers"])
    assert result.exit_code == 0, result.output
    store = Store(env / "jobs.db")
    assert [e.name for e in store.list_employers()] == ["Rohde & Schwarz"]
    store.close()


def test_add_fetches_snapshots_and_scores(env, fake_http):
    runner.invoke(app, ["sync-employers"])
    result = runner.invoke(app, [
        "add", "https://example.com/job/1", "--employer", "Rohde & Schwarz",
        "--city", "Munich", "--country", "DE",
    ])
    assert result.exit_code == 0, result.output

    store = Store(env / "jobs.db")
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].title == "GNSS Engineer"
    assert jobs[0].role_fit > 0
    assert jobs[0].total_score > 0
    assert (env / "postings").exists()
    assert jobs[0].snapshot_path
    store.close()


def test_add_creates_an_unknown_employer_on_demand(env, fake_http):
    result = runner.invoke(app, [
        "add", "https://example.com/job/2", "--employer", "Some New GmbH",
    ])
    assert result.exit_code == 0, result.output
    store = Store(env / "jobs.db")
    assert store.find_employer_by_name("Some New GmbH") is not None
    store.close()


def test_add_stores_an_irrelevant_posting_with_a_warning(env, fake_http, monkeypatch):
    class Irrelevant(FakeResponse):
        text = "<h1>Accountant</h1><p>Bookkeeping.</p>"

    monkeypatch.setattr(
        cli_module, "_http_client",
        lambda: type("C", (), {"get": lambda self, url: Irrelevant(),
                               "close": lambda self: None})(),
    )
    result = runner.invoke(app, ["add", "https://example.com/job/3",
                                 "--employer", "X"])
    assert result.exit_code == 0
    assert "no role family" in result.output
    store = Store(env / "jobs.db")
    assert len(store.list_jobs()) == 1
    store.close()


def test_stage_transition_and_listing(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/4", "--employer", "X"])
    store = Store(env / "jobs.db")
    job_id = store.list_jobs()[0].id
    store.close()

    assert runner.invoke(app, ["stage", str(job_id), "applied"]).exit_code == 0
    listing = runner.invoke(app, ["list", "--stage", "applied"])
    assert "GNSS Engineer" in listing.output

    store = Store(env / "jobs.db")
    assert store.get_job(job_id).stage is Stage.APPLIED
    store.close()


def test_invalid_stage_is_rejected(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/5", "--employer", "X"])
    result = runner.invoke(app, ["stage", "1", "nonsense"])
    assert result.exit_code != 0
    assert "nonsense" in result.output


def test_dismiss_hides_from_list_and_shows_in_review(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/6", "--employer", "X"])
    runner.invoke(app, ["dismiss", "1", "--reason", "wrong domain"])

    assert "GNSS Engineer" not in runner.invoke(app, ["list"]).output
    review = runner.invoke(app, ["dismissed", "--review"])
    assert "wrong domain" in review.output


def test_reject_sets_the_rejected_stage(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/7", "--employer", "X"])
    runner.invoke(app, ["reject", "1"])
    store = Store(env / "jobs.db")
    assert store.get_job(1).stage is Stage.REJECTED
    store.close()


def test_note_is_recorded_as_an_event(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/8", "--employer", "X"])
    runner.invoke(app, ["note", "1", "spoke to the team lead"])
    show = runner.invoke(app, ["show", "1"])
    assert "spoke to the team lead" in show.output


def test_due_lists_deadlines(env):
    runner.invoke(app, ["sync-employers"])
    result = runner.invoke(app, ["due"])
    assert "ESA YGT" in result.output


def test_report_writes_the_dashboard(env, fake_http):
    runner.invoke(app, ["sync-employers"])
    runner.invoke(app, ["add", "https://example.com/job/9", "--employer",
                        "Rohde & Schwarz", "--city", "Munich", "--country", "DE"])
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    dashboard = env / "dashboard.html"
    assert dashboard.exists()
    assert "GNSS Engineer" in dashboard.read_text(encoding="utf-8")


def test_add_takes_a_pasted_posting_from_a_file(env, fake_http):
    """The CLI counterpart of the paste box. A posting runs to thousands of
    characters, which is not something to put on a command line."""
    body = env / "posting.txt"
    body.write_text("Galileo receiver work: integrity monitoring and RTK.")

    result = runner.invoke(app, [
        "add", "https://example.com/job/9", "-e", "Rohde & Schwarz",
        "--title", "Ingenieur GNSS", "--description-file", str(body),
    ])
    assert result.exit_code == 0, result.output

    store = Store(env / "jobs.db")
    job = store.list_jobs()[0]
    assert job.title == "Ingenieur GNSS"
    assert "integrity monitoring" in job.description
    store.close()


def test_add_stores_the_fetched_text_so_the_model_need_not_refetch(env, fake_http):
    runner.invoke(app, ["add", "https://example.com/job/10", "-e", "Rohde & Schwarz"])
    store = Store(env / "jobs.db")
    job = store.list_jobs()[0]
    assert "Galileo receiver work" in job.description
    assert "<p>" not in job.description
    store.close()
