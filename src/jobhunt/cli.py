from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from jobhunt.config import (
    load_cities, load_comp, load_deadlines, load_employers, load_scoring,
)
from jobhunt import llm as llm_module
from jobhunt.enrich import enrich_jobs
from jobhunt.linkedin import build_links, locations_from_cities
from jobhunt.links import apply_results, check_jobs
from jobhunt.models import Stage
from jobhunt.poll import SOURCE_REGISTRY, poll_all
from jobhunt.report import build_context, render
from jobhunt.rescore import rescore_all
from jobhunt.snapshot import default_client, fetch_text
from jobhunt.store import Store

app = typer.Typer(help="Track GNSS/PNT/radar job opportunities.")


def _today() -> date:
    """Indirection so tests can pin the date."""
    return date.today()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_client():
    """Indirection so tests can inject a fake client."""
    return default_client()


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _run_server(application, host: str, port: int) -> None:
    """Indirection so tests can assert the bind without starting a server."""
    import uvicorn

    uvicorn.run(application, host=host, port=port)


def _config_dir() -> Path:
    return Path(os.environ.get("JOBHUNT_CONFIG", "config"))


def _open_store() -> Store:
    store = Store(Path(os.environ.get("JOBHUNT_DB", "data/jobs.db")))
    store.initialize()
    return store


def _dashboard_path() -> Path:
    return Path(os.environ.get("JOBHUNT_DASHBOARD", "dashboard.html"))


def _load_all():
    cd = _config_dir()
    return (
        load_scoring(cd / "scoring.yaml"),
        load_comp(cd / "comp.yaml"),
        load_cities(cd / "cities.yaml"),
    )


@app.command("sync-employers")
def sync_employers() -> None:
    """Load config/employers.yaml into the database."""
    store = _open_store()
    employers = load_employers(_config_dir() / "employers.yaml")
    for employer in employers:
        store.upsert_employer(employer)
    typer.echo(f"synced {len(employers)} employers")
    store.close()


@app.command()
def add(
    url: str,
    employer: str = typer.Option(..., "--employer", "-e"),
    city: str = typer.Option("", "--city"),
    country: str = typer.Option("", "--country"),
    level: str = typer.Option("junior", "--level"),
    salary: Optional[float] = typer.Option(None, "--salary"),
    tags: str = typer.Option("", "--tags", help="comma-separated"),
    title: Optional[str] = typer.Option(None, "--title"),
) -> None:
    """Fetch a posting URL, snapshot it, score it, and store it."""
    from jobhunt.web.app import add_job_from_url
    from jobhunt.web.deps import Deps

    store = _open_store()
    deps = Deps(
        store_factory=_open_store, config_dir=_config_dir(),
        today=_today, http_client=_http_client,
    )
    if store.find_employer_by_name(employer) is None:
        typer.echo(f"created employer: {employer}")

    job_id = add_job_from_url(
        store, deps, url, employer, city, country, level, tags, title, salary
    )
    job = store.get_job(job_id)
    if job.role_fit == 0.0:
        typer.echo("warning: no role family matched")
    typer.echo(
        f"[{job_id}] {job.title} — score {job.total_score:.2f} "
        f"(fit {job.role_fit:.2f}, comp {job.comp_score:.2f}, qol {job.qol_score:.2f})"
    )
    store.close()


@app.command("list")
def list_jobs(
    stage: Optional[str] = typer.Option(None, "--stage"),
    all_jobs: bool = typer.Option(False, "--all"),
) -> None:
    """List tracked jobs, highest score first."""
    store = _open_store()
    stage_enum = _parse_stage(stage) if stage else None
    jobs = store.list_jobs(stage=stage_enum, include_dismissed=all_jobs)
    employers = {e.id: e.name for e in store.list_employers()}
    for job in jobs:
        flags = " ".join(f"[{f}]" for f in job.language_flags)
        typer.echo(
            f"{job.id:>4}  {job.total_score:.2f}  {job.stage.value:<11} "
            f"{job.title}  —  {employers.get(job.employer_id, '?')}  {flags}"
        )
    if not jobs:
        typer.echo("nothing to show")
    store.close()


