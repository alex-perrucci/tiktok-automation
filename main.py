import argparse
import asyncio
import csv
import datetime as dt
import html
import json
import math
import os
import random
import re
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
LOG_FILE = BASE_DIR / "processed_links.txt"
VIDEO_LOG = BASE_DIR / "videos_log.csv"
BG_MUSIC_PATH = BASE_DIR / "bg_music.mp3"
AVATAR_OPEN_PATH = BASE_DIR / "avatar_aperto.png"
AVATAR_CLOSED_PATH = BASE_DIR / "avatar_chiuso.png"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3-flash-preview"
PEXELS_KEY = os.environ.get("PEXELS_API_KEY") or None
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or None
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or None
USE_BG_MUSIC = (os.environ.get("USE_BG_MUSIC") or "false").lower() in {"1", "true", "yes"}

MIN_EDITORIAL_SCORE = float(os.environ.get("MIN_EDITORIAL_SCORE", "7.0"))
MIN_DURATION_SECONDS = int(os.environ.get("MIN_DURATION_SECONDS", "60"))
TARGET_DURATION_LOW = int(os.environ.get("TARGET_DURATION_LOW", "65"))
TARGET_DURATION_HIGH = int(os.environ.get("TARGET_DURATION_HIGH", "90"))
ESTIMATED_WPM = int(os.environ.get("ESTIMATED_WPM", "165"))
TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-JennyNeural")
TTS_RATE = os.environ.get("TTS_RATE", "+6%")

RELATIONSHIP_FEEDS = [
    {
        "category": "relationship_advice",
        "url": "https://www.reddit.com/r/relationship_advice/top/.rss?t=week",
    },
    {
        "category": "relationships",
        "url": "https://www.reddit.com/r/relationships/top/.rss?t=week",
    },
    {
        "category": "AmIOverreacting",
        "url": "https://www.reddit.com/r/AmIOverreacting/top/.rss?t=week",
    },
    {
        "category": "AITAH",
        "url": "https://www.reddit.com/r/AITAH/top/.rss?t=week",
    },
    {
        "category": "TrueOffMyChest",
        "url": "https://www.reddit.com/r/TrueOffMyChest/top/.rss?t=week",
    },
]

SCORING_KEYS = [
    "hook_potential",
    "curiosity",
    "conflict",
    "escalation",
    "emotional_response",
    "payoff_twist",
    "comment_potential",
    "sixty_second_capacity",
]

BLOCKED_TERMS = [
    "underage",
    "minor",
    "15f",
    "16f",
    "17f",
    "15m",
    "16m",
    "17m",
    "sexual assault",
    "rape",
    "self harm",
    "self-harm",
    "suicide",
    "murder",
    "domestic violence",
    "revenge porn",
    "nudes",
    "graphic",
]

CSV_FIELDS = [
    "date",
    "platform",
    "category",
    "title",
    "hook",
    "duration_seconds",
    "editorial_score",
    "video_published",
    "views",
    "likes",
    "comments",
    "average_retention",
    "completion_rate",
    "followers_generated",
    "revenue_real_eur",
    "source_url",
    "video_path",
    "metadata_path",
]


def utc_now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_processed_links():
    if not LOG_FILE.exists():
        return set()
    return set(line.strip() for line in LOG_FILE.read_text(encoding="utf-8").splitlines() if line.strip())


def save_processed_links(links):
    links = [link for link in links if link]
    if not links:
        return
    processed = load_processed_links()
    new_links = [link for link in links if link not in processed]
    if not new_links:
        return
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        for link in new_links:
            handle.write(link + "\n")


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def trim_text(value, max_chars=900):
    value = clean_text(value)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rsplit(" ", 1)[0] + "..."


def word_count(text):
    return len(re.findall(r"\b[\w']+\b", text or ""))


def estimate_duration_seconds(text):
    return round((word_count(text) / max(1, ESTIMATED_WPM)) * 60, 1)


