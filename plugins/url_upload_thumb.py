"""
url_upload_thumb.py
--------------------
A self-contained Pyrogram plugin adding two features to a personal
Telegram bot:

  1. /upload <url>   -> downloads a file from a direct link and
                         re-uploads it to the chat, with a live
                         progress message.
  2. /setthumb        -> reply to a photo with this command to save
                         it as your personal custom thumbnail.
     /delthumb         -> remove your saved thumbnail.

If the uploaded file is a video and the user has no custom thumbnail
saved, one is automatically extracted from the video with ffmpeg.

Requirements:
    pip install pyrogram tgcrypto aiohttp

You also need ffmpeg installed on the system (for video thumbnails):
    sudo apt install ffmpeg

Wire this into your bot by importing the module (Pyrogram auto-loads
decorated handlers) or by copying the plugin into your existing
bot's plugins folder.
"""

import os
import time
import asyncio
import subprocess
from urllib.parse import urlparse, unquote

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

# ---------------------------------------------------------------------
# Config / simple persistence
# ---------------------------------------------------------------------

DOWNLOAD_DIR = "downloads"
THUMB_DIR = "thumbnails"          # one file per user: thumbnails/<user_id>.jpg
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

# Throttle how often we edit the progress message (Telegram rate-limits edits)
PROGRESS_EDIT_INTERVAL = 4  # seconds


def user_thumb_path(user_id: int) -> str:
    return os.path.join(THUMB_DIR, f"{user_id}.jpg")


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


# ---------------------------------------------------------------------
# /setthumb and /delthumb
# ---------------------------------------------------------------------

@Client.on_message(filters.command("setthumb") & filters.reply)
async def set_thumb(client: Client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply_text("Reply to a photo with /setthumb to save it as your thumbnail.")
        return

    path = user_thumb_path(message.from_user.id)
    await client.download_media(message.reply_to_message, file_name=path)
    await message.reply_text("✅ Custom thumbnail saved. It'll be used on your future video uploads.")


@Client.on_message(filters.command("delthumb"))
async def del_thumb(client: Client, message: Message):
    path = user_thumb_path(message.from_user.id)
    if os.path.exists(path):
        os.remove(path)
        await message.reply_text("🗑️ Thumbnail removed.")
    else:
        await message.reply_text("You don't have a saved thumbnail.")


# ---------------------------------------------------------------------
# /upload <url>
# ---------------------------------------------------------------------

@Client.on_message(filters.command("upload"))
async def upload_from_url(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/upload <direct_url>`", quote=True)
        return

    url = message.command[1]
    status = await message.reply_text("⏳ Starting download...")

    try:
        file_path = await download_file(url, status)
    except Exception as e:
        await status.edit_text(f"❌ Download failed: {e}")
        return

    ext = os.path.splitext(file_path)[1].lower()
    is_video = ext in VIDEO_EXTS

    thumb_path = user_thumb_path(message.from_user.id)
    thumb_to_use = None
    generated_thumb = None

    if is_video:
        if os.path.exists(thumb_path):
            thumb_to_use = thumb_path
        else:
            generated_thumb = extract_video_thumbnail(file_path)
            thumb_to_use = generated_thumb

    await status.edit_text("⬆️ Uploading to Telegram...")

    try:
        last_update = {"t": 0.0}

        async def progress(current, total):
            now = time.time()
            if now - last_update["t"] < PROGRESS_EDIT_INTERVAL and current != total:
                return
            last_update["t"] = now
            pct = current * 100 / total if total else 0
            try:
                await status.edit_text(
                    f"⬆️ Uploading... {pct:.1f}% ({human_size(current)}/{human_size(total)})"
                )
            except Exception:
                pass  # ignore FLOOD_WAIT/edit races

        if is_video:
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_to_use,
                caption=os.path.basename(file_path),
                progress=progress,
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=file_path,
                thumb=thumb_to_use if thumb_to_use else None,
                caption=os.path.basename(file_path),
                progress=progress,
            )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Upload failed: {e}")
    finally:
        # cleanup temp files
        for p in (file_path, generated_thumb):
            if p and os.path.exists(p):
                os.remove(p)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

async def download_file(url: str, status: Message) -> str:
    """Stream-download a URL to disk with progress updates, return local path."""
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path)) or f"file_{int(time.time())}"
    dest = os.path.join(DOWNLOAD_DIR, filename)

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_update = time.time()

            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 256):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_update >= PROGRESS_EDIT_INTERVAL:
                        last_update = now
                        pct = downloaded * 100 / total if total else 0
                        try:
                            await status.edit_text(
                                f"⏳ Downloading... {pct:.1f}% "
                                f"({human_size(downloaded)}/{human_size(total) if total else '?'})"
                            )
                        except Exception:
                            pass
    return dest


def extract_video_thumbnail(video_path: str) -> str:
    """Grab a frame ~1s in via ffmpeg as a fallback thumbnail."""
    out_path = video_path + "_thumb.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", "00:00:01",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return out_path if os.path.exists(out_path) else None
