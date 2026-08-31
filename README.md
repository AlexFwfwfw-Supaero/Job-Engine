# Job search tracker

Tracks GNSS/PNT/radar engineering opportunities across Europe: a curated employer
watchlist, a pipeline with follow-up nudges, configurable scoring, and a local
web UI.

## Setup

    python3.13 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/jobs sync-employers


    .venv/bin/jobs serve
## Daily use

Start the UI:

    jobs serve            # http://127.0.0.1:8000

Tabs: **Overview** (what needs attention), **Job Search** (spotted jobs, add by
URL, re-check links), **Interested** (shortlisted, with priority and notes),
**Applied** (grouped by stage, with staleness), **Archive** (ruled out, with
reasons), **Deadlines** (recurring cycles).

The **Spotted** table sorts three ways. *Rank* is the default: the model's fit
folded into your compensation and quality-of-life weights, which answers "where
should I apply". *AI fit* is the model's reading of the posting body on its own,
with unread jobs last — use it when a good reading is being buried by a weak
salary band. *Score* is the rule-based number from before the model read
anything.

**Search now**, **Read new** and **Reread all** run in the background and report
what they are doing under the buttons — which employer is being polled and what
it has found, or how many postings the model has read of the total, with the
reason beside anything that failed. A search that finishes reloads the page so
the new rows appear. Each refuses a second start while one is already going.

**Read new** reads only postings the model has never seen. **Reread all** reads
every non-archived posting again, which is what you want after changing the
screening prompt or your profile: an old verdict was reached under different
instructions and will not update on its own. It asks first, because it spends
one model call per job.

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

`negative_keywords` in `scoring.yaml` rejects a posting outright on its title:
technician, operator and administrative roles never reach the pipeline. They
match from the start of a word, so `formation` does not reject "Geo
Information". Management and quality titles are deliberately absent — in French
"Responsable technique" is a technical lead, and rejecting it by keyword would
have hidden a shortlisted job. The screening prompt scores those down instead,
which leaves them visible and arguable.

Tightening the list does not reach backwards. The Job Search tab lists the
spotted jobs the rules would no longer store and offers to archive them, because
ruling a job out stays your decision rather than something a config edit does
behind your back.

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

    jobs enrich --stage shortlisted --limit 20   # 20-50s per posting, 4 at a time
    jobs insights 43                            # what it concluded about one job
    jobs advise                                 # read across the whole set

Two backends, tried in this order:

1. **The `claude` CLI**, if it is on PATH and logged in. Usage lands on your
   Claude subscription. This is the default because it needs no extra account.
2. **`ANTHROPIC_API_KEY`**, which bills a separate API console account.
   `pip install -e ".[ai]"` first. Force it with `JOBHUNT_LLM=api`.

With neither, every other feature works unchanged and the AI buttons do not
render.

Lists sort by `rank_score`: the same weighted formula, with the model's fit
substituted for the keyword fit once it has read the posting. Jobs it has not
read keep ranking on the keyword fit and are marked unread. Sorting on the raw
AI fit would have silently dropped the compensation and quality-of-life weights.

The model's verdict is stored beside the deterministic score, never merged into
it. Disagreements are the point: a title matching `navigation payload` scored
0.90 on rules and 0.35 from the model, which had read far enough to see it was
a test-automation role. Trust neither blindly; read both.

Posting text is fetched once per job and cached. Workday pages render in
JavaScript, so their text comes from the CXS JSON endpoint behind them rather
than the HTML — see `src/jobhunt/posting_text.py`.

## Sitemap boards

Some employers have no API worth reaching but do publish every open advert in
`sitemap.xml`, the file they hand search engines. The `sitemap` source reads
that: the title comes from the URL slug, and everything else waits for
enrichment to fetch the page. It reaches CNES, Telespazio France and Sirius
today, plus PLD Space, and would reach any employer that does the same.

A sitemap lists the whole site, so `ats_endpoint` carries the job path as a URL
fragment — `https://careers.telespazio.fr/sitemap.xml#/jobs/`. A fragment is
never sent to the server, so the whole configuration for a board fits on one
line. Leave it out and the poll fails with that message rather than storing the
privacy policy as a job.

## Not yet built

Cornerstone (GMV, OHB) needs a session token its search API will not issue to a
plain fetch. DLR renders its SuccessFactors results in JavaScript and its only
no-JS feed is capped at 10 postings.

## Certificates

A few employers serve their leaf certificate without the intermediate that
signs it. The chain is real and its root is public; the server just fails to
send the middle of it, so there is no path to follow and the connection is
refused. Browsers fetch the missing certificate themselves and say nothing.

`src/jobhunt/certs/` holds those intermediates, and `snapshot.ssl_context()`
loads them alongside certifi. Verification stays fully on — expired,
self-signed and wrong-host certificates are still rejected — and nothing gains
trust it did not already have. The alternative, `verify=False`, would have
turned off hostname and expiry checking for every host to work around one
employer's misconfiguration. Each file's header records where it came from and
how to check it.
