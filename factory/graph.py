"""
LangGraph pipeline: script -> [GATE 1] -> voice (-> [GATE 1.5], only when
takes are stochastic and unpinned) -> captions -> assemble -> [GATE 2].

Each gate is its own node that does no expensive work itself — it only calls
interrupt(). That split matters: LangGraph re-runs a node from the top on
resume, so if script generation and the approval check were the same node,
approving the script would silently regenerate (and possibly change) it.
Splitting "do the work" from "ask permission" into separate nodes means only
the cheap gate re-runs on resume, never the expensive step before it.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from . import config, db
from .agents.script_agent import generate_script
from .media.voice import get_voice_engine
from .media.captions import transcribe_words, beat_timings_from_words
from .media.assemble import assemble_reel
from .media.thumbnail import generate_thumbnail
from .models import Script


class PipelineState(TypedDict, total=False):
    run_id: str
    topic: str
    run_dir: str
    script: dict
    voiceover_wav: str
    voice_candidates: list
    words: list
    final_path: str
    thumbnail_path: str
    thumbnail_vertical_path: str
    publish_result: dict


def run_dir_for(run_id: str) -> Path:
    d = config.RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- nodes ----------------------------------------------------------------

def node_generate_script(state: PipelineState) -> dict:
    db.log_event(state["run_id"], "generate_script", "started")
    script = generate_script(state["topic"])
    db.log_event(state["run_id"], "generate_script", "done",
                 f"{script.word_count} words")
    return {"script": script.model_dump()}


def node_approve_script(state: PipelineState) -> dict:
    decision = interrupt({
        "gate": "script",
        "script": state["script"],
        "message": "Approve this script? (approve / reject)",
    })
    if decision != "approve":
        db.log_event(state["run_id"], "approve_script", "rejected", str(decision))
        raise RuntimeError(f"Script rejected at Gate 1: {decision}")
    db.log_event(state["run_id"], "approve_script", "approved")
    db.mark_status(state["run_id"], "script_approved", approved_script_at="now")
    return {}


def node_voice(state: PipelineState) -> dict:
    script = Script.model_validate(state["script"])
    run_dir = Path(state["run_dir"])
    engine = get_voice_engine()
    wav_path = engine.synthesize(script.full_narration, run_dir / "voiceover.wav")
    db.log_event(state["run_id"], "voice", "done", str(wav_path))
    return {"voiceover_wav": str(wav_path)}


def _batch_voice_active() -> bool:
    """
    Chatterbox samples stochastically -- same input can be a noticeably
    better or worse take by chance. Batch-generating candidates only makes
    sense when that variance is actually in play: Kokoro is deterministic
    (every take would be identical), and a pinned FACTORY_VOICE_SEED means
    you already found a take you like and don't want to re-pick every run.
    """
    return config.VOICE_ENGINE == "chatterbox" and config.CHATTERBOX_SEED is None


def node_voice_candidates(state: PipelineState) -> dict:
    script = Script.model_validate(state["script"])
    run_dir = Path(state["run_dir"])
    candidates_dir = run_dir / "voice_candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for i in range(config.VOICE_CANDIDATE_COUNT):
        # A FRESH engine instance per take, not one instance called
        # repeatedly -- ChatterboxVoice.synthesize() remembers whatever
        # seed it just rolled (so a second call on the SAME instance would
        # reproduce the first take instead of rolling a new one). Fresh
        # instances each start from config.CHATTERBOX_SEED (None here, by
        # _batch_voice_active()'s check), so each one rolls its own random
        # seed independently.
        engine = get_voice_engine()
        out_path = candidates_dir / f"take_{i + 1}.wav"
        engine.synthesize(script.full_narration, out_path)
        inner = getattr(engine, "inner", engine)  # unwrap SpeedAdjustedVoice
        candidates.append({"path": str(out_path), "seed": getattr(inner, "seed", None)})

    db.log_event(state["run_id"], "voice_candidates", "done", f"{len(candidates)} takes")
    return {"voice_candidates": candidates}


def node_choose_voice(state: PipelineState) -> dict:
    candidates = state["voice_candidates"]
    decision = interrupt({
        "gate": "voice_choice",
        "candidates": candidates,
        "message": f"Listen to each take, then enter its number (1-{len(candidates)}).",
    })
    try:
        chosen = candidates[int(decision) - 1]
    except (ValueError, IndexError, TypeError):
        db.log_event(state["run_id"], "choose_voice", "rejected", str(decision))
        raise RuntimeError(f"Invalid voice choice: {decision!r}")

    # Copy the winner to the same canonical path node_voice() would have
    # used -- everything downstream (and any external tooling) can keep
    # assuming voiceover.wav lives at this one fixed location regardless
    # of which path produced it.
    final_wav = Path(state["run_dir"]) / "voiceover.wav"
    shutil.copy(chosen["path"], final_wav)
    db.log_event(state["run_id"], "choose_voice", "chosen",
                 f"seed={chosen['seed']}, path={chosen['path']}")
    return {"voiceover_wav": str(final_wav)}


def node_captions(state: PipelineState) -> dict:
    words = transcribe_words(Path(state["voiceover_wav"]))
    db.log_event(state["run_id"], "captions", "done", f"{len(words)} words")
    return {"words": words}


def node_assemble(state: PipelineState) -> dict:
    import soundfile as sf

    script = Script.model_validate(state["script"])
    run_dir = Path(state["run_dir"])
    beats = script.all_beats
    word_counts = [len(b.text.split()) for b in beats]
    # Real audio length, not derived from transcription -- see
    # beat_timings_from_words' docstring for why this matters.
    audio_duration = sf.info(state["voiceover_wav"]).duration
    timings = beat_timings_from_words(word_counts, state["words"], audio_duration)
    final_path = assemble_reel(
        script, timings, Path(state["voiceover_wav"]), state["words"], run_dir
    )
    db.log_event(state["run_id"], "assemble", "done", str(final_path))
    return {"final_path": str(final_path)}


def node_thumbnail(state: PipelineState) -> dict:
    script = Script.model_validate(state["script"])
    run_dir = Path(state["run_dir"])
    paths = generate_thumbnail(script, run_dir)
    db.log_event(state["run_id"], "thumbnail", "done", str(paths["landscape"]))
    return {
        "thumbnail_path": str(paths["landscape"]),
        "thumbnail_vertical_path": str(paths["vertical"]),
    }


def node_approve_video(state: PipelineState) -> dict:
    """
    Approve = publish. Reject = discard, nothing goes online -- see
    BACKLOG.md for that decision. Publishing itself lives in the
    SEPARATE node_publish() below, not here: this node only calls
    interrupt(), same discipline as every other gate, and for the same
    reason the module docstring gives -- but doubly important here,
    since a real publish to YouTube/Instagram is externally irreversible
    in a way a re-run of a cheap gate node is not.
    """
    decision = interrupt({
        "gate": "video",
        "final_path": state["final_path"],
        # .get(), not []: a checkpoint from before the thumbnail node
        # existed won't have this key -- resuming (e.g. to reject) an old
        # in-flight run under newer code shouldn't KeyError, since the
        # whole node re-runs from the top on resume (see module docstring).
        "thumbnail_path": state.get("thumbnail_path"),
        "message": "Watch the video, then approve to publish, or reject to discard?",
    })
    if decision != "approve":
        db.log_event(state["run_id"], "approve_video", "rejected", str(decision))
        raise RuntimeError(f"Video rejected at Gate 2: {decision}")
    db.log_event(state["run_id"], "approve_video", "approved")
    return {}


def node_publish(state: PipelineState) -> dict:
    from .publish import publish_video

    script = Script.model_validate(state["script"])
    final_path = Path(state["final_path"])
    thumbnail_path = Path(state["thumbnail_path"]) if state.get("thumbnail_path") else None
    result = publish_video(script, final_path, thumbnail_path)

    ok = any(k in result for k in ("youtube_url", "instagram_media_id", "linkedin_post_id"))
    partial = any(k in result for k in ("youtube_error", "instagram_error", "linkedin_error"))
    status = "published" if ok and not partial else ("publish_failed" if not ok else "published_partial")
    db.mark_status(state["run_id"], status,
                    approved_video_at="now", final_path=str(final_path))
    db.log_event(state["run_id"], "publish", status, str(result))
    return {"publish_result": result}


def _route_after_script_approval(state: PipelineState) -> str:
    return "voice_candidates" if _batch_voice_active() else "voice"


def _add_voice_and_rest(g: StateGraph) -> None:
    """Shared by both graph builders below: everything from Gate 1 approval
    onward is identical, whether or not the script itself was LLM-generated."""
    g.add_node("voice", node_voice)
    g.add_node("voice_candidates", node_voice_candidates)
    g.add_node("choose_voice", node_choose_voice)
    g.add_node("captions", node_captions)
    g.add_node("assemble", node_assemble)
    g.add_node("thumbnail", node_thumbnail)
    g.add_node("approve_video", node_approve_video)
    g.add_node("publish", node_publish)

    g.add_conditional_edges("approve_script", _route_after_script_approval, {
        "voice_candidates": "voice_candidates",
        "voice": "voice",
    })
    g.add_edge("voice_candidates", "choose_voice")
    g.add_edge("choose_voice", "captions")
    g.add_edge("voice", "captions")
    g.add_edge("captions", "assemble")
    g.add_edge("assemble", "thumbnail")
    g.add_edge("thumbnail", "approve_video")
    g.add_edge("approve_video", "publish")
    g.add_edge("publish", END)


def build_graph(checkpointer):
    g = StateGraph(PipelineState)
    g.add_node("generate_script", node_generate_script)
    g.add_node("approve_script", node_approve_script)
    _add_voice_and_rest(g)

    g.add_edge(START, "generate_script")
    g.add_edge("generate_script", "approve_script")

    return g.compile(checkpointer=checkpointer)


def build_custom_graph(checkpointer):
    """
    Same pipeline, minus script generation -- for a script you wrote
    yourself, supplied already in the initial state. Starts straight at
    Gate 1 so you still confirm the metadata before anything gets voiced.
    """
    g = StateGraph(PipelineState)
    g.add_node("approve_script", node_approve_script)
    _add_voice_and_rest(g)

    g.add_edge(START, "approve_script")

    return g.compile(checkpointer=checkpointer)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
