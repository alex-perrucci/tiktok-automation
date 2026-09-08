import json
import os
from pathlib import Path

TICKS_PER_SECOND = 10_000_000
DEFAULT_VOICE = os.environ.get("TTS_VOICE") or "en-US-JennyNeural"
DEFAULT_RATE = os.environ.get("TTS_RATE") or "+18%"
MAX_WORDS_PER_CUE = 3
MAX_CHARS_PER_CUE = 24


def _seconds(value):
    return float(value or 0) / TICKS_PER_SECOND


def build_subtitle_cues(word_events):
    cues = []
    current = []

    def flush():
        nonlocal current
        if not current:
            return
        cues.append(
            {
                "text": " ".join(item["text"] for item in current).strip(),
                "start": current[0]["start"],
                "end": current[-1]["end"],
            }
        )
        current = []

    for event in word_events:
        text = str(event.get("text") or "").strip()
        if not text:
            continue

        proposed = " ".join([*(item["text"] for item in current), text]).strip()
        if current and (
            len(current) >= MAX_WORDS_PER_CUE
            or len(proposed) > MAX_CHARS_PER_CUE
        ):
            flush()

        current.append(event)

        if text.endswith((".", "!", "?", ":", ";")):
            flush()

    flush()

    # Keep each cue visible through natural pauses, but end it just before the
    # next spoken cue starts. This avoids the old subtitle drift/lag.
    for index, cue in enumerate(cues[:-1]):
        next_start = cues[index + 1]["start"]
        cue["end"] = max(cue["end"], next_start - 0.03)

    return cues


async def create_audio_with_word_timings(
    text,
    audio_path,
    timing_path,
    voice=DEFAULT_VOICE,
    rate=DEFAULT_RATE,
):
    import edge_tts

    audio_path = Path(audio_path)
    timing_path = Path(timing_path)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    timing_path.parent.mkdir(parents=True, exist_ok=True)

    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        boundary="WordBoundary",
    )

    word_events = []
    with audio_path.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = _seconds(chunk.get("offset"))
                duration = _seconds(chunk.get("duration"))
                word_events.append(
                    {
                        "text": str(chunk.get("text") or "").strip(),
                        "start": round(start, 4),
                        "end": round(start + duration, 4),
                    }
                )

    if not word_events:
        raise RuntimeError("edge-tts returned audio without WordBoundary metadata")

    cues = build_subtitle_cues(word_events)
    timing_path.write_text(
        json.dumps(
            {
                "voice": voice,
                "rate": rate,
                "word_count": len(word_events),
                "cue_count": len(cues),
                "words": word_events,
                "cues": cues,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return cues
