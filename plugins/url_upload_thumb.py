"""
url_upload_thumb.py
--------------------
A self-contained Pyrogram plugin adding these features to a personal
Telegram bot:

  1. /upload <url>   -> downloads a file from a direct link, then
                         shows Rename / Upload buttons before sending.
  2. /setthumb        -> reply to a photo with this command to save
                         it as your personal custom thumbnail.
     /delthumb         -> remove your saved thumbnail.
  3. After a file is uploaded, a "Send to DB Channel" button appears
     that copies it straight to your configured database channel.

If the uploaded file is a video and the user has no custom thumbnail
saved, one is automatically extracted from the video with ffmpeg.

Requirements:
    pip install pyrogram tgcrypto aiohttp certifi

You also need ffmpeg installed on the system (for video thumbnails):
    sudo apt install ffmpeg

Config:
    Set DB_CHANNEL_ID to your database channel's chat id (e.g. -1001234567890).
    Your bot account must already be an admin/member of that channel.

Wire this into your bot by importing the module (Pyrogram auto-loads
decorated handlers) or by copying the plugin into your existing
bot's plugins folder.
"""

import os
import time
import asyncio
import subprocess
from urllib.parse import urlparse, unquote

import ssl
import certifi
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

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

# Your database/storage channel. Use the numeric chat id (starts with -100 for channels).
DB_CHANNEL_ID = int(os.environ.get("DB_CHANNEL_ID", "0"))

# In-memory state. For a multi-worker/production deployment, swap these
# for a real store (Redis, a DB table, etc.) since a plain dict only
# works within a single running process.
pending_files = {}   # user_id -> {"path": str, "is_video": bool}
awaiting_rename = {} # user_id -> True while we're waiting for their new filename


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
# /upload <url>  -> download, then ask Rename / Upload as-is
# ---------------------------------------------------------------------

@Client.on_message(filters.command("upload"))
async def upload_from_url(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/upload <direct_url>`", quote=True)
        return

    url = message.command[1]
    user_id = message.from_user.id
    status = await message.reply_text("⏳ Starting download...")

    try:
        file_path = await download_file(url, status)
    except Exception as e:
        await status.edit_text(f"❌ Download failed: {e}")
        return

    ext = os.path.splitext(file_path)[1].lower()
    is_video = ext in VIDEO_EXTS
    pending_files[user_id] = {"path": file_path, "is_video": is_video}

    name = os.path.basename(file_path)
    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Rename", callback_data="rename_pending"),
                InlineKeyboardButton("🚀 Upload as-is", callback_data="upload_pending"),
            ]
        ]
    )
    await status.edit_text(f"✅ Downloaded: `{name}`\n\nRename before uploading, or send as-is?", reply_markup=buttons)


@Client.on_callback_query(filters.regex("^rename_pending$"))
async def ask_rename(client: Client, cq: CallbackQuery):
    user_id = cq.from_user.id
    if user_id not in pending_files:
        await cq.answer("Nothing pending — start with /upload <url>.", show_alert=True)
        return
    awaiting_rename[user_id] = True
    await cq.answer()
    await cq.message.edit_text("✍️ Send me the new filename (with extension), e.g. `MyFile.mp4`.")


@Client.on_message(filters.text & filters.private & filters.create(lambda _, __, m: m.from_user and awaiting_rename.get(m.from_user.id)))
async def capture_rename(client: Client, message: Message):
    user_id = message.from_user.id
    awaiting_rename.pop(user_id, None)
    entry = pending_files.get(user_id)
    if not entry:
        await message.reply_text("Nothing pending — start with /upload <url>.")
        return

    new_name = message.text.strip()
    old_path = entry["path"]
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    os.rename(old_path, new_path)
    entry["path"] = new_path
    entry["is_video"] = os.path.splitext(new_path)[1].lower() in VIDEO_EXTS

    status = await message.reply_text(f"✅ Renamed to `{new_name}`. Uploading...")
    await do_upload(client, message.chat.id, user_id, status)


@Client.on_callback_query(filters.regex("^upload_pending$"))
async def confirm_upload(client: Client, cq: CallbackQuery):
    user_id = cq.from_user.id
    if user_id not in pending_files:
        await cq.answer("Nothing pending — start with /upload <url>.", show_alert=True)
        return
    await cq.answer()
    await cq.message.edit_text("⬆️ Uploading to Telegram...")
    await do_upload(client, cq.message.chat.id, user_id, cq.message)


async def do_upload(client: Client, chat_id: int, user_id: int, status: Message):
    """Uploads the pending file for user_id into chat_id, then offers a
    'Send to DB Channel' button on the resulting message."""
    entry = pending_files.pop(user_id, None)
    if not entry:
        return
    file_path = entry["path"]
    is_video = entry["is_video"]

    thumb_path = user_thumb_path(user_id)
    thumb_to_use = None
    generated_thumb = None
    if is_video:
        if os.path.exists(thumb_path):
            thumb_to_use = thumb_path
        else:
            generated_thumb = extract_video_thumbnail(file_path)
            thumb_to_use = generated_thumb

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

        send_kwargs = dict(
            chat_id=chat_id,
            caption=os.path.basename(file_path),
            progress=progress,
        )
        if is_video:
            sent = await client.send_video(video=file_path, thumb=thumb_to_use, **send_kwargs)
        else:
            sent = await client.send_document(document=file_path, thumb=thumb_to_use, **send_kwargs)

        try:
            await status.delete()
        except Exception:
            pass

        if DB_CHANNEL_ID:
            channel_btn = InlineKeyboardMarkup(
                [[InlineKeyboardButton("↪️ Forward to Channel", callback_data=f"tochannel:{sent.id}")]]
            )
            await client.edit_message_reply_markup(chat_id, sent.id, reply_markup=channel_btn)

    except Exception as e:
        await status.edit_text(f"❌ Upload failed: {e}")
    finally:
        for p in (file_path, generated_thumb):
            if p and os.path.exists(p):
                os.remove(p)


@Client.on_callback_query(filters.regex(r"^tochannel:(\d+)$"))
async def send_to_channel(client: Client, cq: CallbackQuery):
    if not DB_CHANNEL_ID:
        await cq.answer("DB_CHANNEL_ID isn't configured on the bot.", show_alert=True)
        return

    msg_id = int(cq.data.split(":")[1])
    try:
        await client.forward_messages(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=cq.message.chat.id,
            message_ids=msg_id,
        )
        await cq.answer("✅ Forwarded to DB channel.")
    except Exception as e:
        await cq.answer(f"❌ Failed: {e}", show_alert=True)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

async def download_file(url: str, status: Message) -> str:
    """Stream-download a URL to disk with progress updates, return local path."""
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path)) or f"file_{int(time.time())}"
    dest = os.path.join(DOWNLOAD_DIR, filename)

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
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
