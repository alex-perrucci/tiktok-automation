import argparse
import datetime as dt
import json
import math
import mimetypes
import os
import subprocess
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_PATH = BASE_DIR / "input" / "manual_script.json"
STATE_PATH = BASE_DIR / "platform_upload_state.json"
VIDEO_PATH = OUTPUT_DIR / "final_video.mp4"
YOUTUBE_THUMBNAIL_PATH = OUTPUT_DIR / "thumbnail_youtube.jpg"

YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_DRAFT_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"

FIVE_MB = 5 * 1024 * 1024
MAX_TIKTOK_CHUNK = 64 * 1024 * 1024
DEFAULT_TIKTOK_CHUNK = 32 * 1024 * 1024


def utc_now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def require_output_files():
    if not SCRIPT_PATH.exists():
        raise RuntimeError(f"Missing script package: {SCRIPT_PATH}")
    if not VIDEO_PATH.exists() or VIDEO_PATH.stat().st_size == 0:
        raise RuntimeError(f"Missing rendered video: {VIDEO_PATH}")


def story_package():
    package = load_json(SCRIPT_PATH)
    selected = package.get("selected") or {}
    source = package.get("source") or {}
    story_id = str(selected.get("source_id") or source.get("id") or "").strip()
    if not story_id:
        raise RuntimeError("input/manual_script.json is missing a stable story id")
    return package, selected, story_id


def load_state():
    state = load_json(STATE_PATH, {"stories": {}})
    state.setdefault("stories", {})
    return state


def save_platform_state(state, story_id, platform, payload):
    story_state = state["stories"].setdefault(story_id, {})
    story_state[platform] = {
        **payload,
        "updated_at": utc_now_iso(),
    }
    write_json(STATE_PATH, state)


def env_group(*names):
    return {name: (os.environ.get(name) or "").strip() for name in names}


def configured(values):
    return all(values.values())


