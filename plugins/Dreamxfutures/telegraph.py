import os
from os import getenv
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# Set IMGBB_API_KEY as an environment variable on your host (Heroku config vars, etc.)
# Get a free key at https://api.imgbb.com/
IMGBB_API_KEY = getenv("IMGBB_API_KEY", "d4cc3d793cb68b2c6cdc2197588e895c")
IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"


def img2url(file_path: str) -> str:
    """Upload an image file to ImgBB and return its direct URL.

    Raises:
        ValueError: if the file is missing.
        RuntimeError: if the upload fails or ImgBB returns an unexpected response.
    """
    if not os.path.isfile(file_path):
        raise ValueError("File does not exist.")

    with open(file_path, "rb") as f:
        resp = requests.post(
            IMGBB_UPLOAD_URL,
            data={"key": IMGBB_API_KEY},
            files={"image": f},
        )

    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Unexpected response from ImgBB (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    if resp.status_code == 200 and result.get("success"):
        return result["data"]["url"]

    error_msg = result.get("error", {}).get("message") if isinstance(result.get("error"), dict) else result.get("error")
    raise RuntimeError(error_msg or "Something went wrong. Please try again later.")


@Client.on_message(filters.command(["img", "cup", "telegraph"], prefixes="/") & filters.reply)
async def c_upload(client, message: Message):
    reply = message.reply_to_message
    if not reply.media:
        return await message.reply_text("Reply to a media to upload it to Cloud.")
    if reply.document and reply.document.file_size > 5 * 1024 * 1024:  # 5 MB
        return await message.reply_text("File size limit is 5 MB.")

    msg = await message.reply_text("Processing...")
    downloaded_media = None
    try:
        downloaded_media = await reply.download()
        if not downloaded_media:
            return await msg.edit_text("Something went wrong during download.")

        url = img2url(downloaded_media)
        await msg.edit_text(url)

    except ValueError as e:
        await msg.edit_text(str(e))
    except Exception as e:
        await msg.edit_text(f"Error: {str(e)}")
    finally:
        if downloaded_media and os.path.exists(downloaded_media):
            os.remove(downloaded_media)