@app.command()
def show(job_id: int) -> None:
    """Show a job's detail, scores, and event history."""
    store = _open_store()
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"no job with id {job_id}")
        raise typer.Exit(code=1)
    employer = store.get_employer(job.employer_id)

    typer.echo(f"{job.title}  [{job.stage.value}]")
    typer.echo(f"employer:  {employer.name if employer else '?'}")
    typer.echo(f"where:     {job.city} {job.country}")
    typer.echo(f"url:       {job.url}")
    typer.echo(f"snapshot:  {job.snapshot_path}")
    typer.echo(
        f"score:     {job.total_score:.2f} "
        f"(fit {job.role_fit:.2f}, comp {job.comp_score:.2f}, qol {job.qol_score:.2f})"
    )
    if job.language_flags:
        typer.echo(f"languages: {', '.join(job.language_flags)}")
    if job.dismissed:
        typer.echo(f"dismissed: {job.dismiss_reason}")
    typer.echo("events:")
    for event in store.list_events(job_id):
        typer.echo(f"  {event.ts[:10]}  {event.kind.value:<8} {event.text}")
    store.close()


def _parse_stage(value: str) -> Stage:
    try:
        return Stage(value)
    except ValueError:
        valid = ", ".join(s.value for s in Stage)
        typer.echo(f"invalid stage '{value}'. valid stages: {valid}")
        raise typer.Exit(code=2)


@app.command()
def stage(job_id: int, new_stage: str) -> None:
    """Move a job to a new pipeline stage."""
    store = _open_store()
    store.set_stage(job_id, _parse_stage(new_stage), ts=_now())
    typer.echo(f"[{job_id}] -> {new_stage}")
    store.close()


@app.command()
def note(job_id: int, text: str) -> None:
    """Replace a job's standing note."""
    store = _open_store()
    store.set_note(job_id, text, ts=_now())
    typer.echo(f"[{job_id}] noted")
    store.close()


@app.command()
def dismiss(job_id: int, reason: str = typer.Option(..., "--reason", "-r")) -> None:
    """Permanently hide a job. Your judgment overrides the score."""
    store = _open_store()
    store.dismiss_job(job_id, reason, ts=_now())
    typer.echo(f"[{job_id}] dismissed: {reason}")
    store.close()


@app.command()
def priority(job_id: int, value: int) -> None:
    """Set a job's priority, 1-5 (0 clears it)."""
    store = _open_store()
    try:
        store.set_priority(job_id, value, ts=_now())
    except ValueError as exc:
        typer.echo(str(exc))
        store.close()
        raise typer.Exit(code=2)
    typer.echo(f"[{job_id}] priority {value}")
    store.close()


@app.command()
def archive(job_id: int, reason: str = typer.Option(..., "--reason", "-r")) -> None:
    """Archive a job you judged uninteresting. Survives re-polling."""
    store = _open_store()
    store.archive_job(job_id, reason, ts=_now())
    typer.echo(f"[{job_id}] archived: {reason}")
    store.close()


@app.command()
def restore(job_id: int) -> None:
    """Bring an archived job back into the pipeline."""
    store = _open_store()
    store.restore_job(job_id, ts=_now())
    typer.echo(f"[{job_id}] restored")
    store.close()


@app.command()
def poll(
    employer: Optional[str] = typer.Option(None, "--employer", "-e"),
    max_pages: int = typer.Option(5, "--max-pages"),
) -> None:
    """Fetch new postings from every employer with polling enabled."""
    cfg, comp_cfg, cities = _load_all()
    store = _open_store()
    client = _http_client()
    try:
        reports = poll_all(
            store, SOURCE_REGISTRY, client, cfg, comp_cfg, cities,
            now=_now(), only=employer, max_pages=max_pages,
        )
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    if not reports:
        typer.echo(
            "no employers are set up for polling. Set ats and poll_enabled "
            "in config/employers.yaml, then run: jobs sync-employers"
        )
        store.close()
        return

    for report in reports:
        if report.error:
            typer.echo(f"{report.employer}: ERROR {report.error}")
            continue
        typer.echo(
            f"{report.employer} [{report.source}]: seen {report.seen}, "
            f"stored {report.stored} ({report.new} new), "
            f"skipped {report.skipped}"
        )
    total_new = sum(r.new for r in reports)
    typer.echo(f"\n{total_new} new job(s). See them with: jobs list")
    store.close()


