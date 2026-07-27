"""Language-model reading of a job posting.

The rule-based matcher decides relevance from the title, because that is all
most sources give us. This module reads the posting body instead and answers
the questions a keyword list cannot: is this really navigation work, is
"junior" actually junior, is German required or merely welcome.

Its output is advisory and is stored beside the deterministic scores, never
merged into them. Two matcher bugs in this project were caught only because
the scoring was inspectable; a model's confident wrong answer silently
outranking a real match would be the same failure with no way to see it.

Nothing here imports the Anthropic SDK at module level, so the rest of the
tool works unchanged with no API key and no SDK installed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from jobhunt.models import Job

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# Career pages carry navigation chrome, cookie banners and legal boilerplate.
# Past this much text the extra tokens buy noise, not signal.
MAX_POSTING_CHARS = 6000
MAX_TOKENS = 1024

_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class Verdict:
    relevant: bool = False
    domain_fit: float = 0.0
    reason: str = ""
    seniority: str = "unknown"
    contract: str = "unknown"
    german_required: str = "unknown"
    salary_stated: float | None = None
    angle: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


def parse_verdict(text: str) -> Verdict:
    """Read a verdict out of the model's reply.

    Models occasionally wrap JSON in prose or a code fence despite being told
    not to, so the first JSON object in the reply is used rather than
    requiring the whole reply to parse.
    """
    match = _JSON_RE.search(text or "")
    if not match:
        raise ValueError(f"no JSON object in model reply: {text[:120]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in model reply: {exc}") from exc

    fit = float(data.get("domain_fit", 0.0) or 0.0)
    salary = data.get("salary_stated")
    return Verdict(
        relevant=bool(data.get("relevant", False)),
        domain_fit=min(max(fit, 0.0), 1.0),
        reason=str(data.get("reason", "") or ""),
        seniority=str(data.get("seniority", "unknown") or "unknown"),
        contract=str(data.get("contract", "unknown") or "unknown"),
        german_required=str(data.get("german_required", "unknown") or "unknown"),
        salary_stated=float(salary) if salary not in (None, "") else None,
        angle=str(data.get("angle", "") or ""),
    )


def build_prompt(job: Job, posting_text: str, profile: str) -> str:
    """Prompt for one posting.

    The candidate section may be thin — the profile notes start as a template
    and get filled in over time. Domain and seniority must still be judged
    from the posting alone, because they do not depend on the candidate's
    history. A first live run got domain_fit 0.00 on a perfect match because
    the model treated a sparse profile as grounds to refuse everything.
    """
    body = (posting_text or "")[:MAX_POSTING_CHARS]
    return f"""You are screening job postings for one specific candidate.

CANDIDATE
{profile}

If the candidate section above is sparse, still judge relevance, domain_fit,
seniority, contract and language from the posting itself — those do not depend
on the candidate's history. Never refuse, and never ask for more information.
Only "angle" needs candidate detail; keep it short and generic when you lack it.

POSTING
Title: {job.title}
Employer location: {job.city} {job.country}

{body}

Judge this posting for this candidate. Be strict about domain: a posting that
mentions GNSS once while being a manufacturing, quality or sales role is not a
match. Be strict about seniority: "junior" in the title but five years of
required experience is not junior.

Reply with a single JSON object and nothing else:
{{"relevant": true/false,
  "domain_fit": 0.0-1.0,
  "reason": "one sentence, concrete",
  "seniority": "intern|junior|mid|senior|phd|unknown",
  "contract": "permanent|fixed-term|phd|apprenticeship|internship|unknown",
  "german_required": "required|helpful|not needed|unknown",
  "salary_stated": number or null,
  "angle": "one sentence on what this candidate should lead with"}}"""


def _digest_line(job: Job) -> str:
    """One line per job, carrying everything the advisor should weigh."""
    parts = [f"[{job.id}] {job.title}", f"{job.city} {job.country}".strip(),
             f"score {job.total_score:.2f}"]
    if job.priority:
        parts.append(f"your priority {job.priority}")
    if job.llm_fit is None:
        parts.append("not read by the model yet")
    else:
        parts.append(f"ai fit {job.llm_fit:.2f}")
        try:
            v = json.loads(job.llm_json or "{}")
        except json.JSONDecodeError:
            v = {}
        for key in ("seniority", "contract", "german_required"):
            if v.get(key) and v[key] != "unknown":
                parts.append(f"{key.replace('_', ' ')} {v[key]}")
        if v.get("reason"):
            parts.append(v["reason"])
    if job.notes:
        parts.append(f"your note: {job.notes}")
    return " | ".join(parts)


def advise_prompt(jobs: list[Job], profile: str, places: str = "") -> str:
    """Ask for a read across the whole set, not a score per job.

    Per-posting analysis already exists; what a person cannot do quickly is
    look at a hundred rows at once and say what the pattern is and where the
    effort should go.
    """
    listing = "\n".join(_digest_line(job) for job in jobs)
    location_note = f"\n\nPLACES\n{places}" if places else ""
    return f"""You are advising one candidate on where to spend their applications.

