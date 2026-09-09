import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

import requests

import main
import media_pipeline
import tts_pipeline


DEFAULT_SCRIPT_PATH = Path(__file__).resolve().parent / "input" / "manual_script.json"
DEFAULT_TTS_RATE = "+20%"
TELEGRAM_SOFT_LIMIT_BYTES = 48 * 1024 * 1024


def parse_args():
    parser = argparse.ArgumentParser(
        description="Render a fixed relationship short through the production QC/video stack."
    )
    parser.add_argument(
        "--script-file",
        default=str(DEFAULT_SCRIPT_PATH),
        help="Path to the manual script JSON fixture.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run script QC and write metadata without TTS/rendering.",
    )
    return parser.parse_args()


def _write_upload_copy(selected):
    youtube_description = str(selected.get("youtube_description") or "").strip()
    tiktok_caption = str(selected.get("tiktok_caption") or "").strip()

    if not youtube_description:
        raise ValueError("selected.youtube_description is required")
    if not tiktok_caption:
        raise ValueError("selected.tiktok_caption is required")

    youtube_path = main.OUTPUT_DIR / "youtube_description.txt"
    tiktok_path = main.OUTPUT_DIR / "tiktok_caption.txt"
    youtube_path.write_text(youtube_description + "\n", encoding="utf-8")
    tiktok_path.write_text(tiktok_caption + "\n", encoding="utf-8")
    return youtube_path, tiktok_path


async def _create_voiceover(selected, audio_path, timing_path, rate):
    print(f"Creating English voice-over at {rate} speaking rate with real word timings.")
    return await tts_pipeline.create_audio_with_word_timings(
        selected["voiceover"],
        audio_path,
        timing_path,
        voice=main.TTS_VOICE,
        rate=rate,
    )


def _audio_duration(audio_path):
    from moviepy.editor import AudioFileClip

    audio_clip = AudioFileClip(str(audio_path))
    try:
        return audio_clip.duration
    finally:
        audio_clip.close()


def _rate_percent(rate):
    value = str(rate or "0%").strip()
    if value.endswith("%"):
        value = value[:-1]
    try:
        return int(value)
    except ValueError:
        return 0


def _adaptive_retry_rate(current_rate, actual_duration, target_duration):
    current_factor = max(0.5, 1.0 + (_rate_percent(current_rate) / 100.0))
    target_factor = current_factor * (float(actual_duration) / max(float(target_duration), 1.0))
    target_percent = round((target_factor - 1.0) * 100)
    target_percent = max(-20, min(40, target_percent))
    return f"{target_percent:+d}%"


def _telegram_config():
    token = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "Telegram delivery requires TELEGRAM_TOKEN and TELEGRAM_CHAT_ID."
        )
    return token, chat_id


def _telegram_post(method, *, data=None, files=None, timeout=180):
    token, chat_id = _telegram_config()
    payload = {"chat_id": chat_id}
    if data:
        payload.update(data)
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=payload,
        files=files,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body}")
    return body