def clean_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return match.group(0).strip() if match else text


def require_dependencies(*module_names):
    missing = []
    for module_name in module_names:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(module_name)
    if missing:
        print("Missing Python packages: " + ", ".join(missing))
        print("Install them with: pip install -r requirements.txt")
        raise SystemExit(2)


def looks_unsafe(title, summary):
    haystack = f"{title} {summary}".lower()
    return any(term in haystack for term in BLOCKED_TERMS)


def fetch_candidates(max_candidates):
    require_dependencies("feedparser")
    import feedparser

    processed = load_processed_links()
    candidates = []
    seen = set()
    feeds = list(RELATIONSHIP_FEEDS)
    random.shuffle(feeds)

    for source in feeds:
        print(f"Checking source radar: {source['category']}")
        feed = feedparser.parse(
            source["url"],
            request_headers={
                "User-Agent": "tiktok-automation-editorial-radar/1.0",
            },
        )

        for entry in feed.entries[:12]:
            link = getattr(entry, "link", "") or getattr(entry, "id", "")
            title = clean_text(getattr(entry, "title", ""))
            summary = trim_text(getattr(entry, "summary", ""))

            if not link or link in processed or link in seen:
                continue
            seen.add(link)

            if looks_unsafe(title, summary):
                print(f"Skipping unsafe candidate: {title[:90]}")
                save_processed_links([link])
                continue

            candidates.append(
                {
                    "id": f"c{len(candidates) + 1}",
                    "category": source["category"],
                    "source_url": link,
                    "source_title": title,
                    "source_summary": summary,
                }
            )

            if len(candidates) >= max_candidates:
                return candidates

    return candidates


def get_gemini_client():
    require_dependencies("google.genai")
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Missing GEMINI_API_KEY. Add it as an environment variable or GitHub secret.")
        raise SystemExit(2)
    return genai.Client(api_key=api_key)


def generate_editorial_package(client, candidates, min_score):
    compact_candidates = [
        {
            "id": candidate["id"],
            "category": candidate["category"],
            "source_title": candidate["source_title"],
            "source_summary": candidate["source_summary"],
        }
        for candidate in candidates
    ]

    prompt = f"""
You are the editorial filter for an English short-form relationship-drama channel.

The source material below is only a radar for conflict patterns. Do not copy wording,
names, comments, or a Reddit-style setup. Create a substantially transformed, original
story that uses the strongest dramatic pattern you find.

Score every candidate from 1 to 10 on:
{", ".join(SCORING_KEYS)}

Select only one candidate if its overall_score is at least {min_score}. If none are strong
enough, return selected as null. Be strict: a technically usable story is not enough.

For the selected story, write a voiceover that naturally supports {TARGET_DURATION_LOW}
to {TARGET_DURATION_HIGH} seconds. Aim for 190 to 230 words. It must be relationship
drama, in English, conversational, tense, and specific. Start immediately with a hook.
Do not say "I found this on Reddit", "today I am telling you", or anything similar.

Structure:
- 0-2 seconds: hook with immediate curiosity.
- 2-15 seconds: only essential setup.
- 15-45 seconds: escalation with new information.
- 45+ seconds: payoff, reveal, consequence, or decision.
- End with a natural comment question only if it fits the story.

Avoid explicit sexual detail, minors, self-harm, graphic violence, real-person allegations,
protected-class stereotypes, generic moralizing, filler, and engagement bait.

Return only valid JSON in this shape:
{{
  "candidates": [
    {{
      "id": "c1",
      "overall_score": 7.4,
      "scores": {{
        "hook_potential": 8,
        "curiosity": 8,
        "conflict": 8,
        "escalation": 7,
        "emotional_response": 8,
        "payoff_twist": 7,
        "comment_potential": 8,
        "sixty_second_capacity": 7
      }},
      "decision": "produce",
      "reason": "short reason"
    }}
  ],
  "selected": {{
    "source_id": "c1",
    "category": "relationship",
    "title": "Short upload title under 80 characters",
    "hook": "The exact first sentence of the voiceover.",
    "voiceover": "Full voiceover text.",
    "caption": "Manual upload caption with no more than 6 hashtags.",
    "hashtags": ["#relationshipdrama", "#storytime"],
    "search_term": "couple argument apartment",
    "editorial_reason": "why this was selected",
    "originality_note": "how the final story is transformed from the source pattern"
  }}
}}

Candidates:
{json.dumps(compact_candidates, ensure_ascii=True, indent=2)}
"""

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return json.loads(clean_json(response.text))


