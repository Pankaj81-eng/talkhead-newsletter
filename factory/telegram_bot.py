"""
Telegram bot: message a raw script to this bot and it runs the whole
pipeline in the background -- format-script's cleanup, every gate
(script approval, voice-take pick with the actual candidate audio files,
video approval) surfaced as Telegram messages instead of terminal
prompts, final video delivered back here when done. See BACKLOG.md's
"Telegram integration" entry for the full design rationale.

Single-user by design -- config.TELEGRAM_CHAT_ID is the only chat this
bot will ever act on. A bot token alone doesn't restrict who can message
it, so this check IS the actual access control; every other chat's
messages are silently ignored.

Run with `factory telegram-bot` (see cli.py) -- a long-running process
you start manually in a terminal, matching this project's local-first,
zero-cost design. No hosting, no server.
"""
from __future__ import annotations

import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import config, db
from .agents.script_agent import beats_to_script_text, build_script_from_lines, format_raw_script
from .graph import build_custom_graph, build_graph, new_run_id, run_dir_for

# Telegram Bot API's own upload ceiling for bots -- a video larger than
# this can't be sent as a video attachment at all, only mentioned by path.
_MAX_UPLOAD_MB = 49

# A gate pending this long or more gets re-shown instead of silently
# acting on the next reply -- confirmed the hard way (2026-09-01): a
# video sat unanswered at Gate 2 for ~8 hours, then a LATER message sent
# for a completely different reason (starting a new script) happened to
# match one of the old approve/reject shorthands and got silently
# consumed as approving that stale gate, triggering a real, unintended
# publish to all 3 platforms. Two independent fixes for this: (1) below,
# shorthand synonyms ("a"/"y"/"yes" etc.) are gone, only the exact words
# "approve"/"reject" count now, which alone would have prevented that
# specific incident. (2) this staleness guard is defense in depth for
# the case where a later message genuinely IS the exact word "approve"
# by coincidence (e.g. answering something else entirely) -- a gate this
# old gets re-displayed and re-confirmed rather than acted on blind.
_STALE_GATE_SECONDS = 30 * 60


def _slug(text: str, n: int = 6) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())[:n]
    return "-".join(words) or "telegram-script"


@dataclass
class _Session:
    """One pipeline run in flight for the single trusted chat -- None when
    idle. checkpointer_cm is the SqliteSaver context manager itself, kept
    open for exactly this run's lifetime (opened when the run starts,
    closed when it finishes or errors), same scoping the CLI uses per
    invocation -- just stretched across several Telegram messages instead
    of one blocking terminal session."""
    graph: object
    cfg: dict
    run_id: str
    checkpointer_cm: object
    pending_gate: str | None = None
    pending_gate_payload: dict | None = None  # so a stale gate can be re-shown verbatim
    pending_since: float = field(default_factory=time.monotonic)


_session: _Session | None = None


def _authorized(update: Update) -> bool:
    return str(update.effective_chat.id) == str(config.TELEGRAM_CHAT_ID)


def _close_session(status: str | None = None) -> None:
    """
    status: an explicit final status to record, for the reject/error paths.
    None (the default) means don't touch it -- the success path already
    marks the run 'completed' from inside graph.py's node_approve_video,
    so closing the session here shouldn't clobber that.
    """
    global _session
    if _session is not None:
        if status:
            db.mark_status(_session.run_id, status)
        try:
            _session.checkpointer_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
    _session = None


def _open_session(run_id: str, pending_gate: str | None = None, use_llm_script: bool = False) -> _Session:
    """use_llm_script=True builds the graph starting at generate_script
    (see graph.py's build_graph) -- for a /topic run, where the narration
    itself still needs writing. Default (False) is build_custom_graph,
    for a raw script pasted in by hand (see handle_message)."""
    checkpointer_cm = SqliteSaver.from_conn_string(str(config.RUNS_DIR / "checkpoints.db"))
    checkpointer = checkpointer_cm.__enter__()
    graph = build_graph(checkpointer) if use_llm_script else build_custom_graph(checkpointer)
    cfg = {"configurable": {"thread_id": run_id}}
    return _Session(graph=graph, cfg=cfg, run_id=run_id, checkpointer_cm=checkpointer_cm,
                     pending_gate=pending_gate)


