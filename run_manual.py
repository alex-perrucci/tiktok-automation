import argparse
import asyncio
import json
import os
from pathlib import Path

import main
import media_pipeline
import tts_pipeline


DEFAULT_SCRIPT_PATH = Path(__file__).resolve().parent / "input" / "manual_script.json"
DEFAULT_TTS_RATE = "+20%"


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

    # Keep narration energetic while adapting once if the real audio misses QC range.
    if audio_duration < main.TARGET_DURATION_LOW:
        retry_rate = "+14%"
        print(
            f"Voice-over is {audio_duration:.1f}s, below target. "
            f"Retrying once at {retry_rate}."
        )
        word_boundaries = asyncio.run(
            _create_voiceover(selected, audio_path, timing_path, retry_rate)
        )
        audio_duration = _audio_duration(audio_path)
        final_rate = retry_rate
    elif audio_duration > main.TARGET_DURATION_HIGH:
        retry_rate = "+28%"
        print(
            f"Voice-over is {audio_duration:.1f}s, above target. "
            f"Retrying once at {retry_rate}."
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
            "status": "ready_for_manual_upload",
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

    print(f"Ready for manual upload: {video_path}")
    print(f"YouTube thumbnail: {thumbnail_youtube}")
    print(f"TikTok cover: {thumbnail_tiktok}")
    print(f"YouTube description: {youtube_description_path}")
    print(f"TikTok caption: {tiktok_caption_path}")
    print(f"Subtitle timings: {timing_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