@app.command()
def refresh() -> None:
    """Re-check every tracked job's URL and record whether it still resolves."""
    store = _open_store()
    jobs = store.list_jobs()
    client = _http_client()
    try:
        results = check_jobs(jobs, client, now=_now())
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    apply_results(store, results, now=_now())
    dead = [r for r in results if r.status == "dead"]
    typer.echo(f"checked {len(results)} jobs, {len(dead)} dead")
    for result in dead:
        typer.echo(f"  dead: {result.url}")
    store.close()


@app.command()
def reject(job_id: int) -> None:
    """Record that the employer rejected you."""
    store = _open_store()
    store.set_stage(job_id, Stage.REJECTED, ts=_now())
    typer.echo(f"[{job_id}] -> rejected")
    store.close()


@app.command()
def dismissed(review: bool = typer.Option(False, "--review")) -> None:
    """List dismissed jobs and their reasons, to inform negative keywords."""
    store = _open_store()
    jobs = [j for j in store.list_jobs(include_dismissed=True) if j.dismissed]
    for job in jobs:
        typer.echo(f"{job.id:>4}  {job.title}  —  {job.dismiss_reason}")
    if review and jobs:
        typer.echo(
            "\nRecurring reasons are candidates for config/scoring.yaml "
            "negative_keywords."
        )
    if not jobs:
        typer.echo("nothing dismissed")
    store.close()


@app.command()
def due() -> None:
    """Show follow-ups due and upcoming application cycles."""
    from jobhunt.deadlines import upcoming
    from jobhunt.staleness import due_jobs

    cfg, _, _ = _load_all()
    store = _open_store()
    today = _today()

    jobs = store.list_jobs()
    events = {j.id: store.list_events(j.id) for j in jobs if j.id is not None}
    items = due_jobs(jobs, events, cfg, today)

    typer.echo("follow-ups due:")
    for item in items or []:
        typer.echo(f"  [{item.job_id}] {item.title} — {item.days_since}d ({item.level})")
    if not items:
        typer.echo("  none")

    typer.echo("\nupcoming cycles:")
    cycles = upcoming(load_deadlines(_config_dir() / "deadlines.yaml"), today)
    for cycle in cycles:
        typer.echo(f"  {cycle.name} ({cycle.employer}) — {cycle.days_left}d, {cycle.state}")
    if not cycles:
        typer.echo("  none")
    store.close()


PROFILE_FILES = ("positioning.md", "evidence.md")


def _profile() -> str:
    """The candidate description sent to the model.

    Built from profile/, falling back to what config/scoring.yaml already
    states about role families and languages, so the AI commands work before
    the profile notes are written — just less precisely.
    """
    directory = Path(os.environ.get("JOBHUNT_PROFILE", "profile"))
    parts = [
        text for name in PROFILE_FILES
        if (path := directory / name).exists()
        and (text := path.read_text(encoding="utf-8").strip())
    ]
    if parts:
        return "\n\n".join(parts)

    cfg = load_scoring(_config_dir() / "scoring.yaml")
    families = ", ".join(f.name.replace("_", " ") for f in cfg.role_families)
    languages = ", ".join(cfg.known_languages)
    typer.echo(f"note: {directory}/ is empty, using a profile derived from "
               "scoring.yaml. Fill it in for sharper judgments.")
    return (f"Engineering graduate targeting: {families}. "
            f"Speaks: {languages}. Open to junior roles and funded PhDs.")


