# TikTok / Shorts Relationship Story Generator

Minimal pipeline for the first manual-upload phase:

1. Pull relationship-drama candidates from public RSS feeds.
2. Use the candidates only as a radar for conflict patterns.
3. Score every candidate with a strict editorial filter.
4. Generate one original English script only when the best story scores at least 7/10.
5. Create voice-over, subtitles, a vertical video, metadata, and a QC report.
6. Leave upload as a manual step for TikTok and YouTube Shorts.

This repo intentionally does not auto-publish during the current phase.

## Required Secrets

- `GEMINI_API_KEY`: required for candidate scoring and script generation.
- `PEXELS_API_KEY`: optional. If missing, the video uses a local animated background.
- `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`: optional. If missing, the package remains in `output/`.

Optional variables:

- `GEMINI_MODEL`, default `gemini-3-flash-preview`
- `MIN_EDITORIAL_SCORE`, default `7.0`
- `TTS_VOICE`, default `en-US-JennyNeural`
- `TTS_RATE`, default `+6%`
- `USE_BG_MUSIC`, default `false`. Enable only if you have cleared rights for `bg_music.mp3`.

## Run Locally

```bash
pip install -r requirements.txt
python main.py --self-test
python main.py --dry-run
python main.py --once
```

The generated package is written to:

- `output/final_video.mp4`
- `output/metadata.json`
- `output/qc_report.json`
- `output/candidate_scores.json`

## Manual Upload Checklist

Before uploading, watch `output/final_video.mp4` once and confirm:

- duration is at least 60 seconds;
- the hook works without extra context;
- the middle has real escalation;
- the ending pays off the hook;
- subtitles are acceptably synced;
- the story is transformed and does not copy source wording;
- the caption matches the account tone.

After upload, update `videos_log.csv` with views, likes, comments, retention, completion rate, follower gain, and real revenue if any.
