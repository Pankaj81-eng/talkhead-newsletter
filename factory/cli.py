from __future__ import annotations

import json
from pathlib import Path
import typer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from . import config, db
from .graph import build_graph, build_custom_graph, new_run_id, run_dir_for
from .agents.script_agent import build_script_from_lines, format_raw_script, beats_to_script_text

app = typer.Typer(add_completion=False)


def _print_script(script: dict) -> None:
    typer.secho("\n" + "=" * 60, fg="cyan")
    typer.secho(f"TITLE: {script['title']}", bold=True)
    typer.echo(f"DESC:  {script['description']}")
    typer.echo(f"TAGS:  {', '.join(script['hashtags'])}")
    typer.secho("\n--- HOOK ---", fg="yellow")
    typer.echo(f"[{script['hook']['visual']}: {script['hook']['query']}]")
    typer.echo(script["hook"]["text"])
    typer.secho("\n--- BODY ---", fg="yellow")
    for i, beat in enumerate(script["body"], 1):
        typer.echo(f"\n{i}. [{beat['visual']}: {beat['query']}]")
        typer.echo(f"   {beat['text']}")
    words = len(" ".join(
        [script["hook"]["text"]] + [b["text"] for b in script["body"]]
    ).split())
    est_seconds = words / config.WORDS_PER_SECOND
    typer.secho(
        f"\n~{words} words, ~{est_seconds:.0f}s narration "
        f"(+ outro) -- budget is {config.MIN_SECONDS:.0f}-{config.SAFE_MAX_SECONDS:.0f}s",
        fg="cyan",
    )
    typer.secho("=" * 60 + "\n", fg="cyan")


def _ask(prompt: str) -> str:
    while True:
        ans = typer.prompt(prompt).strip().lower()
        if ans in ("approve", "a", "y", "yes"):
            return "approve"
        if ans in ("reject", "r", "n", "no"):
            return "reject"
        typer.echo("Type 'approve' or 'reject'.")


def _ask_voice_choice(candidates: list[dict]) -> str:
    typer.secho("\n" + "=" * 60, fg="cyan")
    typer.secho("Multiple voice takes generated -- listen to each, then pick one.",
                bold=True)
    for i, c in enumerate(candidates, 1):
        typer.echo(f"  {i}. {c['path']}  (seed={c['seed']})")
    typer.echo(
        "\nOnce you've picked a favorite, set FACTORY_VOICE_SEED to its "
        "seed in .env to reuse that exact take on future runs instead of "
        "generating candidates again."
    )
    typer.secho("=" * 60, fg="cyan")
    while True:
        ans = typer.prompt(f"Which take? [1-{len(candidates)}]").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(candidates):
            return ans
        typer.echo(f"Enter a number between 1 and {len(candidates)}.")


def _run_pipeline_gates(graph, cfg, result) -> dict:
    """
    Shared by build/build-script: walk however many human gates the graph
    actually raises (always script + video; optionally a voice-take pick
    in between, only when node_voice_candidates ran -- see graph.py's
    _batch_voice_active()) until the pipeline completes.
    """
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        gate = payload["gate"]
        if gate == "script":
            _print_script(payload["script"])
            decision = _ask("Approve script? [approve/reject]")
        elif gate == "voice_choice":
            decision = _ask_voice_choice(payload["candidates"])
        elif gate == "video":
            typer.secho(f"\nVideo ready: {payload['final_path']}", fg="green", bold=True)
            if payload.get("thumbnail_path"):
                typer.echo(f"Thumbnail: {payload['thumbnail_path']}")
            typer.echo("Open and watch it, then come back here.")
            decision = _ask("Approve to publish, or reject to discard? [approve/reject]")
        else:
            raise RuntimeError(f"Unknown gate: {gate!r}")
        result = graph.invoke(Command(resume=decision), cfg)

    typer.secho(f"\nDone. Final file: {result['final_path']}", fg="green", bold=True)
    pr = result.get("publish_result", {})
    if "youtube_url" in pr:
        typer.secho(f"YouTube: {pr['youtube_url']}", fg="green")
    if "youtube_error" in pr:
        typer.secho(f"YouTube FAILED: {pr['youtube_error']}", fg="red")
    if "instagram_media_id" in pr:
        typer.secho(f"Instagram: published (media id {pr['instagram_media_id']})", fg="green")
    if "instagram_error" in pr:
        typer.secho(f"Instagram FAILED: {pr['instagram_error']}", fg="red")
    if "linkedin_post_id" in pr:
        typer.secho(f"LinkedIn: published (post id {pr['linkedin_post_id']})", fg="green")
    if "linkedin_error" in pr:
        typer.secho(f"LinkedIn FAILED: {pr['linkedin_error']}", fg="red")
    return result