def selected_candidate(editorial_package, candidates):
    selected = editorial_package.get("selected")
    if not selected:
        return None
    source_id = selected.get("source_id")
    return next((candidate for candidate in candidates if candidate["id"] == source_id), None)


def get_selected_score(editorial_package, source_id):
    for candidate in editorial_package.get("candidates", []):
        if candidate.get("id") == source_id:
            return float(candidate.get("overall_score") or 0)
    return 0.0


def revise_script_for_duration(client, selected, current_duration):
    direction = "expand" if current_duration < MIN_DURATION_SECONDS else "tighten"
    target_words = "210 to 235" if direction == "expand" else "185 to 210"
    prompt = f"""
Revise this English relationship-drama short voiceover. Keep the same premise, hook,
payoff, caption, and metadata, but {direction} the story so it lands naturally in
{TARGET_DURATION_LOW}-{TARGET_DURATION_HIGH} seconds. Target {target_words} words.

Do not add filler, repeated phrasing, generic moral lessons, or fake cliffhanger language.
Return only the same selected JSON object with updated hook, voiceover, caption, hashtags,
search_term, editorial_reason, and originality_note.

Current selected JSON:
{json.dumps(selected, ensure_ascii=True, indent=2)}
"""
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return json.loads(clean_json(response.text))


def build_qc_report(selected, editorial_score, actual_duration=None):
    voiceover = selected.get("voiceover", "")
    hook = selected.get("hook", "")
    lower_voiceover = voiceover.lower()
    estimated_duration = estimate_duration_seconds(voiceover)
    duration_value = actual_duration if actual_duration is not None else estimated_duration

    checks = [
        {
            "name": "editorial_score_at_least_threshold",
            "pass": editorial_score >= MIN_EDITORIAL_SCORE,
            "value": editorial_score,
        },
        {
            "name": "duration_at_least_60_seconds",
            "pass": duration_value >= MIN_DURATION_SECONDS,
            "value": round(duration_value, 2),
        },
        {
            "name": "target_duration_range",
            "pass": TARGET_DURATION_LOW <= duration_value <= TARGET_DURATION_HIGH,
            "value": round(duration_value, 2),
        },
        {
            "name": "hook_starts_without_intro",
            "pass": bool(hook)
            and not hook.lower().startswith(
                (
                    "i found",
                    "today",
                    "let me tell",
                    "this story",
                    "so this",
                    "welcome",
                )
            ),
            "value": hook,
        },
        {
            "name": "relationship_drama_category",
            "pass": any(
                term in lower_voiceover
                for term in [
                    "boyfriend",
                    "girlfriend",
                    "husband",
                    "wife",
                    "fiance",
                    "partner",
                    "ex",
                    "dating",
                    "relationship",
                ]
            ),
            "value": selected.get("category", "relationship"),
        },
        {
            "name": "not_reddit_framed",
            "pass": "reddit" not in lower_voiceover,
            "value": "reddit" not in lower_voiceover,
        },
        {
            "name": "word_count_supports_one_minute",
            "pass": word_count(voiceover) >= 165,
            "value": word_count(voiceover),
        },
    ]

    manual_checks = [
        "Watch the rendered video once before upload.",
        "Confirm the hook is understandable without extra context.",
        "Confirm no section feels flat or repetitive.",
        "Confirm subtitles feel acceptably synced to the voice-over.",
        "Confirm the final story is transformative and does not reuse source wording.",
        "Confirm the upload caption fits the platform account tone.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "auto_pass": all(check["pass"] for check in checks),
        "estimated_duration_seconds": estimated_duration,
        "actual_duration_seconds": round(actual_duration, 2) if actual_duration is not None else None,
        "checks": checks,
        "manual_checks_required": manual_checks,
    }


