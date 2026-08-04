import os
import re
import time
import mimetypes
import humanize
import aiohttp
import aiofiles
from urllib.parse import urlparse, unquote

from pyrogram import Client, filters
from pyrogram.types import Message

from info import ADMINS
from utils import temp
from database.users_chats_db import db

DOWNLOAD_DIR = "./downloads"
THUMB_DIR = "./thumbnails"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

THUMB_SETTING_KEY = "global_url_thumb"

# Telegram bot API upload limit. Override with MAX_UPLOAD_SIZE_MB env var if you run
# a local Bot API server (which supports up to 4000 MB instead of the default 2000 MB).
MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "2000"))

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}

PROGRESS_EDIT_INTERVAL = 5  # seconds between progress message edits, to avoid FloodWait


def humanbytes(size):
    if not size:
        return "0 B"
    return humanize.naturalsize(size, binary=True)


def get_filename_from_headers(url, headers):
    cd = headers.get("Content-Disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', cd)
    if match:
        return unquote(match.group(1).strip())

    path = urlparse(url).path
    name = os.path.basename(path)
    if name:
        return unquote(name)

    ext = mimetypes.guess_extension(headers.get("Content-Type", "").split(";")[0].strip()) or ""
    return f"file_{int(time.time())}{ext}"


async def download_thumb_locally(client, file_id):
    """Downloads the stored thumbnail file_id to a fresh local jpg for use in this upload."""
    path = os.path.join(THUMB_DIR, f"thumb_{int(time.time())}.jpg")
    try:
        result = await client.download_media(file_id, file_name=path)
        return result
    except Exception:
        return None


@Client.on_message(filters.command("setthumb") & filters.user(ADMINS))
async def set_thumb(client, message: Message):
    reply = message.reply_to_message
    if not reply or not reply.photo:
        return await message.reply_text(
            "Reply to a photo with /setthumb to set it as the bot's global upload thumbnail."
        )
    file_id = reply.photo.file_id
    await db.update_bot_setting(temp.ME, THUMB_SETTING_KEY, file_id)
    await message.reply_text("✅ Global thumbnail updated. It'll be used for all future /upload uploads.")


@Client.on_message(filters.command("viewthumb") & filters.user(ADMINS))
async def view_thumb(client, message: Message):
    file_id = await db.get_bot_setting(temp.ME, THUMB_SETTING_KEY, None)
    if not file_id:
        return await message.reply_text("No global thumbnail set. Use /setthumb (reply to a photo) to set one.")
    await client.send_photo(message.chat.id, file_id, caption="Current global upload thumbnail.")


@Client.on_message(filters.command("delthumb") & filters.user(ADMINS))
async def del_thumb(client, message: Message):
    await db.update_bot_setting(temp.ME, THUMB_SETTING_KEY, None)
    await message.reply_text("🗑 Global thumbnail cleared. Uploads will use auto-generated thumbnails (for videos) or none.")


@Client.on_message(filters.command("upload") & filters.user(ADMINS))
async def url_upload(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: <code>/upload &lt;direct url&gt;</code>\n\n"
            "Reply with a caption via <code>/upload &lt;url&gt; | your caption</code> (optional)."
        )

    raw_args = message.text.split(None, 1)[1]
    if "|" in raw_args:
        url, caption = (part.strip() for part in raw_args.split("|", 1))
    else:
        url, caption = raw_args.strip(), None

    if not re.match(r"^https?://", url, re.IGNORECASE):
        return await message.reply_text("That doesn't look like a valid http(s) URL.")

    status = await message.reply_text("🔗 Resolving link...")

    file_path = None
    local_thumb = None
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return await status.edit_text(f"❌ Server returned HTTP {resp.status} for this link.")

                total_size = int(resp.headers.get("Content-Length", 0))
                if total_size and total_size > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                    return await status.edit_text(
                        f"❌ File is {humanbytes(total_size)}, which exceeds the "
                        f"{MAX_UPLOAD_SIZE_MB} MB upload limit."
                    )

                filename = get_filename_from_headers(str(resp.url), resp.headers)
                file_path = os.path.join(DOWNLOAD_DIR, f"{int(time.time())}_{filename}")

                downloaded = 0
                last_update = time.time()
                await status.edit_text(f"⬇️ Downloading <b>{filename}</b>...")

                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_update >= PROGRESS_EDIT_INTERVAL:
                            last_update = now
                            pct = f"{downloaded * 100 / total_size:.1f}%" if total_size else humanbytes(downloaded)
                            try:
                                await status.edit_text(f"⬇️ Downloading <b>{filename}</b>\n{pct}")
                            except Exception:
                                pass

        actual_size = os.path.getsize(file_path)
        if actual_size > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            return await status.edit_text(
                f"❌ Downloaded file is {humanbytes(actual_size)}, which exceeds the "
                f"{MAX_UPLOAD_SIZE_MB} MB upload limit."
            )

        thumb_file_id = await db.get_bot_setting(temp.ME, THUMB_SETTING_KEY, None)
        if thumb_file_id:
            local_thumb = await download_thumb_locally(client, thumb_file_id)

        await status.edit_text(f"⬆️ Uploading <b>{filename}</b>...")

        last_update = time.time()

        async def progress(current, total):
            nonlocal last_update
            now = time.time()
            if now - last_update >= PROGRESS_EDIT_INTERVAL:
                last_update = now
                pct = f"{current * 100 / total:.1f}%" if total else humanbytes(current)
                try:
                    await status.edit_text(f"⬆️ Uploading <b>{filename}</b>\n{pct}")
                except Exception:
                    pass

        ext = os.path.splitext(filename)[1].lower()
        send_kwargs = dict(
            chat_id=message.chat.id,
            caption=caption or filename,
            thumb=local_thumb,
            progress=progress,
            reply_to_message_id=message.id,
        )

        if ext in VIDEO_EXTS:
            await client.send_video(video=file_path, **send_kwargs)
        else:
            await client.send_document(document=file_path, **send_kwargs)

        await status.delete()

    except aiohttp.ClientError as e:
        await status.edit_text(f"❌ Download failed: {e}")
    except Exception as e:
        await status.edit_text(f"❌ Error: {e}")
    finally:
        for p in (file_path, local_thumb):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
