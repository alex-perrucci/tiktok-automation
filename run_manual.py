import argparse
import asyncio
import json
from pathlib import Path

import main


DEFAULT_SCRIPT_PATH = Path(__file__).resolve().parent / "input" / "manual_script.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Render a fixed relationship short through the production QC/video stack.")
    parser.add_argument("--script-file", default=str(DEFAULT_SCRIPT_PATH), help="Path to the manual script JSON fixture.")
    parser.add_argument("--dry-run", action="store_true", help="Run script QC and write metadata without TTS/rendering.")
    return parser.parse_args()


def run(args):
    script_path = Path(args.script_file)
    if not script_path.exists():
        print(f"Manual script not found: {script_path}")
        return 2

    package = json.loads(script_path.read_text(encoding="utf-8"))
    selected = package["selected"]
    selected_source = package.get("source", {})
    editorial_score = float(package.get("editorial_score", 0))

    main.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    candidate_scores = {
        "mode": "manual_test_script",
        "candidates": [
            {
                "id": selected.get("source_id", "manual-test"),
                "overall_score": editorial_score,
                "decision": "produce",
                "reason": selected.get("editorial_reason", "Manual end-to-end test script."),
            }
        ],
        "selected": selected,
    }
    main.write_json(main.OUTPUT_DIR / "candidate_scores.json", candidate_scores)

    qc_report = main.build_qc_report(selected, editorial_score)
    main.write_json(main.OUTPUT_DIR / "qc_report.json", qc_report)
    if not qc_report["auto_pass"]:
        print("Manual script failed automatic QC before rendering. Review output/qc_report.json.")
        return 1

    metadata = {
        "generated_at": main.utc_now_iso(),
        "mode": "manual_test_script",
        "platforms": ["TikTok", "YouTube Shorts"],
        "script_file": str(script_path),
        "selected_source": selected_source,
        "selected": selected,
        "editorial_score": editorial_score,
        "estimated_duration_seconds": qc_report["estimated_duration_seconds"],
        "status": "script_ready" if args.dry_run else "video_pending",
    }
    main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)

    print(
        f"Manual script QC passed: {main.word_count(selected['voiceover'])} words, "
        f"~{qc_report['estimated_duration_seconds']:.1f}s estimated."
    )

    if args.dry_run:
        print("Manual dry run complete.")
        return 0

    audio_path = main.OUTPUT_DIR / "voiceover.mp3"
    bg_path = main.OUTPUT_DIR / "background.mp4"
    video_path = main.OUTPUT_DIR / "final_video.mp4"

    print("Creating English voice-over from manual script.")
    asyncio.run(main.create_audio(selected["voiceover"], audio_path))

    from moviepy.editor import AudioFileClip

    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration
    audio_clip.close()

    qc_report = main.build_qc_report(selected, editorial_score, actual_duration=audio_duration)
    main.write_json(main.OUTPUT_DIR / "qc_report.json", qc_report)
    if not qc_report["auto_pass"]:
        print(f"Manual voice-over duration/QC failed at {audio_duration:.1f}s. Review output/qc_report.json.")
        metadata["actual_duration_seconds"] = round(audio_duration, 2)
        metadata["status"] = "failed_qc"
        main.write_json(main.OUTPUT_DIR / "metadata.json", metadata)
        return 1

    downloaded_bg = main.download_pexels_video(
        selected.get("search_term", "couple relationship argument"),
        bg_path,
    )

    print("Rendering final vertical video from manual script.")
    final_duration = main.make_video(selected["voiceover"], audio_path, downloaded_bg, video_path)

    metadata.update(
        {
            "status": "ready_for_manual_upload",
            "actual_duration_seconds": round(final_duration, 2),
            "video_path": str(video_path),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