async def create_audio(text, audio_path):
    require_dependencies("edge_tts")
    import edge_tts

    communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
    await communicate.save(str(audio_path))


def download_pexels_video(query, output_path):
    if not PEXELS_KEY:
        print("No PEXELS_API_KEY found. Using local animated background fallback.")
        return None

    require_dependencies("requests")
    import requests

    print(f"Searching Pexels background: {query}")
    response = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 3},
        timeout=45,
    )
    if response.status_code != 200:
        print(f"Pexels request failed with status {response.status_code}. Using fallback.")
        return None

    videos = response.json().get("videos", [])
    if not videos:
        print("No Pexels result found. Using fallback.")
        return None

    files = videos[0].get("video_files", [])
    portrait_files = [item for item in files if item.get("height", 0) >= item.get("width", 0)]
    selected_file = next(
        (item for item in portrait_files if item.get("quality") in {"hd", "sd"}),
        portrait_files[0] if portrait_files else files[0],
    )

    video_bytes = requests.get(selected_file["link"], timeout=120).content
    output_path.write_bytes(video_bytes)
    return output_path


def load_font(size):
    from PIL import ImageFont

    candidates = [
        os.environ.get("SUBTITLE_FONT_PATH"),
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def render_text_image(text, fill):
    from PIL import Image, ImageDraw

    width, height = 1000, 230
    text = text.upper()
    font_size = 88
    stroke_width = 5

    while font_size >= 46:
        font = load_font(font_size)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke_width, spacing=8)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= width - 60 and text_height <= height - 20:
            x = (width - text_width) / 2
            y = (height - text_height) / 2 - 8
            draw.multiline_text(
                (x, y),
                text,
                font=font,
                fill=fill,
                align="center",
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 230),
                spacing=8,
            )
            return image
        font_size -= 4

    return image


def chunk_words(text):
    words = re.findall(r"\S+", text)
    chunks = []
    current = []
    current_len = 0

    for word in words:
        clean_word = re.sub(r"[,.;:!?]+$", "", word)
        if current and current_len + len(clean_word) > 23:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(clean_word)
        current_len += len(clean_word) + 1

    if current:
        chunks.append(" ".join(current))
    return chunks


def make_motion_background(duration):
    require_dependencies("moviepy", "numpy")
    import numpy as np
    from moviepy.editor import VideoClip

    small_w, small_h = 270, 480
    x = np.linspace(0, 1, small_w)[None, :]
    y = np.linspace(0, 1, small_h)[:, None]

    def frame(t):
        drift = 0.18 * math.sin(t * 0.35)
        pulse = 0.12 * math.sin(t * 0.9)
        red = 22 + 35 * x + 28 * np.sin((y + drift) * math.pi)
        green = 24 + 34 * y + 18 * np.cos((x + pulse) * math.pi * 1.5)
        blue = 42 + 46 * (1 - y) + 24 * np.sin((x + y + drift) * math.pi)
        image = np.dstack([red, green, blue])
        return np.clip(image, 0, 255).astype("uint8")

    return VideoClip(frame, duration=duration).resize((1080, 1920))


def fit_vertical(clip):
    target_ratio = 9 / 16
    current_ratio = clip.w / clip.h
    if current_ratio > target_ratio:
        new_width = int(clip.h * target_ratio)
        clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=new_width, height=clip.h)
    else:
        new_height = int(clip.w / target_ratio)
        clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=clip.w, height=new_height)
    return clip.resize((1080, 1920))