CANDIDATE
{profile}{location_note}

JOBS
{listing}

Give, in plain text:

1. The five to eight jobs worth applying to first, strongest first, each with
   one concrete sentence on why it beats the alternatives. Reference each by
   its [id].
2. The patterns you see across the whole set — which employers, cities,
   domains or role types keep producing good matches, and which keep
   producing near-misses and why.
3. Anything the candidate appears to be missing or over-weighting, including
   jobs ranked high by score that you would skip, and why.

Jobs marked "not read by the model yet" have not been analysed; say so rather
than implying a judgment. Be direct about weak options. Do not invent jobs or
details that are not listed above."""


def rank_prompt(jobs: list[Job], profile: str) -> str:
    lines = "\n".join(
        f"[{j.id}] {j.title} — {j.city} {j.country}"
        + (f" — {j.notes}" if j.notes else "")
        for j in jobs
    )
    return f"""You are advising one candidate on which jobs to apply to first.

CANDIDATE
{profile}

SHORTLIST
{lines}

Give the order to apply in, strongest first, with one sentence of reasoning
each. Say plainly if a job looks like a poor use of effort. Reply as plain
text, one line per job, starting with its [id]."""


def analyse(job: Job, posting_text: str, profile: str, llm) -> Verdict:
    """Ask the model to read one posting. Retries once on unparseable output."""
    prompt = build_prompt(job, posting_text, profile)
    try:
        return parse_verdict(llm.complete(prompt))
    except ValueError:
        retry = prompt + "\n\nYour previous reply was not valid JSON. Reply with the JSON object only."
        return parse_verdict(llm.complete(retry))


CLI_DEFAULT_MODEL = "haiku"


def resolve_backend(
    api_key: str = "",
    model: str = "",
    prefer: str = "",
    which=None,
    build_anthropic=None,
):
    """Pick a model backend, or return None if none is available.

    The Claude Code CLI comes first. It authenticates with the same login as
    the interactive tool, so usage lands on a Claude plan; an API key is a
    separate account with separate billing, which is a surprise nobody wants.
    Set JOBHUNT_LLM=api to force the API path.
    """
    if which is None:
        from shutil import which as which

    def make_api():
        if not api_key:
            return None
        builder = build_anthropic or (lambda k, m: AnthropicLLM(k, model=m))
        try:
            return builder(api_key, model or DEFAULT_MODEL)
        except ImportError:
            return None

    def make_cli():
        if not which("claude"):
            return None
        return ClaudeCodeLLM(model=model or CLI_DEFAULT_MODEL)

    order = (make_api, make_cli) if prefer == "api" else (make_cli, make_api)
    for factory in order:
        backend = factory()
        if backend is not None:
            return backend
    return None


class ClaudeCodeLLM:
    """Run the model through the local Claude Code CLI in headless mode.

    This is the backend to use when you have a Claude subscription but no API
    console billing: `claude -p` authenticates with the same login as the
    interactive tool, so the usage lands on the plan rather than on a separate
    API account.

    The prompt goes on stdin because postings run to thousands of characters
    and argv is the wrong place for that. Tools are denied outright: screening
    a job posting needs no filesystem or shell access, and a screening loop
    that can edit files is a bad idea however well it behaves.
    """

    DEFAULT_TIMEOUT = 180

    def __init__(
        self,
        model: str = "haiku",
        binary: str = "claude",
        timeout: int = DEFAULT_TIMEOUT,
        runner=None,
        cwd: str | None = None,
    ) -> None:
        self.model = model
        self.binary = binary
        self.timeout = timeout
        self.cwd = cwd
        self._runner = runner

    # Run headless in a scratch directory. Started inside the project, the CLI
    # loads the repo's own context: a live run had it inspecting git status and
    # the profile/ files instead of judging the posting in front of it.
    SYSTEM = (
        "You are a text-only classifier with no tools and no repository. "
        "Everything you need is in the user message. Never ask for more "
        "information and never mention files. Reply with the requested JSON "
        "object only."
    )

    def _scratch(self) -> str:
        if self.cwd is None:
            import tempfile

            self.cwd = tempfile.mkdtemp(prefix="jobhunt-llm-")
        return self.cwd

    def command(self) -> list[str]:
        return [
            self.binary, "-p",
            "--model", self.model,
            "--output-format", "text",
            "--disallowed-tools", "Bash Edit Write Read WebFetch WebSearch",
            "--append-system-prompt", self.SYSTEM,
        ]

    def complete(self, prompt: str) -> str:
        runner = self._runner
        if runner is None:
            import subprocess

            def runner(cmd, **kwargs):
                return subprocess.run(cmd, **kwargs)

        result = runner(
            self.command(), input=prompt, capture_output=True, text=True,
            timeout=self.timeout, cwd=self._scratch(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude exited {result.returncode}: "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        return result.stdout


class AnthropicLLM:
    """Thin adapter so the rest of the code never imports the SDK."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, prompt: str) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in message.content if hasattr(block, "text")
        )
