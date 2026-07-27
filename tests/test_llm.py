import json

import pytest

from jobhunt.llm import (
    MAX_POSTING_CHARS,
    Verdict,
    analyse,
    build_prompt,
    parse_verdict,
    rank_prompt,
)
from jobhunt.models import Job

PROFILE = "GNSS/aerospace graduate, Oct 2026, EN/FR/ES/PT, no German."


def job(title="GNSS Research Engineer", **kw):
    return Job(employer_id=1, title=title, url="https://x/1", city="Toulouse",
               country="FR", **kw)


def verdict_json(**overrides):
    data = {
        "relevant": True, "domain_fit": 0.8, "reason": "GNSS receiver work",
        "seniority": "junior", "contract": "permanent",
        "german_required": "not needed", "salary_stated": 42000,
        "angle": "Lead with your Galileo signal-processing thesis.",
    }
    data.update(overrides)
    return json.dumps(data)


def test_parse_verdict_reads_every_field():
    v = parse_verdict(verdict_json())
    assert v.relevant is True
    assert v.domain_fit == 0.8
    assert v.seniority == "junior"
    assert v.german_required == "not needed"
    assert v.salary_stated == 42000
    assert "Galileo" in v.angle


def test_parse_verdict_accepts_json_wrapped_in_prose():
    """Models sometimes prepend 'Here is the analysis:' despite instructions."""
    v = parse_verdict("Here is the analysis:\n```json\n" + verdict_json() + "\n```")
    assert v.domain_fit == 0.8


def test_parse_verdict_clamps_domain_fit_to_the_unit_interval():
    assert parse_verdict(verdict_json(domain_fit=4.2)).domain_fit == 1.0
    assert parse_verdict(verdict_json(domain_fit=-1)).domain_fit == 0.0


def test_parse_verdict_rejects_unparseable_output():
    with pytest.raises(ValueError):
        parse_verdict("I cannot help with that.")


def test_parse_verdict_tolerates_missing_optional_fields():
    v = parse_verdict(json.dumps({"relevant": False, "domain_fit": 0.0}))
    assert v.reason == ""
    assert v.salary_stated is None


def test_build_prompt_includes_the_posting_and_the_profile():
    prompt = build_prompt(job(), "We seek a GNSS engineer in Toulouse.", PROFILE)
    assert "GNSS Research Engineer" in prompt
    assert "We seek a GNSS engineer in Toulouse." in prompt
    assert "no German" in prompt


def test_build_prompt_truncates_a_very_long_posting():
    """Career pages carry navigation chrome and legal boilerplate; sending all
    of it costs tokens without adding signal."""
    prompt = build_prompt(job(), "x" * (MAX_POSTING_CHARS * 3), PROFILE)
    assert len(prompt) < MAX_POSTING_CHARS * 2


def test_build_prompt_demands_json_only():
    assert "JSON" in build_prompt(job(), "text", PROFILE)


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_analyse_returns_a_verdict():
    llm = FakeLLM([verdict_json()])
    v = analyse(job(), "posting text", PROFILE, llm)
    assert isinstance(v, Verdict)
    assert v.domain_fit == 0.8
    assert len(llm.prompts) == 1


def test_analyse_retries_once_on_unparseable_output():
    llm = FakeLLM(["not json at all", verdict_json()])
    v = analyse(job(), "posting text", PROFILE, llm)
    assert v.domain_fit == 0.8
    assert len(llm.prompts) == 2


def test_analyse_raises_when_the_retry_also_fails():
    llm = FakeLLM(["nope", "still nope"])
    with pytest.raises(ValueError):
        analyse(job(), "posting text", PROFILE, llm)


def test_rank_prompt_lists_every_job_with_its_identifier():
    jobs = [job("GNSS Engineer", id=3), job("Radar Engineer", id=9)]
    prompt = rank_prompt(jobs, PROFILE)
    assert "3" in prompt and "GNSS Engineer" in prompt
    assert "9" in prompt and "Radar Engineer" in prompt


def test_rank_prompt_asks_for_an_ordering_with_reasons():
    prompt = rank_prompt([job(id=1)], PROFILE)
    assert "order" in prompt.lower()