def load_avatar_frame(path, width=720):
    require_dependencies("PIL", "numpy")
    import numpy as np
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    ratio = width / image.width
    resized = image.resize((width, int(image.height * ratio)), Image.LANCZOS)
    data = np.array(resized)
    rgb = data[:, :, :3]
    alpha = data[:, :, 3] / 255.0
    return rgb, alpha


def make_avatar_clip(duration, voice_audio):
    require_dependencies("moviepy", "numpy")
    import numpy as np
    from moviepy.editor import VideoClip

    if not AVATAR_OPEN_PATH.exists() or not AVATAR_CLOSED_PATH.exists():
        return None

    open_rgb, open_alpha = load_avatar_frame(AVATAR_OPEN_PATH)
    closed_rgb, closed_alpha = load_avatar_frame(AVATAR_CLOSED_PATH)

    def is_talking(t):
        try:
            sample_t = min(max(t, 0), max(voice_audio.duration - 0.02, 0))
            sample = voice_audio.get_frame(sample_t)
            return float(np.abs(sample).mean()) > 0.025
        except Exception:
            return False

    def face_frame(t):
        return open_rgb if is_talking(t) else closed_rgb

    def mask_frame(t):
        return open_alpha if is_talking(t) else closed_alpha

    avatar = VideoClip(face_frame, duration=duration)
    avatar_mask = VideoClip(mask_frame, ismask=True, duration=duration)
    avatar = avatar.set_mask(avatar_mask)

    def position(t):
        base_y = 1040 + 10 * math.sin(t * 2.2)
        return ("center", base_y - 10 if is_talking(t) else base_y)

    return avatar.set_position(position)


def make_subtitle_clips(text, duration):
    require_dependencies("moviepy", "numpy", "PIL")
    import numpy as np
    from moviepy.editor import ImageClip

    chunks = chunk_words(text)
    if not chunks:
        return []

    total_chars = sum(max(1, len(chunk)) for chunk in chunks)
    colors = [(255, 255, 255, 255), (255, 230, 109, 255), (122, 231, 255, 255)]
    clips = []
    current_time = 0

    for index, chunk in enumerate(chunks):
        chunk_duration = duration * (max(1, len(chunk)) / total_chars)
        image = render_text_image(chunk, colors[index % len(colors)])
        clip = (
            ImageClip(np.array(image))
            .set_position(("center", 730))
            .set_start(current_time)
            .set_duration(chunk_duration)
            .crossfadein(0.04)
        )
        clips.append(clip)
        current_time += chunk_duration

    return clips


def make_video(full_text, audio_path, bg_path, output_path):
    require_dependencies("moviepy", "numpy", "PIL")
    from moviepy.audio.fx.all import audio_loop, volumex
    from moviepy.editor import (
        AudioFileClip,
        ColorClip,
        CompositeAudioClip,
        CompositeVideoClip,
        VideoFileClip,
    )

    voice_audio = AudioFileClip(str(audio_path))
    duration = voice_audio.duration

    if bg_path and Path(bg_path).exists():
        print(f"Using background video: {bg_path}")
        background = VideoFileClip(str(bg_path)).without_audio().speedx(1.12)
        if background.duration < duration:
            background = background.loop(duration=duration)
        else:
            background = background.subclip(0, duration)
        background = fit_vertical(background)
    else:
        print("Using local animated background.")
        background = make_motion_background(duration)

    final_audio = voice_audio
    if USE_BG_MUSIC and BG_MUSIC_PATH.exists():
        try:
            bg_music = AudioFileClip(str(BG_MUSIC_PATH))
            bg_music = audio_loop(bg_music, duration=duration)
            bg_music = volumex(bg_music, 0.08)
            final_audio = CompositeAudioClip([voice_audio, bg_music])
        except Exception as exc:
            print(f"Background music skipped: {exc}")

    background = background.set_audio(final_audio)
    dark_overlay = ColorClip(size=(1080, 1920), color=(0, 0, 0)).set_opacity(0.22).set_duration(duration)
    subtitle_clips = make_subtitle_clips(full_text, duration)
    avatar_clip = make_avatar_clip(duration, voice_audio)

    layers = [background, dark_overlay]
    if avatar_clip is not None:
        layers.append(avatar_clip)
    layers.extend(subtitle_clips)

    final_clip = CompositeVideoClip(layers)
    final_clip.write_videofile(
        str(output_path),
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="6000k",
        preset="medium",
        threads=4,
        temp_audiofile=str(output_path.with_suffix(".temp-audio.m4a")),
        remove_temp=True,
    )

    final_clip.close()
    voice_audio.close()
    return duration