def refresh_youtube_access_token():
    creds = env_group(
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    )
    if not configured(creds):
        return None

    response = requests.post(
        YOUTUBE_TOKEN_URL,
        data={
            "client_id": creds["YOUTUBE_CLIENT_ID"],
            "client_secret": creds["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": creds["YOUTUBE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"YouTube token refresh did not return access_token: {data}")
    return access_token


def upload_youtube(selected, story_id, state):
    existing = state["stories"].get(story_id, {}).get("youtube")
    if existing and existing.get("video_id"):
        print(f"YouTube already uploaded for {story_id}: {existing['video_id']}. Skipping duplicate.")
        return existing

    access_token = refresh_youtube_access_token()
    if not access_token:
        print("YouTube upload skipped: OAuth secrets are not configured yet.")
        return None

    title = str(selected.get("title") or "Relationship story").strip()[:100]
    description = str(selected.get("youtube_description") or selected.get("caption") or "").strip()
    if not description:
        raise RuntimeError("YouTube description is missing from manual_script.json")

    video_size = VIDEO_PATH.stat().st_size
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(video_size),
    }
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    init = requests.post(
        YOUTUBE_UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers=headers,
        json=body,
        timeout=45,
    )
    init.raise_for_status()
    upload_url = init.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube resumable upload did not return a Location header")

    with VIDEO_PATH.open("rb") as handle:
        upload = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(video_size),
            },
            data=handle,
            timeout=900,
        )
    upload.raise_for_status()
    video = upload.json()
    video_id = video.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload completed without a video id: {video}")

    privacy_status = ((video.get("status") or {}).get("privacyStatus") or "unknown").lower()

    if YOUTUBE_THUMBNAIL_PATH.exists():
        thumb_type = mimetypes.guess_type(YOUTUBE_THUMBNAIL_PATH.name)[0] or "image/jpeg"
        with YOUTUBE_THUMBNAIL_PATH.open("rb") as handle:
            thumb = requests.post(
                YOUTUBE_THUMBNAIL_URL,
                params={"videoId": video_id, "uploadType": "media"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": thumb_type,
                    "Content-Length": str(YOUTUBE_THUMBNAIL_PATH.stat().st_size),
                },
                data=handle,
                timeout=120,
            )
        thumb.raise_for_status()
    else:
        print("YouTube thumbnail missing; video was uploaded without a custom thumbnail.")

    result = {
        "video_id": video_id,
        "privacy_status": privacy_status,
        "requested_privacy_status": "public",
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }
    save_platform_state(state, story_id, "youtube", result)
    print(f"YouTube upload complete: {result['url']} (privacy={privacy_status})")

    if privacy_status != "public":
        raise RuntimeError(
            "YouTube accepted the upload but did not make it public. "
            "A non-audited API project can be forced to private by YouTube."
        )
    return result


def refresh_tiktok_access_token():
    creds = env_group(
        "TIKTOK_CLIENT_KEY",
        "TIKTOK_CLIENT_SECRET",
        "TIKTOK_REFRESH_TOKEN",
    )
    if not configured(creds):
        return None, None

    response = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": creds["TIKTOK_CLIENT_KEY"],
            "client_secret": creds["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": creds["TIKTOK_REFRESH_TOKEN"],
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")
    if not access_token or not new_refresh_token:
        raise RuntimeError(f"TikTok token refresh returned an incomplete response: {data}")
    return access_token, new_refresh_token


def persist_rotated_tiktok_refresh_token(old_token, new_token):
    if not new_token or new_token == old_token:
        return

    admin_token = (os.environ.get("GH_SECRETS_TOKEN") or "").strip()
    repository = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not admin_token or not repository:
        raise RuntimeError(
            "TikTok rotated the refresh token, but GH_SECRETS_TOKEN is not configured. "
            "A GitHub token able to update Actions secrets is required for unattended daily refreshes."
        )

    env = os.environ.copy()
    env["GH_TOKEN"] = admin_token
    subprocess.run(
        [
            "gh",
            "secret",
            "set",
            "TIKTOK_REFRESH_TOKEN",
            "--repo",
            repository,
            "--body",
            new_token,
        ],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    print("TikTok rotated its refresh token; GitHub secret was updated for the next run.")


def tiktok_chunk_plan(video_size):
    if video_size <= MAX_TIKTOK_CHUNK:
        return video_size, 1

    chunk_size = DEFAULT_TIKTOK_CHUNK
    chunk_count = max(1, math.floor(video_size / chunk_size))
    final_size = video_size - (chunk_size * (chunk_count - 1))
    while final_size > 128 * 1024 * 1024:
        chunk_size = min(MAX_TIKTOK_CHUNK, chunk_size + FIVE_MB)
        chunk_count = max(1, math.floor(video_size / chunk_size))
        final_size = video_size - (chunk_size * (chunk_count - 1))
    return chunk_size, chunk_count


def upload_tiktok_draft(selected, story_id, state):
    existing = state["stories"].get(story_id, {}).get("tiktok")
    if existing and existing.get("publish_id"):
        print(f"TikTok draft already uploaded for {story_id}: {existing['publish_id']}. Skipping duplicate.")
        return existing

    old_refresh_token = (os.environ.get("TIKTOK_REFRESH_TOKEN") or "").strip()
    access_token, new_refresh_token = refresh_tiktok_access_token()
    if not access_token:
        print("TikTok draft upload skipped: OAuth secrets are not configured yet.")
        return None

    video_size = VIDEO_PATH.stat().st_size
    chunk_size, total_chunk_count = tiktok_chunk_plan(video_size)

    init = requests.post(
        TIKTOK_DRAFT_INIT_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            }
        },
        timeout=45,
    )
    init.raise_for_status()
    init_data = init.json()
    error = init_data.get("error") or {}
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(f"TikTok draft initialization failed: {init_data}")

    data = init_data.get("data") or {}
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url or not publish_id:
        raise RuntimeError(f"TikTok draft initialization returned incomplete data: {init_data}")

    with VIDEO_PATH.open("rb") as handle:
        start = 0
        for index in range(total_chunk_count):
            if index == total_chunk_count - 1:
                length = video_size - start
            else:
                length = chunk_size
            payload = handle.read(length)
            if len(payload) != length:
                raise RuntimeError("Unexpected EOF while reading TikTok upload chunk")
            end = start + length - 1
            response = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(length),
                    "Content-Range": f"bytes {start}-{end}/{video_size}",
                },
                data=payload,
                timeout=300,
            )
            expected = 201 if index == total_chunk_count - 1 else 206
            if response.status_code != expected:
                raise RuntimeError(
                    f"TikTok chunk {index + 1}/{total_chunk_count} returned "
                    f"HTTP {response.status_code}, expected {expected}: {response.text[:500]}"
                )
            start = end + 1

    result = {
        "publish_id": publish_id,
        "mode": "draft",
        "caption_for_manual_paste": str(selected.get("tiktok_caption") or "").strip(),
    }
    save_platform_state(state, story_id, "tiktok", result)
    persist_rotated_tiktok_refresh_token(old_refresh_token, new_refresh_token)
    print(f"TikTok draft upload complete: {publish_id}")
    return result


def parse_args():
    parser = argparse.ArgumentParser(description="Upload a rendered short to zero-cost platform APIs.")
    parser.add_argument("--youtube", action="store_true", help="Publish to YouTube when configured.")
    parser.add_argument("--tiktok", action="store_true", help="Upload a TikTok draft when configured.")
    return parser.parse_args()


def main():
    args = parse_args()
    use_youtube = args.youtube or not (args.youtube or args.tiktok)
    use_tiktok = args.tiktok or not (args.youtube or args.tiktok)

    require_output_files()
    _, selected, story_id = story_package()
    state = load_state()

    failures = []
    if use_youtube:
        try:
            upload_youtube(selected, story_id, state)
        except Exception as exc:
            failures.append(f"YouTube: {exc}")
            print(f"YouTube upload error: {exc}")

    if use_tiktok:
        try:
            upload_tiktok_draft(selected, story_id, state)
        except Exception as exc:
            failures.append(f"TikTok: {exc}")
            print(f"TikTok draft upload error: {exc}")

    if failures:
        raise SystemExit(" | ".join(failures))

    print("Platform upload step completed.")


if __name__ == "__main__":
    main()
