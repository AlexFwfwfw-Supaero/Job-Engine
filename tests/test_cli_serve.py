import pytest
from typer.testing import CliRunner

from jobhunt import cli as cli_module
from jobhunt.cli import app

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch):
    calls = {}

    def fake_run(application, host, port, **kwargs):
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr(cli_module, "_run_server", fake_run)
    return calls


def test_serve_binds_loopback_by_default(captured):
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000


def test_serve_accepts_a_custom_port(captured):
    runner.invoke(app, ["serve", "--port", "9001"])
    assert captured["port"] == 9001


def test_serve_refuses_a_non_loopback_host(captured):
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "loopback" in result.output.lower()
    assert captured == {}


def test_serve_prints_the_url(captured):
    result = runner.invoke(app, ["serve"])
    assert "http://127.0.0.1:8000" in result.output