def send_telegram_video(video_path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram is not configured. Manual upload package stays in output/.")
        return

    require_dependencies("requests")
    import requests

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    with open(video_path, "rb") as video_file:
        response = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"video": video_file},
            timeout=120,
        )
    if response.status_code == 200:
        print("Video sent to Telegram.")
    else:
        print(f"Telegram upload failed: {response.text}")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def append_video_log(row):
    exists = VIDEO_LOG.exists()
    with VIDEO_LOG.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def run_self_test():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = {
        "source_id": "self-test",
        "category": "relationship",
        "title": "She Thought The Locked Drawer Was About Cheating",
        "hook": "My girlfriend found a locked drawer and packed a suitcase before I got home.",
        "voiceover": (
            "My girlfriend found a locked drawer and packed a suitcase before I got home. "
            "We had been together for three years, and the one thing she always hated was secrecy. "
            "So when she noticed a tiny key on my keychain that did not open the mailbox, she tested every drawer in the apartment. "
            "One of them would not move. She texted me one photo, then stopped answering. "
            "By the time I got back, half her clothes were folded on the bed, and she had already called her sister. "
            "I asked for ten minutes. She said I had two. "
            "Inside the drawer was not another phone, or letters, or anything romantic. "
            "It was a stack of receipts, a ring box, and a lease application for the tiny bakery space she had been dreaming about for years. "
            "I had been saving every bonus to help her open it. "
            "Then she saw the last envelope. It was from her sister, telling me not to propose until she checked whether I was serious. "
            "Now my girlfriend is furious at her sister, her sister says she was protecting her, and I am sitting here wondering if the surprise is even worth saving. "
            "Would you still propose after that?"
        ),
        "caption": "A locked drawer almost ended the relationship. #relationshipdrama #storytime #couples #trust",
        "hashtags": ["#relationshipdrama", "#storytime", "#couples", "#trust"],
        "search_term": "couple apartment argument",
        "editorial_reason": "Clear conflict, escalation, reveal, and a natural comment question.",
        "originality_note": "Synthetic self-test story; not sourced from a real post.",
    }
    report = build_qc_report(selected, editorial_score=8.2)
    write_json(OUTPUT_DIR / "self_test_qc_report.json", report)
    if not report["auto_pass"]:
        print("Self-test failed. Review output/self_test_qc_report.json.")
        return 1
    print("Self-test passed. QC helpers and local file output are working.")
    return 0


