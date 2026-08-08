from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.config import load_cities, load_comp, load_scoring
from jobhunt.enrich import enrich_jobs
from jobhunt.links import apply_results, check_jobs
from jobhunt.match import evaluate
from jobhunt.models import Employer, Job, Stage
from jobhunt.poll import SOURCE_REGISTRY, poll_all
from jobhunt.rescore import rescore_all
from jobhunt.score import compensation, quality_of_life, total_score
from jobhunt.posting_text import fetch_posting_text, strip_html
from jobhunt.snapshot import fetch_text, save_snapshot
from jobhunt.sources.manual import posting_from_url
from jobhunt.store import Store
from jobhunt.web.deps import Deps
from jobhunt.web.progress import ADVICE, POLL, PROGRESS
from jobhunt.web.views import (
    DEFAULT_SPOTTED_SORT, TABS, advice_context, applied_context,
    archive_context, deadlines_context, interested_context, overview_context,
    render_tab, search_context,
)

CONTEXT_BUILDERS = {
    "overview": overview_context,
    "search": search_context,
    "interested": interested_context,
    "applied": applied_context,
    "archive": archive_context,
    "deadlines": deadlines_context,
    "advice": advice_context,
}

KNOWN_TABS = {slug for slug, _, _ in TABS}
SEE_OTHER = 303


def _write_briefing(deps: Deps, llm, scope: str) -> None:
    """Ask for the briefing off the request thread, with its own store."""
    from jobhunt.advise import build_briefing

    store = deps.store_factory()
    try:
        jobs = store.list_jobs(
            stage=Stage.SHORTLISTED if scope == "shortlisted" else None
        )
        build_briefing(store, jobs, deps.profile(), _places(deps), llm,
                       _now(), scope=scope)
    finally:
        ADVICE.finish(1)
        store.close()


def _places(deps: Deps) -> str:
    """The cities you score, described for the advisor."""
    cities = load_cities(deps.config_dir / "cities.yaml")
    return "\n".join(
        f"{c.name}, {c.country}: {c.sunshine_hours:.0f} sunshine hours, "
        f"nature {c.nature:.0f}/10, rent index {c.rent_index:.0f}"
        for c in cities.values()
    )


def _read_all(deps: Deps, cfg, llm, jobs, force: bool = False) -> None:
    """Have the model read the given jobs. Runs off the request thread, so
    it opens its own store: SQLite connections belong to one thread."""
    store = deps.store_factory()
    client = deps.http_client()
    try:
        report = enrich_jobs(store, jobs, deps.profile(), llm,
                             fetch_posting_text, _now(), client=client,
                             weights=cfg.weights, on_done=PROGRESS.step,
                             force=force)
        PROGRESS.finish(report.analysed, report.failed)
    except Exception:
        PROGRESS.finish(PROGRESS.done, PROGRESS.failures)
        raise
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
        store.close()


def _poll_all(deps: Deps, cfg, comp_cfg, cities) -> None:
    """Sweep every polled employer off the request thread.

    Its own store and client, for the same reason as _read_all: a SQLite
    connection belongs to the thread that opened it.
    """
    store = deps.store_factory()
    client = deps.http_client()
    try:
        poll_all(store, SOURCE_REGISTRY, client, cfg, comp_cfg, cities,
                 now=_now(), on_start=POLL.starting, on_done=POLL.completed)
    finally:
        POLL.finish()
        close = getattr(client, "close", None)
        if close:
            close()
        store.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_return(return_to: str) -> str:
    """Only ever redirect to a known tab.

    return_to arrives from a form field, so treating it as a URL would be an
    open redirect. Anything unrecognised falls back to the overview.
    """
    return f"/{return_to}" if return_to in KNOWN_TABS else "/overview"


