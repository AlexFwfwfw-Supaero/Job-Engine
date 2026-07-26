# Job search tracker

Tracks GNSS/PNT/radar engineering opportunities across Europe: a curated employer
watchlist, a pipeline with follow-up nudges, configurable scoring, and a static
dashboard.

## Setup

    python3.13 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/jobs sync-employers

## Daily use

    jobs add <url> --employer "Septentrio" --city Leuven --country BE
    jobs list
    jobs stage 12 applied
    jobs note 12 "recruiter call booked for Tuesday"
    jobs due
    jobs report && xdg-open dashboard.html

## Dismissing

    jobs dismiss 14 --reason "surveying, not engineering"   # you reject the job
    jobs reject 12                                          # they reject you
    jobs dismissed --review                                 # tune negative keywords

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

## Not yet built

ATS polling (`jobs poll`) and the per-platform source modules. The `Source`
protocol in `src/jobhunt/sources/base.py` is the interface they implement.
