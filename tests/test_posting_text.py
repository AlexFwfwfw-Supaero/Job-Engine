import json

import pytest

from jobhunt.models import Job
from jobhunt.posting_text import cxs_detail_url, fetch_posting_text, strip_html


def workday_job():
    return Job(
        employer_id=1, source="workday", title="Navigation Payload AIV Engineer",
        url="https://thales.wd3.myworkdayjobs.com/en-US/Careers/job/Roma/"
            "Navigation-Payload-AIV-Engineer_R0307056",
    )


def test_cxs_detail_url_is_derived_from_the_browsable_url():
    """Workday job pages render in JavaScript, so the HTML has no description.
    The CXS endpoint behind them returns it as JSON."""
    assert cxs_detail_url(workday_job().url) == (
        "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/job/Roma/"
        "Navigation-Payload-AIV-Engineer_R0307056"
    )


def test_cxs_detail_url_handles_a_locale_free_path():
    url = "https://ag.wd3.myworkdayjobs.com/Airbus/job/Getafe/Antenna_JR1"
    assert cxs_detail_url(url) == (
        "https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/job/Getafe/Antenna_JR1"
    )


def test_cxs_detail_url_returns_none_for_a_non_workday_url():
    assert cxs_detail_url("https://boards.greenhouse.io/x/jobs/1") is None


def test_strip_html_keeps_the_words_and_drops_the_markup():
    html = "<p>We seek a <b>GNSS</b> engineer.</p><ul><li>Galileo</li></ul>"
    text = strip_html(html)
    assert "GNSS" in text and "Galileo" in text
    assert "<" not in text


def test_strip_html_decodes_entities():
    assert "R&D" in strip_html("<p>R&amp;D role</p>")


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.response


def test_fetch_posting_text_uses_the_cxs_endpoint_for_workday():
    payload = {"jobPostingInfo": {
        "jobDescription": "<p>Work on <b>Galileo</b> navigation payloads, "
                          "integration and verification in Rome.</p>"
    }}
    client = FakeClient(FakeResponse(payload=payload))
    text = fetch_posting_text(workday_job(), client)
    assert "Galileo" in text
    assert "<p>" not in text
    assert client.urls[0].startswith(
        "https://thales.wd3.myworkdayjobs.com/wday/cxs/"
    )


def test_fetch_posting_text_falls_back_to_html_for_other_sources():
    job = Job(employer_id=1, title="Radar Engineer", source="greenhouse",
              url="https://job-boards.eu.greenhouse.io/x/jobs/1")
    called = {}

    def fake_fetch(url, client):
        called["url"] = url
        return "<p>Radar engineer wanted for waveform design work in Munich.</p>"

    text = fetch_posting_text(job, FakeClient(FakeResponse()),
                              html_fetcher=fake_fetch)
    assert "Radar engineer wanted" in text
    assert called["url"] == job.url


def test_fetch_posting_text_raises_when_workday_returns_no_description():
    """An empty description must fail loudly, not silently feed the model
    nothing and collect a confident verdict about a blank page."""
    client = FakeClient(FakeResponse(payload={"jobPostingInfo": {}}))
    with pytest.raises(ValueError, match="no description"):
        fetch_posting_text(workday_job(), client)


def test_fetch_posting_text_raises_when_the_page_yields_almost_nothing():
    def empty_fetch(url, client):
        return "   "

    job = Job(employer_id=1, title="X", source="manual", url="https://x/1")
    with pytest.raises(ValueError, match="no description"):
        fetch_posting_text(job, FakeClient(FakeResponse()),
                           html_fetcher=empty_fetch)


def test_fetch_posting_text_serialises_a_dict_description_safely():
    payload = {"jobPostingInfo": {"jobDescription": {"unexpected": "shape"}}}
    client = FakeClient(FakeResponse(payload=payload))
    with pytest.raises(ValueError):
        fetch_posting_text(workday_job(), client)
    assert json.dumps(payload)  # payload itself stays inspectable
