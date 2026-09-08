import math
import os
import random
from pathlib import Path

PEXELS_KEY = os.environ.get("PEXELS_API_KEY") or None
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY") or None

TARGET_SIZE = (1080, 1920)
SEGMENT_MIN_SECONDS = 5.0
SEGMENT_MAX_SECONDS = 8.0
MAX_DOWNLOADED_CLIPS = 8


def _fit_vertical(clip):
    target_ratio = 9 / 16
    current_ratio = clip.w / clip.h
    if current_ratio > target_ratio:
        new_width = int(clip.h * target_ratio)
        clip = clip.crop(
            x_center=clip.w / 2,
            y_center=clip.h / 2,
            width=new_width,
            height=clip.h,
        )
    else:
        new_height = int(clip.w / target_ratio)
        clip = clip.crop(
            x_center=clip.w / 2,
            y_center=clip.h / 2,
            width=clip.w,
            height=new_height,
        )
    return clip.resize(TARGET_SIZE)


def _search_pexels(query, per_page=4):
    if not PEXELS_KEY:
        return []

    import requests

    try:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={
                "query": query,
                "orientation": "portrait",
                "per_page": per_page,
            },
            timeout=45,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Pexels search failed for {query!r}: {exc}")
        return []

    results = []
    for video in response.json().get("videos", []):
        files = [
            item
            for item in video.get("video_files", [])
            if item.get("link") and item.get("height", 0) >= item.get("width", 0)
        ]
        if not files:
            continue

        files.sort(
            key=lambda item: (
                0 if 540 <= int(item.get("width") or 0) <= 1080 else 1,
                abs(int(item.get("width") or 0) - 720),
            )
        )
        selected = files[0]
        results.append(
            {
                "provider": "pexels",
                "query": query,
                "id": str(video.get("id") or selected["link"]),
                "url": selected["link"],
            }
        )
    return results


def _search_pixabay(query, per_page=4):
    if not PIXABAY_KEY:
        return []

    import requests

    try:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "per_page": per_page,
                "safesearch": "true",
            },
            timeout=45,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Pixabay search failed for {query!r}: {exc}")
        return []

    results = []
    for video in response.json().get("hits", []):
        variants = video.get("videos", {})
        selected = None
        for key in ("medium", "small", "large", "tiny"):
            candidate = variants.get(key) or {}
            if candidate.get("url"):
                selected = candidate
                break
        if not selected:
            continue

        results.append(
            {
                "provider": "pixabay",
                "query": query,
                "id": str(video.get("id") or selected["url"]),
                "url": selected["url"],
            }
        )
    return results


def collect_background_candidates(queries):
    queries = [str(query).strip() for query in queries if str(query).strip()]
    if not queries:
        queries = [
            "first person parkour city",
            "obstacle course running POV",
            "fast driving road POV",
        ]

    candidates = []
    seen_urls = set()

    for query in queries:
        provider_batches = [_search_pexels(query), _search_pixabay(query)]
        for index in range(max((len(batch) for batch in provider_batches), default=0)):
            for batch in provider_batches:
                if index >= len(batch):
                    continue
                item = batch[index]
                if item["url"] in seen_urls:
                    continue
                seen_urls.add(item["url"])
                candidates.append(item)

    return candidates


def download_background_pool(queries, output_dir, limit=MAX_DOWNLOADED_CLIPS):
    import requests

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_file in output_dir.glob("clip_*.mp4"):
        old_file.unlink(missing_ok=True)

    candidates = collect_background_candidates(queries)
    if not candidates:
        print("No stock-video candidates available. Falling back to local motion background.")
        return [], []

    downloaded = []
    manifest = []
    for item in candidates[: max(limit * 2, limit)]:
        if len(downloaded) >= limit:
            break

        path = output_dir / f"clip_{len(downloaded) + 1:02d}_{item['provider']}.mp4"
        try:
            with requests.get(item["url"], stream=True, timeout=120) as response:
                response.raise_for_status()
                with path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if path.stat().st_size < 50_000:
                raise ValueError("downloaded file is unexpectedly small")
        except Exception as exc:
            path.unlink(missing_ok=True)
            print(f"Skipping {item['provider']} clip for {item['query']!r}: {exc}")
            continue

        downloaded.append(path)
        manifest.append(
            {
                "provider": item["provider"],
                "query": item["query"],
                "path": str(path),
            }
        )
        print(f"Background clip {len(downloaded)}: {item['provider']} / {item['query']}")

    return downloaded, manifest


def build_background_sequence(paths, duration):
    from moviepy.editor import VideoFileClip, concatenate_videoclips

    source_clips = []
    usable = []
    for path in paths:
        try:
            clip = VideoFileClip(str(path)).without_audio()
            if clip.duration and clip.duration >= 1.0:
                source_clips.append(clip)
                usable.append(clip)
            else:
                clip.close()
        except Exception as exc:
            print(f"Could not open background clip {path}: {exc}")

    if not usable:
        for clip in source_clips:
            clip.close()
        return None, []

    segments = []
    elapsed = 0.0
    cursor = 0

    while elapsed < duration:
        source = usable[cursor % len(usable)]
        cursor += 1

        remaining = duration - elapsed
        wanted = min(random.uniform(SEGMENT_MIN_SECONDS, SEGMENT_MAX_SECONDS), remaining)

        if source.duration > wanted + 0.25:
            max_start = max(0.0, source.duration - wanted - 0.05)
            start = random.uniform(0.0, max_start) if max_start > 0 else 0.0
            segment = source.subclip(start, start + wanted)
        else:
            segment = source.loop(duration=wanted)

        segment = _fit_vertical(segment)
        segments.append(segment)
        elapsed += wanted

    sequence = concatenate_videoclips(segments, method="compose")
    if sequence.duration > duration:
        sequence = sequence.subclip(0, duration)

    return sequence, source_clips