def run(args):
    if args.self_test:
        return run_self_test()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = fetch_candidates(args.max_candidates)
    if not candidates:
        print("No new relationship candidates found.")
        return 1

    client = get_gemini_client()
    editorial_package = generate_editorial_package(client, candidates, args.min_score)
    write_json(OUTPUT_DIR / "candidate_scores.json", editorial_package)

    selected_source = selected_candidate(editorial_package, candidates)
    if not selected_source:
        print("No candidate passed the editorial threshold. Nothing generated.")
        save_processed_links(candidate["source_url"] for candidate in candidates)
        return 0

    selected = editorial_package["selected"]
    editorial_score = get_selected_score(editorial_package, selected.get("source_id"))
    qc_report = build_qc_report(selected, editorial_score)
    write_json(OUTPUT_DIR / "qc_report.json", qc_report)

    if not qc_report["auto_pass"]:
        print("Selected story failed automatic QC before rendering. Review output/qc_report.json.")
        save_processed_links(candidate["source_url"] for candidate in candidates)
        return 1

    metadata = {
        "generated_at": utc_now_iso(),
        "mode": "manual_upload_initial",
        "platforms": ["TikTok", "YouTube Shorts"],
        "selected_source": selected_source,
        "selected": selected,
        "editorial_score": editorial_score,
        "estimated_duration_seconds": qc_report["estimated_duration_seconds"],
        "status": "script_ready" if args.dry_run else "video_pending",
    }
    write_json(OUTPUT_DIR / "metadata.json", metadata)

    if args.dry_run:
        print("Dry run complete. Script, metadata, scores, and QC report are in output/.")
        return 0

    audio_path = OUTPUT_DIR / "voiceover.mp3"
    bg_path = OUTPUT_DIR / "background.mp4"
    video_path = OUTPUT_DIR / "final_video.mp4"

    print("Creating English voice-over.")
    asyncio.run(create_audio(selected["voiceover"], audio_path))

    from moviepy.editor import AudioFileClip

    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration
    audio_clip.close()

    if audio_duration < MIN_DURATION_SECONDS or audio_duration > TARGET_DURATION_HIGH + 8:
        print(f"Voice-over duration is {audio_duration:.1f}s. Asking model for one timing revision.")
        selected = revise_script_for_duration(client, selected, audio_duration)
        asyncio.run(create_audio(selected["voiceover"], audio_path))
        audio_clip = AudioFileClip(str(audio_path))
        audio_duration = audio_clip.duration
        audio_clip.close()
        metadata["selected"] = selected
        metadata["estimated_duration_seconds"] = estimate_duration_seconds(selected["voiceover"])

    qc_report = build_qc_report(selected, editorial_score, actual_duration=audio_duration)
    write_json(OUTPUT_DIR / "qc_report.json", qc_report)
    if not qc_report["auto_pass"]:
        print("Story failed automatic QC after voice-over. Video was not rendered.")
        write_json(OUTPUT_DIR / "metadata.json", metadata)
        save_processed_links(candidate["source_url"] for candidate in candidates)
        return 1

    downloaded_bg = download_pexels_video(selected.get("search_term", "couple city night"), bg_path)
    print("Rendering final vertical video.")
    final_duration = make_video(selected["voiceover"], audio_path, downloaded_bg, video_path)

    metadata.update(
        {
            "status": "ready_for_manual_upload",
            "actual_duration_seconds": round(final_duration, 2),
            "video_path": str(video_path),
            "metadata_path": str(OUTPUT_DIR / "metadata.json"),
            "qc_path": str(OUTPUT_DIR / "qc_report.json"),
        }
    )
    write_json(OUTPUT_DIR / "metadata.json", metadata)

    append_video_log(
        {
            "date": utc_now_iso(),
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
            "metadata_path": str(OUTPUT_DIR / "metadata.json"),
        }
    )

    save_processed_links(candidate["source_url"] for candidate in candidates)
    send_telegram_video(video_path, caption=f"{selected.get('title', 'Relationship story')}\n\nReady for manual upload.")
    print(f"Ready for manual upload: {video_path}")
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Generate one QC-gated relationship short for manual upload.")
    parser.add_argument("--once", action="store_true", help="Generate one video package. This is the default behavior.")
    parser.add_argument("--dry-run", action="store_true", help="Create script, scoring, metadata, and QC without rendering video.")
    parser.add_argument("--self-test", action="store_true", help="Run local QC checks without network, AI, TTS, or video dependencies.")
    parser.add_argument("--max-candidates", type=int, default=10, help="Maximum source candidates to score in one model call.")
    parser.add_argument("--min-score", type=float, default=MIN_EDITORIAL_SCORE, help="Minimum editorial score required.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
