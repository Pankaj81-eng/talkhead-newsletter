"""EXAMPLE (not part of the daily pipeline): repair the visuals of one finished run, keeping its voice, captions and timing.

Used by hand on 2026-10-25 for run cef5bef72f77: a new hook clip (real delivery robot) and a replacement for a blurry
9.8 s stretch. Imports project helpers only. To reuse: change RUN, the clip paths and which beats to rebuild, run it from
the project root with the project's .venv python, look at the contact sheet, then copy fix/final.mp4 over the run's
final.mp4 (keep a backup) BEFORE anyone presses Publish. Turning this into one reusable command is listed in
docs/KNOWN-ISSUES.md.
"""
import sys, json, warnings
warnings.filterwarnings("ignore")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factory import config
from factory.utils import probe_duration
from factory.media.assemble import _video_shot, _still_image_clip, _fill_frame, concat_clips, mux_audio, burn_captions
from factory.media.captions import transcribe_words, align_to_script, render_caption_images

RUN = "cef5bef72f77"
R = ROOT / "runs" / RUN
FIX = R / "fix"; FIX.mkdir(exist_ok=True)
FPS = config.OUT_FPS
HOOK = ROOT / "cache/broll/ec594de2fab13138.mp4"        # replacement hook clip (stock video found and checked by eye)
CANE = ROOT / "cache/broll/69178f8e57b5454e.mp4"

# beat 0: new hook video, same length as the original beat
d0 = probe_duration(R / "beat_00.mp4")
beat0 = FIX / "beat_00_new.mp4"
_video_shot(HOOK, round(d0 * FPS), 9.0, beat0)

# beat 2: three cuts replacing a blurry single clip (still -> video -> still)
d2 = probe_duration(R / "beat_02.mp4")
total = round(d2 * FPS); base, extra = divmod(total, 3)
fr = [base + (1 if k < extra else 0) for k in range(3)]
f04 = _fill_frame(R / "article_images/img_04.jpg", FIX / "img_04_frame.png")
c0 = FIX / "beat_02_cut0.mp4"; _still_image_clip(f04, fr[0] / FPS, c0, zoom_out=False)
c1 = FIX / "beat_02_cut1.mp4"; _video_shot(CANE, fr[1], 0.2, c1)
f01 = R / "article_images/img_01_frame.png"
if not f01.exists():
    f01 = _fill_frame(R / "article_images/img_01.jpg", FIX / "img_01_frame.png")
c2 = FIX / "beat_02_cut2.mp4"; _still_image_clip(f01, fr[2] / FPS, c2, zoom_out=True)
beat2 = FIX / "beat_02_new.mp4"
concat_clips([c0, c1, c2], beat2)

# rebuild the body, re-mux the SAME voiceover, re-burn captions (script text comes from state/published_log.json)
body = FIX / "body_video.mp4"
concat_clips([beat0, R / "beat_01.mp4", beat2, R / "beat_03.mp4", R / "beat_04.mp4"], body)
wav = R / "voiceover.wav"
body_audio = FIX / "body_with_audio.mp4"
mux_audio(body, wav, body_audio)
log = json.load(open(ROOT / "state" / "published_log.json"))
narr = [e for e in log if e.get("run_id") == RUN][-1]["full_narration"]
words = align_to_script(transcribe_words(wav), narr)
caps = render_caption_images(words, FIX / "captions")
captioned = FIX / "body_captioned.mp4"
burn_captions(body_audio, caps, captioned)
final = FIX / "final.mp4"
concat_clips([captioned, R / "outro_leveled.mp4"], final, faststart=True)
print("FINAL", final, probe_duration(final), "original", probe_duration(R / "final.mp4"))