def render_video(full_text, audio_path, clip_paths, output_path, fallback_factory):
    from moviepy.editor import AudioFileClip, ColorClip, CompositeVideoClip

    import main

    voice_audio = AudioFileClip(str(audio_path))
    duration = voice_audio.duration

    background, source_clips = build_background_sequence(clip_paths, duration)
    if background is None:
        print("Using local animated background fallback.")
        background = fallback_factory(duration)
    else:
        print(f"Using multi-clip background sequence with {len(clip_paths)} downloaded source clips.")

    background = background.set_audio(voice_audio)
    dark_overlay = (
        ColorClip(size=TARGET_SIZE, color=(0, 0, 0))
        .set_opacity(0.18)
        .set_duration(duration)
    )
    subtitle_clips = main.make_subtitle_clips(full_text, duration)

    # Intentionally no avatar/speaker layer: voice-over only.
    final_clip = CompositeVideoClip([background, dark_overlay, *subtitle_clips])
    final_clip.write_videofile(
        str(output_path),
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="6000k",
        preset="medium",
        threads=4,
        temp_audiofile=str(Path(output_path).with_suffix(".temp-audio.m4a")),
        remove_temp=True,
    )

    final_clip.close()
    background.close()
    voice_audio.close()
    for clip in source_clips:
        try:
            clip.close()
        except Exception:
            pass

    return duration


def _thumbnail_font(size):
    from PIL import ImageFont

    candidates = [
        os.environ.get("THUMBNAIL_FONT_PATH"),
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _cover_resize(image, size):
    from PIL import Image

    target_w, target_h = size
    ratio = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (int(image.width * ratio), int(image.height * ratio)),
        Image.LANCZOS,
    )
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _wrap_thumbnail_text(draw, text, font, max_width):
    words = text.upper().split()
    lines = []
    current = []
    for word in words:
        candidate = " ".join([*current, word])
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=4)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines[:4]


def _make_thumbnail_from_frame(frame, size, headline, subheadline, output_path):
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance

    image = Image.fromarray(np.asarray(frame).astype("uint8")).convert("RGB")
    image = _cover_resize(image, size)
    image = ImageEnhance.Brightness(image).enhance(0.55)

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        (0, int(size[1] * 0.48), size[0], size[1]),
        fill=(0, 0, 0, 105),
    )
    image = Image.alpha_composite(image.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(image)
    headline_size = max(54, int(size[0] * 0.075))
    subheadline_size = max(30, int(size[0] * 0.035))
    font = _thumbnail_font(headline_size)
    subfont = _thumbnail_font(subheadline_size)

    lines = _wrap_thumbnail_text(draw, headline, font, int(size[0] * 0.86))
    line_height = int(headline_size * 1.05)
    block_height = len(lines) * line_height
    y = int(size[1] * 0.58) - block_height // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=6)
        width = bbox[2] - bbox[0]
        x = (size[0] - width) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=6,
            stroke_fill=(0, 0, 0, 255),
        )
        y += line_height

    if subheadline:
        y += int(subheadline_size * 0.35)
        bbox = draw.textbbox((0, 0), subheadline.upper(), font=subfont, stroke_width=3)
        width = bbox[2] - bbox[0]
        x = (size[0] - width) // 2
        draw.text(
            (x, y),
            subheadline.upper(),
            font=subfont,
            fill=(255, 230, 109, 255),
            stroke_width=3,
            stroke_fill=(0, 0, 0, 255),
        )

    image.convert("RGB").save(output_path, quality=94, optimize=True)


def make_thumbnails(video_path, selected, output_dir):
    from moviepy.editor import VideoFileClip

    output_dir = Path(output_dir)
    youtube_path = output_dir / "thumbnail_youtube.jpg"
    tiktok_path = output_dir / "thumbnail_tiktok.jpg"

    headline = selected.get("thumbnail_text") or selected.get("title") or "RELATIONSHIP DRAMA"
    subheadline = selected.get("thumbnail_subtext") or ""

    clip = VideoFileClip(str(video_path))
    try:
        sample_time = min(max(1.0, clip.duration * 0.12), max(0.0, clip.duration - 0.1))
        frame = clip.get_frame(sample_time)
    finally:
        clip.close()

    _make_thumbnail_from_frame(frame, (1280, 720), headline, subheadline, youtube_path)
    _make_thumbnail_from_frame(frame, (1080, 1920), headline, subheadline, tiktok_path)
    print(f"Created YouTube thumbnail: {youtube_path}")
    print(f"Created TikTok cover: {tiktok_path}")
    return youtube_path, tiktok_path
