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
    body = (posting_text or "")[:MAX_POSTING_CHARS]
    return f"""You are screening job postings for one specific candidate.

CANDIDATE
{profile}

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