def _prepare_telegram_video(video_path, duration):
    if video_path.stat().st_size <= TELEGRAM_SOFT_LIMIT_BYTES:
        return video_path

    delivery_path = main.OUTPUT_DIR / "telegram_video.mp4"
    safe_duration = max(float(duration), 1.0)
    target_total_bps = int((45 * 1024 * 1024 * 8) / safe_duration)
    audio_bps = 128_000
    video_bps = max(1_600_000, min(4_200_000, target_total_bps - audio_bps))

    print(
        f"Video is {video_path.stat().st_size / 1024 / 1024:.1f} MB; "
        "creating a Telegram-friendly delivery copy."
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            str(video_bps),
            "-maxrate",
            str(video_bps),
            "-bufsize",
            str(video_bps * 2),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(delivery_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if delivery_path.stat().st_size > TELEGRAM_SOFT_LIMIT_BYTES:
        print("Telegram delivery copy is still large; creating a 720x1280 fallback.")
        fallback_path = main.OUTPUT_DIR / "telegram_video_720p.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vf",
                "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                "2400k",
                "-maxrate",
                "2400k",
                "-bufsize",
                "4800k",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(fallback_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return fallback_path

    return delivery_path


def _send_telegram_package(selected, video_path, thumbnail_youtube, thumbnail_tiktok, duration):
    title = str(selected.get("title") or "Relationship story").strip()
    youtube_description = str(selected.get("youtube_description") or "").strip()
    tiktok_caption = str(selected.get("tiktok_caption") or "").strip()

    copy_message = (
        f"DAILY SHORT READY\n\n"
        f"TITLE\n{title}\n\n"
        f"YOUTUBE DESCRIPTION\n{youtube_description}\n\n"
        f"TIKTOK CAPTION\n{tiktok_caption}"
    )
    _telegram_post("sendMessage", data={"text": copy_message}, timeout=60)

    delivery_video = _prepare_telegram_video(video_path, duration)
    with delivery_video.open("rb") as handle:
        _telegram_post(
            "sendVideo",
            data={
                "caption": f"VIDEO — {title}",
                "supports_streaming": "true",
            },
            files={"video": (delivery_video.name, handle, "video/mp4")},
            timeout=600,
        )

    with Path(thumbnail_youtube).open("rb") as handle:
        _telegram_post(
            "sendDocument",
            data={"caption": "YOUTUBE THUMBNAIL — 1280x720"},
            files={"document": (Path(thumbnail_youtube).name, handle, "image/jpeg")},
            timeout=120,
        )

    with Path(thumbnail_tiktok).open("rb") as handle:
        _telegram_post(
            "sendDocument",
            data={"caption": "TIKTOK COVER — 1080x1920"},
            files={"document": (Path(thumbnail_tiktok).name, handle, "image/jpeg")},
            timeout=120,
        )

    print("Telegram delivery complete: copy, video, YouTube thumbnail, and TikTok cover sent.")
    return delivery_video


def run(args):
    script_path = Path(args.script_file)
    if not script_path.exists():
        print(f"Manual script not found: {script_path}")
        return 2

    package = json.loads(script_path.read_text(encoding="utf-8"))
    selected = package["selected"]
    selected_source = package.get("source", {})
    editorial_score = float(package.get("editorial_score", 0))

    background_queries = [
        str(query).strip()
        for query in selected.get("background_queries", [])
        if str(query).strip()
    ]
    if not background_queries:
        print(
            "Manual script is missing selected.background_queries. "
            "Daily script generation must provide the visual search plan."
        )
        return 2

    if not selected.get("thumbnail_text"):
        print(
            "Manual script is missing selected.thumbnail_text. "
            "Daily script generation must provide the thumbnail hook."
        )
        return 2

    if not str(selected.get("youtube_description") or "").strip():
        print("Manual script is missing selected.youtube_description.")
        return 2

    if not str(selected.get("tiktok_caption") or "").strip():
        print("Manual script is missing selected.tiktok_caption.")
        return 2

    main.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    candidate_scores = {
        "mode": "manual_test_script",
        "candidates": [
            {
                "id": selected.get("source_id", "manual-test"),
                "overall_score": editorial_score,
                "decision": "produce",
                "reason": selected.get(
                    "editorial_reason",
                    "Manual end-to-end test script.",
                ),
            }
        ],
        "selected": selected,
    }
    main.write_json(main.OUTPUT_DIR / "candidate_scores.json", candidate_scores)

    qc_report = main.build_qc_report(selected, editorial_score)
    main.write_json(main.OUTPUT_DIR / "qc_report.json", qc_report)
    if not qc_report["auto_pass"]:
        print(
            "Manual script failed automatic QC before rendering. "
            "Review output/qc_report.json."
        )
        return 1

    try:
        youtube_description_path, tiktok_caption_path = _write_upload_copy(selected)
    except ValueError as exc:
        print(f"Upload copy validation failed: {exc}")
        return 2

    metadata = {
        "generated_at": main.utc_now_iso(),
        "mode": "manual_test_script",
        "platforms": ["TikTok", "YouTube Shorts"],
        "script_file": str(script_path),
        "selected_source": selected_source,
        "selected": selected,
        "editorial_score": editorial_score,
        "estimated_duration_seconds": qc_report["estimated_duration_seconds"],
        "background_queries": background_queries,
        "thumbnail_text": selected.get("thumbnail_text"),
        "thumbnail_subtext": selected.get("thumbnail_subtext", ""),
        "youtube_description": selected.get("youtube_description"),
        "tiktok_caption": selected.get("tiktok_caption"),
        "youtube_description_path": str(youtube_description_path),
        "tiktok_caption_path": str(tiktok_caption_path),
        "status": "script_ready" if args.dry_run else "video_pending",
    }
    main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)

    print(
        f"Manual script QC passed: {main.word_count(selected['voiceover'])} words, "
        f"~{qc_report['estimated_duration_seconds']:.1f}s estimated."
    )
    print("Background queries:")
    for query in background_queries:
        print(f"  - {query}")

    if args.dry_run:
        print("Manual dry run complete.")
        return 0

    audio_path = main.OUTPUT_DIR / "voiceover.mp3"
    timing_path = main.OUTPUT_DIR / "subtitle_timings.json"
    video_path = main.OUTPUT_DIR / "final_video.mp4"
    background_dir = main.OUTPUT_DIR / "background_clips"

    requested_rate = os.environ.get("TTS_RATE") or DEFAULT_TTS_RATE
    word_boundaries = asyncio.run(
        _create_voiceover(selected, audio_path, timing_path, requested_rate)
    )
    audio_duration = _audio_duration(audio_path)
    final_rate = requested_rate

    for retry_number in range(1, 3):
        if main.TARGET_DURATION_LOW <= audio_duration <= main.TARGET_DURATION_HIGH:
            break

        if audio_duration < main.TARGET_DURATION_LOW:
            target_duration = main.TARGET_DURATION_LOW + 1.5
        else:
            target_duration = main.TARGET_DURATION_HIGH - 2.0

        retry_rate = _adaptive_retry_rate(
            final_rate,
            audio_duration,
            target_duration,
        )
        print(
            f"Voice-over is {audio_duration:.1f}s, outside target. "
            f"Adaptive retry {retry_number}/2 at {retry_rate} "
            f"for ~{target_duration:.1f}s."
        )
        word_boundaries = asyncio.run(
            _create_voiceover(selected, audio_path, timing_path, retry_rate)
        )
        audio_duration = _audio_duration(audio_path)
        final_rate = retry_rate

    metadata["tts_rate"] = final_rate
    metadata["word_boundary_count"] = len(word_boundaries)
    metadata["subtitle_timings_path"] = str(timing_path)

    qc_report = main.build_qc_report(
        selected,
        editorial_score,
        actual_duration=audio_duration,
    )
    main.write_json(main.OUTPUT_DIR / "qc_report.json", qc_report)
    if not qc_report["auto_pass"]:
        print(
            f"Manual voice-over duration/QC failed at {audio_duration:.1f}s. "
            "Review output/qc_report.json."
        )
        metadata["actual_duration_seconds"] = round(audio_duration, 2)
        metadata["status"] = "failed_qc"
        main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)
        return 1

    print("Building stock-video pool from script-provided queries.")
    clip_paths, background_manifest = media_pipeline.download_background_pool(
        background_queries,
        background_dir,
    )
    main.write_json(
        main.OUTPUT_DIR / "background_manifest.json",
        {
            "queries": background_queries,
            "clips": background_manifest,
        },
    )

    media_pipeline.SUBTITLE_LEAD_SECONDS = -0.06

    print("Rendering final vertical video with sequential background clips.")
    final_duration = media_pipeline.render_video(
        selected["voiceover"],
        audio_path,
        clip_paths,
        video_path,
        main.make_motion_background,
        word_boundaries=word_boundaries,
    )

    thumbnail_youtube, thumbnail_tiktok = media_pipeline.make_thumbnails(
        video_path,
        selected,
        main.OUTPUT_DIR,
    )

    metadata.update(
        {
            "status": "ready_for_telegram_delivery",
            "actual_duration_seconds": round(final_duration, 2),
            "background_clip_count": len(clip_paths),
            "background_manifest_path": str(
                main.OUTPUT_DIR / "background_manifest.json"
            ),
            "video_path": str(video_path),
            "thumbnail_youtube_path": str(thumbnail_youtube),
            "thumbnail_tiktok_path": str(thumbnail_tiktok),
            "metadata_path": str(main.OUTPUT_DIR / "metadata.json"),
            "qc_path": str(main.OUTPUT_DIR / "qc_report.json"),
        }
    )
    main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)

    main.append_video_log(
        {
            "date": main.utc_now_iso(),
            "platform": "manual_upload_pending",
            "category": "relationship",
            "title": selected.get("title", ""),
            "hook": selected.get("hook", ""),
            "duration_seconds": round(final_duration, 2),
            "editorial_score": editorial_score,
            "video_published": "no",
            "revenue_real_eur": "0",
            "source_url": selected_source.get("source_url", ""),
            "video_path": str(video_path),
            "metadata_path": str(main.OUTPUT_DIR / "metadata.json"),
        }
    )

    try:
        telegram_video = _send_telegram_package(
            selected,
            video_path,
            thumbnail_youtube,
            thumbnail_tiktok,
            final_duration,
        )
    except Exception as exc:
        metadata["status"] = "telegram_delivery_failed"
        metadata["telegram_error"] = str(exc)
        main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)
        print(f"Telegram delivery failed: {exc}")
        return 1

    metadata["status"] = "delivered_to_telegram"
    metadata["telegram_video_path"] = str(telegram_video)
    main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)

    print(f"Ready for manual upload: {video_path}")
    print(f"YouTube thumbnail: {thumbnail_youtube}")
    print(f"TikTok cover: {thumbnail_tiktok}")
    print(f"YouTube description: {youtube_description_path}")
    print(f"TikTok caption: {tiktok_caption_path}")
    print(f"Subtitle timings: {timing_path}")
    print("Daily package delivered to Telegram for phone upload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
