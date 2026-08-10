# Marking jobs found since the last search

## Problem

After a search run there is no way to tell, at a glance, which jobs are the
ones it just found. They sort in among everything else by score. The question
"what is new?" is the first one you ask after clicking Search now, and today it
takes reading `first_seen` in the database to answer.

## Behaviour

A job found by the most recent search run is marked as new. The mark clears
when the next search starts, whether or not that search finds anything.

- The row gets a translucent yellow background, readable under both light and
  dark colour schemes, and a small yellow `new` tag beside the title.
- The mark appears in every job list — Spotted, Interested, Applied, Archive —
  not only on the Search page. Triaging a job does not clear it; only the next
  search does.
- A job added by hand after the last search also counts as new. It was not
  there when the search ran, which is the same thing the mark is saying.
- "Update current jobs" (refresh) is not a search and clears nothing.
- Before the first ever search, nothing is marked.

## Design

**State.** A new `meta(key TEXT PRIMARY KEY, value TEXT)` table holds one row,
`last_search_at`. It goes in `SCHEMA` with `CREATE TABLE IF NOT EXISTS`, so
existing databases pick it up on the next `initialize()`; `_migrate` is not
touched, as that path only adds columns to `jobs`. `Store` gains `get_meta` and
`set_meta`.

Chosen over an `is_new` column on `jobs` because the fact is already derivable
from `first_seen`, and a bulk `UPDATE` per run is more moving parts than one
row that only the poller writes.

**Stamping.** `poll_all` already receives the `now` it stores as every new
job's `first_seen`. It writes `last_search_at = now` as its first act, so both
callers — `/actions/search` and `jobs poll` — get the behaviour without
changing either. `jobs poll --only <employer>` stamps too: any poll counts as
a search. That is a deliberate simplification, not an oversight.

**Reading.** `views._is_new(job, marker)` is true when `marker` is set and
`job.first_seen >= marker` — ISO-8601 strings compare correctly as text.
`_rows()` reads the marker once per render and sets `JobRow.is_new`. Every tab
builds its rows through `_rows()`, so one change covers all of them.

**Presentation.** A `tr.new` background tint in `base.html.j2` using a
translucent yellow, so it reads on either scheme, and a `new` badge reusing the
existing `.flag` class. The `identity()` macro in `_row.html.j2` renders the
badge; each template's `<tr>` carries the class.

## Testing

- `Store`: `get_meta` on a missing key returns `None`; `set_meta` then
  `get_meta` round-trips; `set_meta` twice on one key overwrites.
- `poll_all` stamps `last_search_at` with the `now` it was passed.
- `_is_new`: absent marker, `first_seen` before the marker, equal to it, after
  it, and a job with no `first_seen` at all.
- Rendering: a row for a new job carries the class and the badge; an older row
  carries neither.
