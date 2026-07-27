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


# --- Claude Code CLI backend -------------------------------------------

def test_claude_code_command_uses_the_chosen_model_and_reads_stdin():
    """The prompt goes on stdin, not argv: postings are long and argv is not
    the place for 6000 characters of text."""
    from jobhunt.llm import ClaudeCodeLLM

    cmd = ClaudeCodeLLM(model="haiku").command()
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "haiku"
    assert "--output-format" in cmd
    assert not any("POSTING" in part for part in cmd)


def test_claude_code_denies_tools():
    """Screening a posting needs no filesystem or shell access."""
    from jobhunt.llm import ClaudeCodeLLM

    cmd = ClaudeCodeLLM().command()
    assert "--disallowed-tools" in cmd


def test_claude_code_complete_returns_stdout():
    from jobhunt.llm import ClaudeCodeLLM

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["input"] = kwargs.get("input")

        class Result:
            returncode = 0
            stdout = '```json\n{"relevant": true, "domain_fit": 0.7}\n```'
            stderr = ""

        return Result()

    llm = ClaudeCodeLLM(runner=fake_run)
    out = llm.complete("judge this posting")
    assert "domain_fit" in out
    assert calls["input"] == "judge this posting"


def test_claude_code_raises_a_readable_error_on_failure():
    from jobhunt.llm import ClaudeCodeLLM

    def failing_run(cmd, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "not logged in"

        return Result()

    llm = ClaudeCodeLLM(runner=failing_run)
    with pytest.raises(RuntimeError, match="not logged in"):
        llm.complete("anything")


def test_claude_code_output_parses_into_a_verdict():
    """End to end: fenced JSON from the CLI must survive parse_verdict."""
    from jobhunt.llm import ClaudeCodeLLM

    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
            stdout = "Here you go:\n```json\n" + verdict_json() + "\n```\n"
            stderr = ""

        return Result()

    v = analyse(job(), "text", PROFILE, ClaudeCodeLLM(runner=fake_run))
    assert v.domain_fit == 0.8
    assert v.seniority == "junior"


# --- backend resolution ------------------------------------------------

def test_resolve_prefers_the_claude_cli_over_an_api_key():
    """API console billing is a separate account from a Claude plan, so the
    subscription-backed path is the default when both are available."""
    from jobhunt.llm import ClaudeCodeLLM, resolve_backend

    backend = resolve_backend(api_key="sk-test", model="",
                              which=lambda _: "/usr/bin/claude")
    assert isinstance(backend, ClaudeCodeLLM)


def test_resolve_falls_back_to_the_api_key_when_no_cli_is_installed(monkeypatch):
    from jobhunt.llm import resolve_backend

    built = {}

    def fake_anthropic(key, model):
        built["key"] = key
        return "anthropic-client"

    backend = resolve_backend(api_key="sk-test", model="", which=lambda _: None,
                              build_anthropic=fake_anthropic)
    assert backend == "anthropic-client"
    assert built["key"] == "sk-test"


def test_resolve_returns_none_when_nothing_is_configured():
    from jobhunt.llm import resolve_backend

    assert resolve_backend(api_key="", model="", which=lambda _: None) is None


def test_resolve_honours_an_explicit_backend_choice():
    from jobhunt.llm import resolve_backend

    assert resolve_backend(api_key="sk-test", model="", prefer="api",
                           which=lambda _: "/usr/bin/claude",
                           build_anthropic=lambda k, m: "anthropic") == "anthropic"


def test_resolve_uses_the_cli_default_model_when_none_is_given():
    from jobhunt.llm import resolve_backend

    backend = resolve_backend(api_key="", model="", which=lambda _: "/bin/claude")
    assert backend.model == "haiku"


def test_claude_code_runs_outside_the_project_directory():
    """Started in the repo, the CLI loads the project's own context: a live run
    had it inspecting git status instead of judging the posting."""
    from jobhunt.llm import ClaudeCodeLLM

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs.get("cwd")

        class Result:
            returncode = 0
            stdout = '{"relevant": true, "domain_fit": 0.5}'
            stderr = ""

        return Result()

    ClaudeCodeLLM(runner=fake_run).complete("judge this")
    assert seen["cwd"]
    assert "job-searching" not in seen["cwd"]


def test_claude_code_tells_the_model_it_has_no_tools():
    from jobhunt.llm import ClaudeCodeLLM

    cmd = ClaudeCodeLLM().command()
    system = cmd[cmd.index("--append-system-prompt") + 1]
    assert "no tools" in system
    assert "JSON" in system


def test_prompt_tells_the_model_not_to_refuse_on_a_sparse_profile():
    """Live regression: a thin profile made the model return domain_fit 0.00
    for a posting the rule-based matcher scored 1.00."""
    prompt = build_prompt(job(), "posting body", "Target domains: gnss.")
    assert "Never refuse" in prompt
    assert "do not depend" in prompt


# --- advising across the whole set -------------------------------------

def advisable(**kw):
    from jobhunt.models import Job
    base = dict(employer_id=1, title="GNSS Engineer", url="https://x/1",
                city="Toulouse", country="FR", total_score=0.6)
    base.update(kw)
    return Job(**base)


def test_advise_prompt_includes_the_scores_and_the_ai_reading():
    from jobhunt.llm import advise_prompt

    jobs = [advisable(id=3, title="PhD Navigation Payloads", total_score=0.85,
                      llm_fit=0.95,
                      llm_json='{"reason": "exact domain match", '
                               '"seniority": "phd", "contract": "phd", '
                               '"german_required": "helpful"}')]
    prompt = advise_prompt(jobs, "Target domains: gnss.", "")
    assert "PhD Navigation Payloads" in prompt
    assert "0.85" in prompt and "0.95" in prompt
    assert "exact domain match" in prompt
    assert "helpful" in prompt


def test_advise_prompt_carries_your_own_notes_and_priority():
    """Your notes are the strongest preference signal in the database."""
    from jobhunt.llm import advise_prompt

    jobs = [advisable(id=7, notes="met the team at a conference", priority=5)]
    prompt = advise_prompt(jobs, "profile", "")
    assert "met the team at a conference" in prompt
    assert "5" in prompt


def test_advise_prompt_includes_city_preferences_when_given():
    from jobhunt.llm import advise_prompt

    prompt = advise_prompt([advisable(id=1)], "profile",
                           "Toulouse: sunny, cheap rent")
    assert "Toulouse: sunny, cheap rent" in prompt


def test_advise_prompt_asks_for_themes_not_just_a_list():
    from jobhunt.llm import advise_prompt

    prompt = advise_prompt([advisable(id=1)], "profile", "")
    assert "pattern" in prompt.lower() or "theme" in prompt.lower()


def test_advise_prompt_marks_jobs_the_model_has_not_read():
    """Unread jobs must be visibly unread, or the advice implies a judgment
    that was never made."""
    from jobhunt.llm import advise_prompt

    prompt = advise_prompt([advisable(id=1, llm_fit=None)], "profile", "")
    assert "not read" in prompt.lower()
