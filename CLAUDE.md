# TalkHead / TechTales Content Factory: instructions for Claude sessions

## One-off reels (important, Pankaj 2026-10-16)
- One-off / special reels (e.g. brand-footage explainers like the Tesla Roadster reel) are built in `runs/oneoff-<name>-<date>/` with throwaway scripts that only IMPORT project helpers. **Never change the daily pipeline code, workflow or config for a one-off.** Keep the daily videos safe.
- Rules, learning loop, performance log and reference examples live in `docs/oneoff-reels/` (start with its README.md). Checklist: `/Users/pankajkumar/Documents/AI/AI_Content/YouTube/YouTube_Video/Brand-Footage-Special-Checklist.md`.
- Human approval gates stay: show the script, send the finished video to Telegram without Publish buttons, Pankaj publishes by hand. No paid tools without his approval of the shot and the cost.
- Core AI-engineering education content continues; one-offs complement it.

## Testing note
When testing code that reads `config.*` locally, a variable exported in the shell overrides `.env` (dotenv does not override). Use `env -u VAR` or check `os.environ`.
