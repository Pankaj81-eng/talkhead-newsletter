# 90-day plan: name first, income second (30 Oct 2026 to late Jan 2027)

Written 30 Oct 2026. Goal from Pankaj: build a name over the long run and earn enough from YouTube to cover expenses and the effort put in.

Targets below are goals to steer by, not predictions. Where a number is a guess, it says so.

## Starting point (29 Oct 2026)

| Item | Value |
|---|---|
| YouTube subscribers | 914 |
| Watch time, last 365 days | about 76 h (4,579 min) |
| Shorts views, last 90 days | 33,863 (about 56% from ads) |
| Organic views, last 7 days | about 1,225 (0% ads) |
| Instagram followers | 1,733, falling about 13/week |
| Instagram Reels | about 190 views, 6.6 s watched of about 45 s |

Long-form videos, last 90 days (about 11.6 watch hours in total):

| Video | Length | Views | Viewed | Notes |
|---|---|---|---|---|
| Fresher to AI Engineer in 2026 Complete Roadmap | 18:08 | 321 | 8% | 0 subscribers |
| How to Call Gemini API with Python | 13:11 | 124 | 25% (194 s) | best watch time of the long ones |
| What Is the Claude Ecosystem? | 15:43 | 56 | 9% | |
| AI Test Case Generator | 5:19 | 155 | 26% | |
| What is MCP? | 3:52 | 146 | 26% | |
| Cursor AI for Beginners | 2:51 | 109 | n/a | 3 subscribers, best conversion |

The harness-engineering video (released 28 Oct) was not in this data yet. Analytics lags 1-2 days: pull its numbers early in week 1.

## What the numbers say

1. Roadmap-style titles get clicked but are not watched (8-9%).
2. Hands-on "build X" videos hold viewers (25-26%).
3. Long-form videos barely convert viewers into subscribers. Shorts do that job.
4. The Partner Programme needs 1,000 subscribers and 4,000 watch hours (12 months), or 10M Shorts views in 90 days. The fan-funding tier needs 500 subscribers and 3,000 hours, or 3M Shorts views. Check the current thresholds in YouTube Studio. At about 11.6 hours per 90 days, 4,000 hours is not reachable in this window.
5. So income in these 90 days has to come from the name (see "Income" below), not from ad revenue.

## The idea: build in public, one long video a week

Series: **"Building an AI news channel with Claude Code"**. The learning and the sharing are the same work.

Possible episodes, drawn from what already exists in the pipeline:
- How the pipeline works end to end
- Falling back across free AI models when a quota runs out
- Checking AI-picked pictures before they go in a video
- Cloning a voice and keeping it natural
- Post-mortem: the frozen-outro bug
- Post-mortem: LinkedIn failing silently, and the alert that fixed it
- Harness engineering (already published; use it as the first data point)

Alternate with **"AI Engineer roadmap 2.0"**, rebuilt for retention: shorter, built around one project, finished result shown first.

Keep client and employer details out, as already agreed.

## Rules for every long video

- 10-15 minutes. One a week. More is not sustainable next to the job.
- Show the finished result in the first 15 seconds, then explain.
- Add chapters. Specific title, thumbnail with one idea.
- Record while building, so learning and publishing are one task.
- Link the long video from that day's Short as the related video.
- Pin a comment with the next episode and the newsletter/email link.
- No paid views or paid subscribers.
- Daily Shorts keep running unchanged.

## Weekly targets

Dates assume week 1 starts Monday 2 Nov 2026.

| Week | Dates | Output | Target |
|---|---|---|---|
| 1 | 2-8 Nov | Long video #1; create the "AI Engineer" playlist; pinned comments | Baseline recorded (views, average viewed, subscribers) incl. harness-engineering video |
| 2 | 9-15 Nov | Video #2, tighter hook | 150+ views in first 7 days |
| 3 | 16-22 Nov | Video #3 | 30%+ average viewed |
| 4 | 23-29 Nov | Video #4; **checkpoint 1** | Keep or change format (see below) |
| 5 | 30 Nov-6 Dec | Video #5; LinkedIn clip of each video | Weekly organic Shorts views at least holding 1,200 |
| 6 | 7-13 Dec | Video #6 | 200+ views in first 7 days |
| 7 | 14-20 Dec | Roadmap 2.0 video | Average viewed above 25% (the old roadmap got 8%) |
| 8 | 21-27 Dec | Video #8; **checkpoint 2** | 1,000 subscribers; 300+ views in first 7 days |
| 9 | 28 Dec-3 Jan | Video #9; simple email signup in description | 50 email signups |
| 10 | 4-10 Jan | Video #10; write the first paid offer | Offer written |
| 11 | 11-17 Jan | Video #11; small pilot of the offer | First paid user or enquiry |
| 12 | 18-24 Jan | Video #12; full review | About 1,300 subscribers (a guess); 500+ views per video; choose the next 90 days |

Weekly rhythm: record and edit at the weekend, publish on a fixed day, read the scorecard on Monday. Add long-form numbers (views, average viewed, subscribers gained) to the weekly scorecard.

## Checkpoints

- **31 Oct two-week review (task `talkhead-2-week-review`):** read the Shorts side as planned; add the long-form baseline.
- **Week 4:** if average viewed is under 25% on all four videos, change the format (shorter, or more screen and less talking) before making more.
- **Week 8:** if first-week views have not at least doubled from week 1, change the title and thumbnail approach.
- **Week 12:** compare against the 29 Oct baseline above and the 8 Oct baseline (`talkhead-analytics-baseline-2026-10-08`); decide what continues.

## Income, honestly

- **Ad revenue:** not in this window. Expect roughly 150-250 watch hours at 90 days (a guess) against the 4,000-hour bar, unless Shorts views take off.
- **Realistic first income:** one small paid thing by week 12. Options:
  - a paid roadmap or CV/project review for freshers moving into AI engineering
  - a small mentoring or workshop pilot for the same audience as the roadmap video
  - tool affiliate links where Pankaj genuinely uses the tool
- Purpose of the first sale is proof that people pay, more than the amount.

## Instagram and TikTok

- **Instagram:** Reels pay little and followers are falling. Keep the automated posting (it costs nothing), but spend no extra effort or money there.
- **TikTok:** uploaded manually. Creator Rewards (as I know it: about 10k followers, 100k views in 30 days, videos over a minute) is far away. Do not pay for TikTok promotion.
- **LinkedIn:** best fit for a name and future paid offers. Post a clip of each long video there.

## What to stop

- Paid views or paid subscriber campaigns on any platform.
- Chasing the daily Shorts pipeline polish when the long-form series is the bigger lever (fixes only when something breaks).

## Open items

- Pull harness-engineering numbers once analytics catches up.
- Add long-form metrics to `scripts/scorecard.py`.
- Decide on a paid Gemini plan separately (quota is 20 requests/day on free tier).
