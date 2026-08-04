import os
import imghdr
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

TELEGRAPH_UPLOAD_URL = "https://telegra.ph/upload"
SUPPORTED_TYPES = {"jpeg", "png"}  # imghdr reports jpg files as "jpeg"


def img2url(file_path: str) -> str:
    """Upload an image file to telegra.ph and return its direct URL.

    Raises:
        ValueError: if the file is missing or not a supported image type.
        RuntimeError: if the upload fails or telegra.ph returns an unexpected response.
    """
    if not os.path.isfile(file_path):
        raise ValueError("File does not exist.")

    img_type = imghdr.what(file_path)
    if img_type not in SUPPORTED_TYPES:
        raise ValueError("Unsupported image type. Only jpg, jpeg, png are allowed.")

    with open(file_path, "rb") as f:
        resp = requests.post(
            TELEGRAPH_UPLOAD_URL,
            files={"file": (f"tmp.{img_type}", f, f"image/{img_type}")},
        )

    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError("Unexpected response from telegra.ph.")

    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])
    if not isinstance(result, list) or not result or "src" not in result[0]:
        raise RuntimeError("Unexpected response from telegra.ph.")

    return "https://telegra.ph" + result[0]["src"]


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
