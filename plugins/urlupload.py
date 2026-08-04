import os
import re
import ssl
import aiohttp
import certifi
import asyncio
import json
import base64
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

async def get_mediafire_direct_link(share_url):
    """
    Extract direct download link from MediaFire using multiple methods
    """
    ssl_context = get_ssl_context()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(share_url, ssl=ssl_context, headers=headers, timeout=30) as response:
                if response.status != 200:
                    return None, None
                
                html = await response.text()
                
                # METHOD 1: Extract from JavaScript variable 'kNO'
                kno_pattern = r'var\s+kNO\s*=\s*"([^"]+)"'
                match = re.search(kno_pattern, html)
                if match:
                    download_url = match.group(1)
                    download_url = download_url.replace('\\/', '/')
                    download_url = download_url.replace('\/', '/')
                    filename = extract_filename_from_mediafire_page(html)
                    return download_url, filename
                
                # METHOD 2: Extract from 'window.location'
                location_pattern = r'window\.location\s*=\s*"([^"]+)"'
                match = re.search(location_pattern, html)
                if match:
                    download_url = match.group(1)
                    filename = extract_filename_from_mediafire_page(html)
                    return download_url, filename
                
                # METHOD 3: Extract from 'location.href'
                href_pattern = r'location\.href\s*=\s*"([^"]+)"'
                match = re.search(href_pattern, html)
                if match:
                    download_url = match.group(1)
                    filename = extract_filename_from_mediafire_page(html)
                    return download_url, filename
                
                # METHOD 4: Extract from download button
                button_pattern = r'<a[^>]+id="downloadButton"[^>]+href="([^"]+)"'
                match = re.search(button_pattern, html)
                if match:
                    download_url = match.group(1)
                    if download_url.startswith('/'):
                        download_url = 'https://www.mediafire.com' + download_url
                    filename = extract_filename_from_mediafire_page(html)
                    return download_url, filename
                
                # METHOD 5: Extract from data attributes
                data_pattern = r'<a[^>]+data-download-link="([^"]+)"'
                match = re.search(data_pattern, html)
                if match:
                    download_url = match.group(1)
                    filename = extract_filename_from_mediafire_page(html)
                    return download_url, filename
                
                # METHOD 6: Extract from direct file links
                direct_pattern = r'(https?://download[^"]*\.mediafire\.com[^"\s]+)'
                match = re.search(direct_pattern, html)
                if match:
                    download_url = match.group(1)
                    filename = extract_filename_from_mediafire_page(html)
                    return download_url, filename
                
                return None, None
                
    except Exception as e:
        logger.error(f"MediaFire extraction error: {e}")
        return None, None

def extract_filename_from_mediafire_page(html):
    """Extract filename from MediaFire page HTML"""
    # Method 1: Title tag
    title_match = re.search(r'<title>([^<]+)</title>', html)
    if title_match:
        title = title_match.group(1)
        title = re.sub(r'(?:Download|MediaFire|File|from|-\s*)', '', title)
        title = title.strip()
        if title and '.' in title:
            return title
    
    # Method 2: og:title
    og_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og_match:
        title = og_match.group(1)
        title = re.sub(r'(?:Download|MediaFire|File|from|-\s*)', '', title)
        title = title.strip()
        if title and '.' in title:
            return title
    
    # Method 3: JavaScript variable
    js_patterns = [
        r'"filename"\s*:\s*"([^"]+)"',
        r'filename\s*=\s*"([^"]+)"',
        r'fileName\s*=\s*"([^"]+)"',
        r'file_name\s*=\s*"([^"]+)"',
    ]
    for pattern in js_patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    
    # Method 4: Data attributes
    data_patterns = [
        r'data-filename="([^"]+)"',
        r'data-name="([^"]+)"',
    ]
    for pattern in data_patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    
    return None

def extract_filename_from_url(url):
    """Extract filename from URL"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    path = re.sub(r'^/(?:file|download|view)/', '', path)
    if path and '.' in path:
        return path
    return None

def sanitize_filename(filename):
    """Sanitize filename"""
    if not filename:
        return None
    
    invalid_chars = r'[<>:"/\\|?*]'
    filename = re.sub(invalid_chars, '_', filename)
    filename = ' '.join(filename.split()).strip()
    
    # Remove duplicate extensions
    parts = filename.split('.')
    if len(parts) > 2:
        ext = parts[-1] if parts[-1] else 'pdf'
        name = '.'.join(parts[:-1])
        filename = f"{name}.{ext}"
    
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:195] + ext
    
    return filename

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
        
        # Try to get download link
        download_url, filename = await get_mediafire_direct_link(url)
        
        if not download_url:
            # Try alternative: extract file ID and use different URL format
            file_id_match = re.search(r'/([a-zA-Z0-9]{15,})', url)
            if file_id_match:
                file_id = file_id_match.group(1)
                for test_url in [
                    f"https://www.mediafire.com/file/{file_id}",
                    f"https://www.mediafire.com/download/{file_id}",
                ]:
                    download_url, filename = await get_mediafire_direct_link(test_url)
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
                    "3. Copy the direct download URL\n"
                    "4. Use: `/upload DIRECT_URL`"
                )
                return
        
        # Get filename if not found
        if not filename:
            filename = extract_filename_from_url(url)
        
        if not filename:
            filename = f"mediafire_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Sanitize filename
        filename = sanitize_filename(filename)
        if not filename:
            filename = f"mediafire_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Ensure extension
        if '.' not in filename:
            ext_match = re.search(r'\.(pdf|zip|rar|mp4|mp3|mkv|avi|jpg|jpeg|png|gif|txt|doc|docx|xls|xlsx|ppt|pptx)$', download_url, re.IGNORECASE)
            if ext_match:
                filename += '.' + ext_match.group(1)
            else:
                url_ext = re.search(r'\.([a-zA-Z0-9]{2,4})(?:\?|$)', download_url)
                if url_ext:
                    filename += '.' + url_ext.group(1)
                else:
                    filename += '.pdf'
        
        # Prepare file path
        file_path = f"./downloads/{filename}"
        os.makedirs("./downloads", exist_ok=True)
        
        await status_msg.edit_text(
            f"⬇️ **Downloading:** `{filename}`\n\n"
            f"⏳ Please wait..."
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
                
                os.remove(file_path)
                
            except Exception as e:
                await status_msg.edit_text(f"❌ **Upload failed:** `{str(e)}`")
        else:
            await status_msg.edit_text(
                f"❌ **Download failed:**\n\n"
                f"`{result}`"
            )
    else:
        await message.reply_text("❌ **Unsupported URL**\n\nOnly MediaFire links are supported.")