async def _post_init(application) -> None:
    """
    If the bot died or got restarted while a run was parked at a gate,
    reconnect to it instead of starting fresh -- confirmed the hard way
    once already: with no memory of the pending run, the next message
    (a plain "approve") got silently misread as a brand-new raw script
    instead of the gate reply it actually was. The run's own progress
    was never lost (LangGraph's checkpoint survives regardless of
    process lifetime) -- only this bot's *tracking* of which gate was
    pending lived in memory and needed rebuilding.
    """
    global _session
    pending = db.get_pending_gate_run()
    if pending is None:
        return
    run_id, gate = pending["id"], pending["status"].removeprefix("awaiting_")
    # build_graph is a strict superset of build_custom_graph (same shared
    # nodes from approve_script onward, see graph.py's _add_voice_and_rest)
    # -- safe to always rehydrate with it regardless of which one the run
    # actually started with, since we're resuming from a checkpoint at
    # `gate`, never re-running the earlier generate_script/approve_script
    # nodes that might differ.
    _session = _open_session(run_id, pending_gate=gate, use_llm_script=True)
    print(f"[telegram_bot] rehydrated pending run {run_id}, gate={gate}")
    hint = "a number" if gate == "voice_choice" else "'approve' or 'reject'"
    await application.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=f"(Restarted -- still waiting on your reply for run {run_id}. "
             f"Reply with {hint} to continue.)",
    )