@app.command()
def build(topic: str):
    """Run the full pipeline for one topic, pausing at both human gates."""
    db.init_db()
    run_id = new_run_id()
    db.create_run(run_id, topic)
    run_dir = run_dir_for(run_id)
    typer.secho(f"\nRun {run_id}: \"{topic}\"", bold=True)

    with SqliteSaver.from_conn_string(str(config.RUNS_DIR / "checkpoints.db")) as checkpointer:
        graph = build_graph(checkpointer)
        cfg = {"configurable": {"thread_id": run_id}}
        result = graph.invoke(
            {"run_id": run_id, "topic": topic, "run_dir": str(run_dir)}, cfg
        )
        _run_pipeline_gates(graph, cfg, result)


@app.command()
def build_script(
    script_file: str,
    topic: str = typer.Option(None, help="Optional label; defaults to the filename."),
):
    """
    Run the pipeline on narration YOU wrote yourself -- your words go
    through untouched, no LLM rewriting. Only title/description/hashtags
    are generated.

    script_file: a plain text file, first line = hook, every line after
    = one body beat, blank lines ignored.
    """
    db.init_db()
    path = Path(script_file)
    if not path.exists():
        typer.secho(f"No such file: {path}", fg="red")
        raise typer.Exit(1)
    lines = path.read_text(encoding="utf-8").splitlines()
    topic = topic or path.stem

    run_id = new_run_id()
    db.create_run(run_id, topic)
    run_dir = run_dir_for(run_id)
    typer.secho(f"\nRun {run_id}: \"{topic}\" (from {path.name})", bold=True)

    typer.echo("Generating title/description/hashtags for your script...")
    script = build_script_from_lines(topic, lines)

    with SqliteSaver.from_conn_string(str(config.RUNS_DIR / "checkpoints.db")) as checkpointer:
        graph = build_custom_graph(checkpointer)
        cfg = {"configurable": {"thread_id": run_id}}
        result = graph.invoke(
            {
                "run_id": run_id,
                "topic": topic,
                "run_dir": str(run_dir),
                "script": script.model_dump(),
            },
            cfg,
        )
        _run_pipeline_gates(graph, cfg, result)


@app.command()
def format_script(
    raw_file: str,
    output: str = typer.Option(
        None, help="Output path; defaults to <raw_file stem>_formatted.txt"
    ),
):
    """
    Convert a raggedly-formatted script draft (markdown headers like
    "### [0-5 sec — Hook]", bold timestamps, narration fragmented across
    lines -- any shape) into this project's clean beat-per-line format,
    with a first-pass broll/text_card + query suggestion per beat.

    Narration wording is preserved verbatim -- only structure/formatting
    and visual suggestions come from the LLM. Review/edit the output
    before running `build-script` on it; suggested queries are a
    starting point, not final.
    """
    path = Path(raw_file)
    if not path.exists():
        typer.secho(f"No such file: {path}", fg="red")
        raise typer.Exit(1)
    raw_text = path.read_text(encoding="utf-8")
    out_path = Path(output) if output else path.with_name(f"{path.stem}_formatted.txt")

    typer.echo("Formatting script...")
    beats = format_raw_script(raw_text)
    out_path.write_text(beats_to_script_text(beats), encoding="utf-8")

    typer.secho(f"\nWrote {len(beats)} beats -> {out_path}", fg="green", bold=True)
    typer.secho("=" * 60, fg="cyan")
    for i, b in enumerate(beats, 1):
        typer.echo(f"\n{i}. [{b.visual}: {b.query}]")
        typer.echo(f"   {b.text}")
    typer.secho("\n" + "=" * 60, fg="cyan")
    typer.echo(f"\nReview/edit {out_path}, then run:\n  build-script {out_path}")


@app.command()
def costs():
    """Show total spend this month (Phase 1 is always $0 -- this is future-proofing)."""
    db.init_db()
    typer.echo(f"Spent this month: ${db.total_spent_this_month():.2f}")


@app.command(name="telegram-bot")
def telegram_bot_cmd():
    """
    Start the Telegram bot: message it a script (any format, text or
    .txt file) and it runs the whole pipeline in the background, with
    every gate (script approval, voice-take pick, video approval)
    surfaced as Telegram messages instead of terminal prompts.

    Long-running, blocking -- start it in its own terminal, Ctrl-C to
    stop. Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.
    """
    from .telegram_bot import run_bot
    db.init_db()
    run_bot()


if __name__ == "__main__":
    app()