def add_job_from_url(
    store: Store,
    deps: Deps,
    url: str,
    employer: str,
    city: str = "",
    country: str = "",
    level: str = "junior",
    tags: str = "",
    title: str | None = None,
    salary: float | None = None,
    description: str = "",
) -> int:
    """Fetch, snapshot, score and store a posting. Shared by the web UI and CLI.

    Manual entry exists for the postings the pollers cannot reach — LinkedIn,
    PDF adverts, a professor's page, a JavaScript careers site. Those are
    exactly the URLs a fetch either fails on or returns chrome for, so the
    title and the body can be given directly:

    * a pasted description is what gets scored and what the model later reads,
      instead of re-fetching a page that did not work the first time;
    * a fetch that fails is fatal only when nothing was pasted. Losing a
      hand-entered posting because its site refused a robot would defeat the
      purpose of having manual entry at all.
    """
    cfg = load_scoring(deps.config_dir / "scoring.yaml")
    comp_cfg = load_comp(deps.config_dir / "comp.yaml")
    cities = load_cities(deps.config_dir / "cities.yaml")

    known = store.find_employer_by_name(employer)
    if known is None:
        employer_id = store.upsert_employer(
            Employer(name=employer, country=country, city=city)
        )
    else:
        employer_id = known.id
        country = country or known.country
        city = city or known.city

    pasted = (description or "").strip()
    client = deps.http_client()
    try:
        text = fetch_text(url, client)
    except Exception:
        # Only survivable because the operator supplied the posting already.
        if not pasted:
            raise
        text = ""
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    posting = posting_from_url(url, text, title=title or None)
    postings_dir = Path(os.environ.get("JOBHUNT_POSTINGS", "data/postings"))
    snapshot_path = save_snapshot(postings_dir, url, text) if text else ""

    # What the model will read later. Pasted text wins: the page behind a
    # hand-entered URL is usually chrome or a login wall. Fetched markup is
    # stripped, because the description column is a text budget, not markup.
    body = pasted or strip_html(text)

    match = evaluate(posting.title, body, country, cfg)
    city_entry = cities.get(city.lower()) if city else None
    breakdown = compensation(country, level, salary, comp_cfg, city_entry, cfg)
    qol = quality_of_life(city_entry, cfg)
    total = total_score(breakdown.normalised, qol, match.role_fit, cfg.weights)

    now = _now()
    return store.upsert_job(Job(
        employer_id=employer_id, title=posting.title, url=url, city=city,
        country=country, source="manual", snapshot_path=str(snapshot_path),
        first_seen=now, last_seen=now, role_fit=match.role_fit,
        comp_score=breakdown.normalised, qol_score=qol, total_score=total,
        salary_stated=salary, level=level, language_flags=match.language_flags,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        description=body,
    ))


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="jobhunt")

    @app.get("/", response_class=RedirectResponse)
    def root() -> RedirectResponse:
        return RedirectResponse("/overview", status_code=SEE_OTHER)

    @app.get("/api/progress")
    def progress() -> dict:
        """What the two long actions are doing, for the page's poller.

        Two path segments, so the /{slug} tab route cannot shadow it. Each
        record ships its own rendered sentence: the wording belongs in Python,
        not duplicated into JavaScript.
        """
        return {"poll": POLL.as_dict(), "ai": PROGRESS.as_dict()}

    @app.get("/{slug}", response_class=HTMLResponse)
    def tab(slug: str, sort: str = DEFAULT_SPOTTED_SORT) -> HTMLResponse:
        builder = CONTEXT_BUILDERS.get(slug)
        if builder is None:
            raise HTTPException(status_code=404, detail=f"no such tab: {slug}")
        # Only the search tab takes an option today. Naming it here beats
        # threading a parameter blob through every context builder.
        options = {"sort": sort} if slug == "search" else {}
        store = deps.store_factory()
        try:
            return HTMLResponse(render_tab(slug, builder(store, deps, **options)))
        finally:
            store.close()

    @app.post("/jobs")
    def create_job(
        url: str = Form(...),
        employer: str = Form(...),
        city: str = Form(""),
        country: str = Form(""),
        level: str = Form("junior"),
        tags: str = Form(""),
        title: str = Form(""),
        description: str = Form(""),
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            add_job_from_url(store, deps, url, employer, city, country, level,
                             tags, title=title, description=description)
        finally:
            store.close()
        return RedirectResponse("/search", status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/stage")
    def set_stage(
        job_id: int, stage: str = Form(...), return_to: str = Form("overview")
    ) -> RedirectResponse:
        try:
            parsed = Stage(stage)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown stage: {stage}")
        store = deps.store_factory()
        try:
            store.set_stage(job_id, parsed, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/note")
    def set_note(
        job_id: int, text: str = Form(""), return_to: str = Form("overview")
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.set_note(job_id, text, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/priority")
    def set_priority(
        job_id: int, value: int = Form(...), return_to: str = Form("overview")
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.set_priority(job_id, value, ts=_now())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/archive")
    def archive(
        job_id: int, reason: str = Form(""), return_to: str = Form("overview")
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.archive_job(job_id, reason, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/restore")
    def restore(job_id: int, return_to: str = Form("overview")) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.restore_job(job_id, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/analyse")
    def analyse_job(
        job_id: int, return_to: str = Form("search")
    ) -> RedirectResponse:
        """Have the model read one posting. Unavailable without a key, and
        that must be a plain message rather than a stack trace."""
        llm = deps.llm()
        if llm is None:
            raise HTTPException(
                status_code=503,
                detail="No model available. Install and log in to the claude "
                       "CLI (uses your Claude plan) or set ANTHROPIC_API_KEY. "
                       "Everything else in the tracker works without either.",
            )
        # Without the weights, save_enrichment leaves rank_score alone: a
        # re-read updated the model's fit but not the number the lists sort
        # by, so one row drifted out of order with the rest.
        cfg = load_scoring(deps.config_dir / "scoring.yaml")
        store = deps.store_factory()
        client = deps.http_client()
        try:
            job = store.get_job(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"no job {job_id}")
            enrich_jobs(store, [job], deps.profile(), llm, fetch_posting_text, _now(),
                        force=True, client=client, weights=cfg.weights)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/actions/rescore")
    def rescore(
        return_to: str = Form("search"), force: str = Form(""),
    ) -> RedirectResponse:
        """Re-apply the rules, then have the model read.

        Two buttons post here. "Read new" reads only the postings that have
        never been read — the cheap, everyday pass. "Reread all" sets force and
        reads everything again, which is what you want after changing the
        screening prompt or the profile: an old verdict was reached under
        different instructions and will not update on its own.

        The rule pass is fast and runs inline. The model pass is not — twenty
        to fifty seconds per posting, four at a time — so it runs in the
        background and the page reports progress rather than the browser
        waiting out a run that can take half an hour.
        """
        cfg = load_scoring(deps.config_dir / "scoring.yaml")
        comp_cfg = load_comp(deps.config_dir / "comp.yaml")
        cities = load_cities(deps.config_dir / "cities.yaml")
        redo = force == "1"
        store = deps.store_factory()
        try:
            rescore_all(store, cfg, comp_cfg, cities)
            jobs = store.list_jobs()
            # Archived jobs are excluded either way: rereading a posting you
            # already ruled out spends a call to change nothing.
            pending = jobs if redo else [j for j in jobs if j.llm_checked is None]
        finally:
            store.close()

        llm = deps.llm()
        if llm is not None and pending and not PROGRESS.running:
            PROGRESS.start(len(pending))
            deps.background(lambda: _read_all(deps, cfg, llm, pending, force=redo))
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/actions/advise")
    def advise(scope: str = Form("all")) -> RedirectResponse:
        """Write the whole-set briefing. One call, but a long one, so it runs
        in the background and the tab shows the previous briefing meanwhile."""
        llm = deps.llm()
        if llm is None:
            raise HTTPException(
                status_code=503,
                detail="No model available. Install and log in to the claude "
                       "CLI (uses your Claude plan) or set ANTHROPIC_API_KEY.",
            )
        store = deps.store_factory()
        try:
            count = len(store.list_jobs(
                stage=Stage.SHORTLISTED if scope == "shortlisted" else None
            ))
        finally:
            store.close()

        if count and not ADVICE.running:
            ADVICE.start(count)
            deps.background(lambda: _write_briefing(deps, llm, scope))
        return RedirectResponse("/advice", status_code=SEE_OTHER)

    @app.post("/actions/refresh")
    def refresh(return_to: str = Form("search")) -> RedirectResponse:
        store = deps.store_factory()
        try:
            jobs = store.list_jobs()
            client = deps.http_client()
            try:
                results = check_jobs(jobs, client, now=_now())
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()
            apply_results(store, results, now=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/actions/search")
    def search(return_to: str = Form("search")) -> RedirectResponse:
        """Sweep the polled employers in the background.

        A sweep is minutes of network, so the browser is sent straight back to
        the page and the progress line reports which employer is being fetched.
        A second click while one is running is refused rather than hammering
        every board twice.
        """
        cfg = load_scoring(deps.config_dir / "scoring.yaml")
        comp_cfg = load_comp(deps.config_dir / "comp.yaml")
        cities = load_cities(deps.config_dir / "cities.yaml")

        store = deps.store_factory()
        try:
            pollable = len([e for e in store.list_employers() if e.poll_enabled])
        finally:
            store.close()

        if pollable and not POLL.running:
            POLL.start(pollable)
            deps.background(lambda: _poll_all(deps, cfg, comp_cfg, cities))
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    return app