def _llm():
    """Build the model client, or explain exactly what is missing."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        typer.echo("ANTHROPIC_API_KEY is not set. Everything else works without "
                   "it; only the AI commands need a key.")
        raise typer.Exit(code=2)
    from jobhunt.llm import AnthropicLLM

    try:
        return AnthropicLLM(key, model=os.environ.get("JOBHUNT_MODEL",
                                                      llm_module.DEFAULT_MODEL))
    except ImportError:
        typer.echo("the anthropic package is not installed. "
                   "Run: .venv/bin/pip install anthropic")
        raise typer.Exit(code=2)


@app.command()
def enrich(
    job_id: Optional[int] = typer.Option(None, "--job", help="One job only"),
    stage: Optional[str] = typer.Option(None, "--stage"),
    limit: int = typer.Option(20, "--limit", help="Cap the API calls per run"),
    force: bool = typer.Option(False, "--force", help="Re-analyse already-read jobs"),
) -> None:
    """Read postings with the model: domain fit, seniority, language, angle."""
    store = _open_store()
    profile = _profile()
    llm = _llm()
    client = _http_client()
    try:
        if job_id is not None:
            job = store.get_job(job_id)
            jobs = [job] if job else []
        else:
            jobs = store.list_jobs(stage=_parse_stage(stage) if stage else None)
        report = enrich_jobs(store, jobs, profile, llm, fetch_text, _now(),
                             limit=limit, force=force, client=client)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
        store.close()

    typer.echo(f"analysed {report.analysed}, skipped {report.skipped} "
               "(already read; use --force to redo)")
    for failure in report.failed:
        typer.echo(f"  failed: {failure}")


@app.command("insights")
def insights(job_id: Optional[int] = typer.Argument(None)) -> None:
    """Show what the model concluded about a job, or about every read job."""
    import json as _json

    store = _open_store()
    jobs = [store.get_job(job_id)] if job_id else store.list_jobs()
    for job in jobs:
        if job is None or not job.llm_json:
            continue
        v = _json.loads(job.llm_json)
        typer.echo(f"[{job.id}] {job.title}")
        typer.echo(f"  ai fit    {v['domain_fit']:.2f}  (rule-based {job.role_fit:.2f})")
        typer.echo(f"  why       {v['reason']}")
        typer.echo(f"  level     {v['seniority']} / {v['contract']}")
        typer.echo(f"  german    {v['german_required']}")
        typer.echo(f"  angle     {v['angle']}")
    store.close()


@app.command("rank")
def rank_jobs() -> None:
    """Ask the model to order your shortlist and say why."""
    from jobhunt.llm import rank_prompt

    store = _open_store()
    jobs = store.list_jobs(stage=Stage.SHORTLISTED)
    if not jobs:
        typer.echo("nothing shortlisted yet")
        store.close()
        return
    profile = _profile()
    llm = _llm()
    store.close()
    typer.echo(llm.complete(rank_prompt(jobs, profile)))


@app.command()
def rescore() -> None:
    """Recompute every stored job's scores against the current config.

    Run this after editing scoring.yaml: polling only writes scores for jobs
    that still match, so a job that stops matching keeps a stale score.
    """
    cfg, comp_cfg, cities = _load_all()
    store = _open_store()
    try:
        report = rescore_all(store, cfg, comp_cfg, cities)
    finally:
        store.close()

    typer.echo(f"rescored {report.rescored} job(s)")
    if report.no_longer_matching:
        typer.echo(f"{len(report.no_longer_matching)} no longer match your role "
                   "families and are now scored 0:")
        for title in report.no_longer_matching[:10]:
            typer.echo(f"  - {title}")
        if len(report.no_longer_matching) > 10:
            typer.echo(f"  ... and {len(report.no_longer_matching) - 10} more")


@app.command()
def linkedin(
    city: str = typer.Option("", "--city", help="Only this city, e.g. Toulouse"),
    term: str = typer.Option("", "--term", help="Only this search term, e.g. gnss"),
) -> None:
    """Print LinkedIn search links to open by hand.

    LinkedIn cannot be polled — no public API, and scraping breaks its terms —
    so this builds the searches instead of running them.
    """
    cfg, _comp_cfg, cities = _load_all()
    links = build_links(cfg, locations_from_cities(cities))
    if city:
        links = [ln for ln in links if ln.location.lower().startswith(city.lower())]
    if term:
        links = [ln for ln in links if term.lower() in ln.term.lower()]

    for link in links:
        typer.echo(f"{link.label}\n  {link.url}")
    if not links:
        typer.echo("no searches match — check --city and --term against your config")


@app.command()
def report() -> None:
    """Regenerate the static HTML dashboard."""
    cfg, comp_cfg, cities = _load_all()
    store = _open_store()
    deadlines = load_deadlines(_config_dir() / "deadlines.yaml")
    context = build_context(store, deadlines, cfg, comp_cfg, cities, _today())
    path = _dashboard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(context), encoding="utf-8")
    typer.echo(f"wrote {path}")
    store.close()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run the local web UI."""
    if host not in LOOPBACK_HOSTS:
        typer.echo(
            f"refusing to bind {host}: this database holds your whole job search, "
            "so the server is loopback-only. Use 127.0.0.1."
        )
        raise typer.Exit(code=2)

    from jobhunt.web.app import create_app
    from jobhunt.web.deps import Deps

    typer.echo(f"serving on http://{host}:{port}  (ctrl-c to stop)")
    _run_server(create_app(Deps.from_env()), host, port)


if __name__ == "__main__":
    app()
