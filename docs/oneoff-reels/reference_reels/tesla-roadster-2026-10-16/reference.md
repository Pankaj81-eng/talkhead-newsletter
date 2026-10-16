# Reference reel: Tesla Roadster reveal (published 2026-10-16)

Status: first strong example of the "brand footage + our explainer" one-off format. A reference PATTERN, not a guarantee.

## Where the actual assets are
- Final video (59.7 s, 1080x1920): `runs/oneoff-tesla-roadster-2026-10-16/tesla_roadster_reel.mp4` (gitignored) and
  `/Users/pankajkumar/Documents/AI/AI_Content/YouTube/YouTube_Video/Tesla Roadster Video/TechTales_Output/`
- Source clips (Pankaj's phone screen recordings of Instagram, 576 px wide): same folder, `assets/` in the run folder.
- Build scripts (one-off, imports project helpers, edits nothing): `build_oneoff.py.txt`, `voice_best.py.txt` here (renamed .txt so nobody runs them as project code).
- Published by Pankaj by hand on Instagram, YouTube Shorts, TikTok (Instagram media id 18199498417387405, https://www.instagram.com/reel/DejLhbYof7D/).

## Hook and script
- Hook (spoken, after a 4.5 s cold open on Tesla's own footage with its original sound): "Tesla just showed a Roadster that launches off a ramp and flips through the air. But the engineering behind it is even more interesting than the video."
- On-screen opener: "TESLA CAN FLY?" (a question, not a claim).
- Structure: cold open (real footage + original sound) -> hook -> engineering explanation (cold-gas thrusters, suction) -> reality check ("not yet", prototype, nobody inside, not road approved, 0-60 claim unverified) -> production target as a target -> comment question -> follow line.
- Full text: `script.txt`. Captions: `captions.srt`. Metadata: `metadata.txt`.

## Editing choices
- 59.7 s, no intro/outro (pace and quality over the 45 s budget, by Pankaj's choice).
- 19 shots: Tesla clips cropped out of the phone recording (Instagram UI removed) over a blurred fill, Tesla press stills with slow zoom, labelled overlays at the TOP (captions lower), "Source: Tesla" always visible.
- Audio: Tesla's original sound for 4.5 s at about the same loudness as the voice, faded down to 20% under the cloned voice; -15 LUFS overall.
- Voice: each sentence re-recorded with several seeds, closest to 2.6 words/sec kept, key words checked with speech recognition.
- Cost: no paid tools, about 2.5 hours of assistant time end to end (research, script, voice, build), manual step: Pankaj saved the clips.

## Published performance
See `../../performance_log.csv` (observed numbers, with the elapsed time at capture).
