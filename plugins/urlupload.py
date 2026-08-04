import os
import re
import ssl
import aiohttp
import certifi
import asyncio
import json
import time
from datetime import datetime
from urllib.parse import urlparse, urljoin, unquote, parse_qs
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import ADMINS, LOG_CHANNEL
from database.users_chats_db import db
from Script import script
import aiofiles
import humanize
import logging
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SSL Context for downloads
def get_ssl_context():
    """Create SSL context that handles certificate issues"""
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        return ssl_context
    except:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

async def download_file(url, file_path):
    """Download file with proper SSL handling"""
    ssl_context = get_ssl_context()
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=ssl_context, headers=headers, timeout=300) as response:
                if response.status != 200:
                    return False, f"HTTP Error: {response.status}"
                
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        if chunk:
                            await f.write(chunk)
                return True, "Download successful"
                
    except aiohttp.ClientSSLError:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=ssl_context, timeout=300) as response:
                    if response.status != 200:
                        return False, f"HTTP Error: {response.status}"
                    
                    async with aiofiles.open(file_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            if chunk:
                                await f.write(chunk)
                    return True, "Download successful"
        except Exception as e:
            return False, f"Download failed: {str(e)}"
    except Exception as e:
        return False, f"Download failed: {str(e)}"

def get_mediafire_direct_link_sync(url):
    """
    Get MediaFire direct download link using synchronous requests
    This is a proven working method
    """
    try:
        # Headers to mimic a real browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }
        
        # First request to get the page
        response = requests.get(url, headers=headers, timeout=30, verify=False, allow_redirects=True)
        
        if response.status_code != 200:
            logger.error(f"HTTP Error: {response.status_code}")
            return None, None
        
        html = response.text
        
        # Try to find the download link in the page
        download_url = None
        filename = None
        
        # METHOD 1: Look for the download link in a specific pattern
        # MediaFire often uses this pattern for the download link
        patterns = [
            r'kNO\s*=\s*"([^"]+)"',
            r'var kNO = "([^"]+)"',
            r'kNO="([^"]+)"',
            r"kNO='([^']+)'",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                download_url = match.group(1)
                # Clean up the URL
                download_url = download_url.replace('\\/', '/')
                download_url = download_url.replace('\/', '/')
                logger.info(f"Found download URL via kNO: {download_url[:50]}...")
                break
        
        # METHOD 2: Look for downloadButton
        if not download_url:
            btn_pattern = r'<a[^>]+id="downloadButton"[^>]+href="([^"]+)"'
            match = re.search(btn_pattern, html)
            if match:
                download_url = match.group(1)
                if download_url.startswith('/'):
                    download_url = 'https://www.mediafire.com' + download_url
                logger.info(f"Found download URL via downloadButton: {download_url[:50]}...")
        
        # METHOD 3: Look for any mediafire download link
        if not download_url:
            direct_pattern = r'(https?://download\d*\.mediafire\.com/[^"\'\s]+)'
            match = re.search(direct_pattern, html)
            if match:
                download_url = match.group(1)
                logger.info(f"Found direct download URL: {download_url[:50]}...")
        
        # METHOD 4: Look for the actual file URL in JavaScript
        if not download_url:
            # Look for something like: window.location = "https://download..."
            location_pattern = r'window\.location\s*=\s*"([^"]+)"'
            match = re.search(location_pattern, html)
            if match:
                download_url = match.group(1)
                logger.info(f"Found download URL via window.location: {download_url[:50]}...")
        
        # Extract filename
        # Try multiple methods to get filename
        title_match = re.search(r'<title>([^<]+)</title>', html)
        if title_match:
            filename = title_match.group(1)
            # Clean up the title
            filename = re.sub(r'(?:Download|MediaFire|File|from|-\s*)', '', filename)
            filename = filename.strip()
            logger.info(f"Extracted filename from title: {filename}")
        
        if not filename:
            og_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
            if og_match:
                filename = og_match.group(1)
                filename = re.sub(r'(?:Download|MediaFire|File|from|-\s*)', '', filename)
                filename = filename.strip()
                logger.info(f"Extracted filename from og:title: {filename}")
        
        if not filename:
            # Try to get from URL
            parsed_url = urlparse(url)
            path = parsed_url.path
            # Extract filename from path
            if path:
                path_parts = path.split('/')
                for part in path_parts:
                    if '.' in part and len(part) > 4:
                        filename = unquote(part)
                        logger.info(f"Extracted filename from URL: {filename}")
                        break
        
        # If we got a download URL but no filename, try to extract from download URL
        if download_url and not filename:
            parsed_dl = urlparse(download_url)
            if parsed_dl.path:
                path_parts = parsed_dl.path.split('/')
                if path_parts:
                    last_part = path_parts[-1]
                    if '.' in last_part:
                        filename = unquote(last_part)
                        logger.info(f"Extracted filename from download URL: {filename}")
        
        # If we still don't have a filename, check the download URL directly
        if download_url and not filename:
            # Try to get filename from Content-Disposition header
            try:
                head_response = requests.head(download_url, headers=headers, timeout=10, verify=False)
                if 'Content-Disposition' in head_response.headers:
                    cd = head_response.headers['Content-Disposition']
                    match = re.search(r'filename="?([^"]+)"?', cd)
                    if match:
                        filename = match.group(1)
                        logger.info(f"Extracted filename from Content-Disposition: {filename}")
            except:
                pass
        
        if not filename:
            filename = f"mediafire_{int(time.time())}.pdf"
        
        return download_url, filename
        
    except Exception as e:
        logger.error(f"Error in get_mediafire_direct_link_sync: {e}")
        return None, None

@Client.on_message(filters.private & filters.command("upload") & filters.user(ADMINS))
async def upload_file(client, message):
    """Handle /upload command for admins"""
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Please provide a URL\n\n"
            "**Example:** `/upload https://www.mediafire.com/file/xxx/`"
        )
        return
    
    url = message.command[1].strip()
    
    if "mediafire.com" in url:
        status_msg = await message.reply_text("📥 **Processing MediaFire link...**")
        
        # Get the download link using the synchronous method
        download_url, filename = get_mediafire_direct_link_sync(url)
        
        # Try with different URL formats if the first one failed
        if not download_url:
            # Try to extract file ID
            file_id_match = re.search(r'/([a-zA-Z0-9]{10,})', url)
            if file_id_match:
                file_id = file_id_match.group(1)
                # Try different URL formats
                for test_url in [
                    f"https://www.mediafire.com/file/{file_id}",
                    f"https://www.mediafire.com/download/{file_id}",
                    f"https://www.mediafire.com/view/{file_id}",
                ]:
                    logger.info(f"Trying alternative URL: {test_url}")
                    download_url, filename = get_mediafire_direct_link_sync(test_url)
                    if download_url:
                        break
        
        if not download_url:
            await status_msg.edit_text(
                "❌ **Failed to extract download link from MediaFire**\n\n"
                "This could be due to:\n"
                "• File requires login/captcha\n"
                "• File is private/deleted\n"
                "• MediaFire changed their page structure\n\n"
                "**Alternative solution:**\n"
                "1. Open the link in your browser\n"
                "2. Click the download button\n"
                "3. Copy the direct download URL (starts with https://download...)\n"
                "4. Use: `/upload DIRECT_URL`"
            )
            return
        
        logger.info(f"Successfully extracted download URL: {download_url[:100]}...")
        logger.info(f"Filename: {filename}")
        
        # Sanitize filename
        if not filename:
            filename = f"mediafire_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Clean filename
        invalid_chars = r'[<>:"/\\|?*]'
        filename = re.sub(invalid_chars, '_', filename)
        filename = ' '.join(filename.split()).strip()
        
        # Ensure extension
        if '.' not in filename:
            ext_match = re.search(r'\.([a-zA-Z0-9]{2,4})(?:\?|$)', download_url)
            if ext_match:
                filename += '.' + ext_match.group(1)
            else:
                filename += '.pdf'
        
        # Prepare file path
        file_path = f"./downloads/{filename}"
        os.makedirs("./downloads", exist_ok=True)
        
        await status_msg.edit_text(
            f"⬇️ **Downloading:** `{filename}`\n\n"
            f"⏳ Please wait...\n"
            f"📎 Size: Unknown (downloading...)"
        )
        
        # Download file
        success, result = await download_file(download_url, file_path)
        
        if success:
            file_size = os.path.getsize(file_path)
            size_human = humanize.naturalsize(file_size)
            
            await status_msg.edit_text(
                f"✅ **Downloaded successfully!**\n\n"
                f"📄 **Name:** `{filename}`\n"
                f"📊 **Size:** {size_human}\n\n"
                f"📤 **Uploading to Telegram...**"
            )
            
            try:
                await message.reply_document(
                    document=file_path,
                    caption=(
                        f"📄 `{filename}`\n"
                        f"📊 {size_human}\n"
                        f"🔗 [MediaFire]({url})"
                    )
                )
                
                if LOG_CHANNEL:
                    await client.send_document(
                        chat_id=LOG_CHANNEL,
                        document=file_path,
                        caption=(
                            f"📥 **Uploaded by:** {message.from_user.mention}\n"
                            f"📄 **Name:** `{filename}`\n"
                            f"📊 **Size:** {size_human}\n"
                            f"🔗 **Source:** {url}"
                        )
                    )
                
                await status_msg.edit_text(
                    f"✅ **Upload complete!**\n\n"
                    f"📄 `{filename}`\n"
                    f"📊 {size_human}"
                )
                
                # Cleanup
                try:
                    os.remove(file_path)
                    logger.info(f"Removed local file: {file_path}")
                except:
                    pass
                
            except Exception as e:
                await status_msg.edit_text(f"❌ **Upload failed:** `{str(e)}`")
                logger.error(f"Upload error: {e}")
        else:
            await status_msg.edit_text(
                f"❌ **Download failed:**\n\n"
                f"`{result}`\n\n"
                f"URL: `{download_url[:100]}...`"
            )
            logger.error(f"Download failed: {result}")
    else:
        await message.reply_text("❌ **Unsupported URL**\n\nOnly MediaFire links are supported.")