async def _send_gate(update: Update, payload: dict) -> None:
    gate = payload["gate"]

    if gate == "script":
        script = payload["script"]
        lines = [f"*{script['title']}*", script["description"], ""]
        lines.append(f"HOOK [{script['hook']['visual']}: {script['hook']['query']}]")
        lines.append(script["hook"]["text"])
        for i, b in enumerate(script["body"], 1):
            lines.append(f"\n{i}. [{b['visual']}: {b['query']}]\n{b['text']}")
        lines.append("\nReply *approve* or *reject*.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif gate == "voice_choice":
        candidates = payload["candidates"]
        for i, c in enumerate(candidates, 1):
            with open(c["path"], "rb") as f:
                await update.message.reply_audio(audio=f, caption=f"Take {i} (seed={c['seed']})")
        await update.message.reply_text(f"Reply with a number 1-{len(candidates)}.")

    elif gate == "video":
        path = Path(payload["final_path"])
        size_mb = path.stat().st_size / (1024 * 1024)
        caption = ("Reply *approve* to publish to YouTube + Instagram + LinkedIn, "
                   "or *reject* to discard.")
        thumb = payload.get("thumbnail_path")
        if thumb and Path(thumb).exists():
            with open(thumb, "rb") as f:
                await update.message.reply_photo(photo=f, caption="Thumbnail")
        if size_mb <= _MAX_UPLOAD_MB:
            with open(path, "rb") as f:
                await update.message.reply_video(video=f, caption=caption, parse_mode="Markdown")
        else:
            # Telegram bots can't upload anything bigger than this at all --
            # not a size we chose, a hard platform ceiling.
            await update.message.reply_text(
                f"Video ready ({size_mb:.1f}MB -- too large for Telegram to "
                f"send, {_MAX_UPLOAD_MB}MB is the bot API's own ceiling): "
                f"{path}\n\n{caption}",
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(f"Unknown gate: {gate!r} -- something's out of sync.")


def _publish_summary(result: dict) -> str:
    pr = result.get("publish_result", {})
    lines = [f"Final file: {result['final_path']}", ""]
    if "youtube_url" in pr:
        lines.append(f"YouTube: {pr['youtube_url']}")
    if "youtube_error" in pr:
        lines.append(f"YouTube FAILED: {pr['youtube_error']}")
    if "instagram_media_id" in pr:
        lines.append(f"Instagram: published (media id {pr['instagram_media_id']})")
    if "instagram_error" in pr:
        lines.append(f"Instagram FAILED: {pr['instagram_error']}")
    if "linkedin_post_id" in pr:
        lines.append(f"LinkedIn: published (post id {pr['linkedin_post_id']})")
    if "linkedin_error" in pr:
        lines.append(f"LinkedIn FAILED: {pr['linkedin_error']}")
    if not pr:
        lines.append("(No publish_result -- run finished without publishing.)")
    return "\n".join(lines)


async def _advance(update: Update, result: dict) -> None:
    """Walk interrupts until the next gate needs a human, or the run finishes."""
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        _session.pending_gate = payload["gate"]
        _session.pending_gate_payload = payload
        _session.pending_since = time.monotonic()
        # Durable, not just in-memory -- see _post_init()'s docstring for
        # why this matters (a bot restart must be able to find this again).
        db.mark_status(_session.run_id, f"awaiting_{payload['gate']}")
        await _send_gate(update, payload)
    else:
        await update.message.reply_text(_publish_summary(result))
        _close_session()  # status already set inside node_publish


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Two ways to start a run:\n\n"
        "/topic <brief> -- give me the story/facts, I'll WRITE the script "
        "(hook + beats) myself, then ask you to approve it.\n\n"
        "Or just send a script you already wrote (any format -- markdown "
        "headers, bold, whatever) as a text message or a .txt file, and "
        "I'll only clean up formatting + write title/description/hashtags, "
        "your narration stays verbatim.\n\n"
        "Either way: approve the script, pick a voice take if asked, then "
        "approve the final video."
    )


async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /topic <brief> -- for a story you want the LLM to actually WRITE a
    script for (generate_script(), same as the CLI's `build` command),
    as opposed to plain messages/documents (handle_message below), which
    always treat the input as narration you already wrote verbatim.
    Accepts a long, multi-paragraph brief in the same message -- only the
    leading "/topic" token is stripped, everything else (including line
    breaks) passes straight to generate_script() as-is.
    """
    global _session
    if not _authorized(update):
        return
    if _session is not None:
        await update.message.reply_text(
            "A run is already in progress -- finish or reject it first."
        )
        return

    text = update.message.text or ""
    topic = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    if not topic:
        await update.message.reply_text(
            "Send /topic followed by the brief, e.g.:\n/topic OpenAI just launched..."
        )
        return

    await update.message.reply_text("Got it -- writing the script...")

    try:
        db.init_db()
        run_id = new_run_id()
        db.create_run(run_id, _slug(topic))
        run_dir = run_dir_for(run_id)
        (run_dir / "telegram_topic.txt").write_text(topic, encoding="utf-8")

        _session = _open_session(run_id, use_llm_script=True)
        result = _session.graph.invoke(
            {"run_id": run_id, "topic": topic, "run_dir": str(run_dir)}, _session.cfg
        )
        await _advance(update, result)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        await update.message.reply_text(f"Something went wrong: {e}")
        status = "rejected" if "rejected at gate" in str(e).lower() else "error"
        _close_session(status=status)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _session
    if not _authorized(update):
        return

    try:
        if _session is not None and _session.pending_gate is not None:
            gate = _session.pending_gate
            raw = (update.message.text or "").strip()

            # Staleness guard: a gate sitting this long doesn't get acted
            # on by the next reply, even a correct one -- it gets
            # re-shown, and the NEXT reply after that is what actually
            # counts. See _STALE_GATE_SECONDS' comment for why.
            age = time.monotonic() - _session.pending_since
            if age >= _STALE_GATE_SECONDS and _session.pending_gate_payload is not None:
                _session.pending_since = time.monotonic()
                await update.message.reply_text(
                    f"(This has been waiting {int(age // 60)} min -- "
                    f"re-showing it. Reply again to confirm.)"
                )
                await _send_gate(update, _session.pending_gate_payload)
                return

            if gate == "voice_choice":
                decision = raw  # a digit, passed through as-is
            else:
                # Exact words only -- no "a"/"y"/"yes" shorthand. Confirmed
                # the hard way: a short reply meant for something else
                # entirely can coincidentally match a loose synonym and
                # get silently consumed as a real, irreversible publish
                # decision. Typing the actual word is cheap; this is not
                # the place to save a keystroke.
                lowered = raw.lower()
                if lowered == "approve":
                    decision = "approve"
                elif lowered == "reject":
                    decision = "reject"
                else:
                    await update.message.reply_text("Reply 'approve' or 'reject'.")
                    return
            _session.pending_gate = None
            _session.pending_gate_payload = None
            result = _session.graph.invoke(Command(resume=decision), _session.cfg)
            await _advance(update, result)
            return

        # No run in flight -- treat this message as a new raw script,
        # from either plain text or an attached .txt file.
        if update.message.document:
            file = await update.message.document.get_file()
            raw_bytes = await file.download_as_bytearray()
            raw_text = bytes(raw_bytes).decode("utf-8", errors="replace")
        else:
            raw_text = update.message.text or ""

        if not raw_text.strip():
            await update.message.reply_text("Send the script as text or a .txt file.")
            return

        await update.message.reply_text("Got it -- formatting and starting the pipeline...")

        topic = _slug(next((l for l in raw_text.splitlines() if l.strip()), "telegram-script"))
        db.init_db()
        run_id = new_run_id()
        db.create_run(run_id, topic)
        run_dir = run_dir_for(run_id)
        (run_dir / "telegram_script_raw.txt").write_text(raw_text, encoding="utf-8")

        beats = format_raw_script(raw_text)
        lines = beats_to_script_text(beats).splitlines()
        (run_dir / "telegram_script_formatted.txt").write_text("\n".join(lines), encoding="utf-8")
        script = build_script_from_lines(topic, lines)

        _session = _open_session(run_id)
        result = _session.graph.invoke(
            {"run_id": run_id, "topic": topic, "run_dir": str(run_dir), "script": script.model_dump()},
            _session.cfg,
        )
        await _advance(update, result)

    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        await update.message.reply_text(f"Something went wrong: {e}")
        # Explicit final status so get_pending_gate_run() doesn't keep
        # finding this run and re-offering it after a future restart --
        # "rejected" (a real Gate decision) is distinguishable from any
        # other failure, everything else is just 'error'.
        status = "rejected" if "rejected at gate" in str(e).lower() else "error"
        _close_session(status=status)


def run_bot() -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first "
            "(see README.md / BACKLOG.md for how to get them)."
        )

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("topic", handle_topic))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_message))
    print(f"[telegram_bot] listening for chat_id={config.TELEGRAM_CHAT_ID} ... (Ctrl-C to stop)")
    app.run_polling()
