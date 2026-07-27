# Job search tracker

Tracks GNSS/PNT/radar engineering opportunities across Europe: a curated employer
watchlist, a pipeline with follow-up nudges, configurable scoring, and a local
web UI.

## Setup

    python3.13 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/jobs sync-employers

## Daily use

Start the UI:

    jobs serve            # http://127.0.0.1:8000

Tabs: **Overview** (what needs attention), **Job Search** (spotted jobs, add by
URL, re-check links), **Interested** (shortlisted, with priority and notes),
**Applied** (grouped by stage, with staleness), **Archive** (ruled out, with
reasons), **Deadlines** (recurring cycles).

The server binds loopback only and refuses anything else. This database holds your
whole job search; a typo should not put it on your network.

Everything the UI does has a CLI equivalent:

    jobs add <url> --employer "Septentrio" --city Leuven --country BE
    jobs list
    jobs stage 12 applied
    jobs priority 12 4
    jobs note 12 "team lead is ex-DLR"
    jobs refresh                     # re-check every posting URL
    jobs archive 14 --reason "surveying, not engineering"
    jobs restore 14
    jobs due

## Archiving

    jobs archive 14 --reason "surveying, not engineering"   # you rule it out
    jobs reject 12                                          # they reject you
    jobs restore 14                                         # bring it back
    jobs dismissed --review                                 # tune negative keywords

A dead link never auto-archives. `jobs refresh` flags it and leaves the decision
to you — a 404 can mean filled, moved, or a transient error.

## Configuration

Everything tunable lives in `config/`:

| File | What it controls |
|---|---|
| `scoring.yaml` | Weights, role families, negative keywords, staleness thresholds |
| `cities.yaml` | Sunshine hours, nature score, rent — quality-of-life inputs |
| `comp.yaml` | Salary tables, effective tax rates, price level indices |
| `employers.yaml` | The watchlist |
| `deadlines.yaml` | Recurring application cycles |

The seed values in `cities.yaml`, `comp.yaml`, and `deadlines.yaml` are estimates.
Correct them as real numbers emerge — they are inputs you own, not facts the tool
asserts. Entries in `deadlines.yaml` marked `VERIFY` have not been checked against
the source.

## Scoring

`total = w_comp · comp + w_qol · qol + w_fit · role_fit`, all weights in
`scoring.yaml`. Compensation prefers a stated salary, falls back to the country
table, and reports `no data` rather than guessing. Score sorts; it never rejects.

## Profile

`profile/` holds the content the tailoring work draws on — `evidence.md`,
`positioning.md`, `answers.md`, and base CVs. Nothing generates documents
unattended.

## AI reading (optional)

The rule-based score decides relevance from the title, which is all most job
boards give us. `jobs enrich` fetches the posting body instead and has a model
read it, answering what a keyword list cannot: whether it is really navigation
work, whether "junior" is actually junior, whether German is required or merely
welcome, and what to lead with.

    jobs enrich --stage shortlisted --limit 20
    jobs insights 43
    jobs rank

Two backends, tried in this order:

1. **The `claude` CLI**, if it is on PATH and logged in. Usage lands on your
   Claude subscription. This is the default because it needs no extra account.
2. **`ANTHROPIC_API_KEY`**, which bills a separate API console account.
   `pip install -e ".[ai]"` first. Force it with `JOBHUNT_LLM=api`.

With neither, every other feature works unchanged and the AI buttons do not
render.

The model's verdict is stored beside the deterministic score, never merged into
it. Disagreements are the point: a title matching `navigation payload` scored
0.90 on rules and 0.35 from the model, which had read far enough to see it was
a test-automation role. Trust neither blindly; read both.

Posting text is fetched once per job and cached. Workday pages render in
JavaScript, so their text comes from the CXS JSON endpoint behind them rather
than the HTML — see `src/jobhunt/posting_text.py`.

## Not yet built

Cornerstone (GMV, OHB) needs a session token its search API will not issue to a
plain fetch. DLR renders its SuccessFactors results in JavaScript and its only
no-JS feed is capped at 10 postings.
