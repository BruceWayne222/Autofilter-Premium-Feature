import os
import re
import ssl
import aiohttp
import certifi
import asyncio
import json
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
        # Retry with disabled verification
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

def extract_filename_from_url(share_url):
    """Extract filename from MediaFire URL"""
    # Try to get filename from URL path
    parsed = urlparse(share_url)
    path = parsed.path
    
    # Remove /file/ or /download/ from path
    path = re.sub(r'^/(?:file|download|view)/', '', path)
    
    # If path has filename
    if path and '.' in path:
        filename = unquote(path)
        return filename
    
    return None

async def get_mediafire_download_link(share_url):
    """Get actual download link from MediaFire using multiple methods"""
    
    # First try: Extract directly from URL structure
    # MediaFire URLs often have the file ID
    file_id_match = re.search(r'/([a-zA-Z0-9]+)(?:/|$)', share_url)
    if file_id_match:
        file_id = file_id_match.group(1)
        # Try different URL patterns
        possible_urls = [
            f"https://www.mediafire.com/file/{file_id}",
            f"https://www.mediafire.com/download/{file_id}",
            f"https://download.mediafire.com/file/{file_id}",
        ]
        
        for url in possible_urls:
            try:
                download_url, filename = await fetch_mediafire_page(url)
                if download_url:
                    return download_url, filename
            except:
                continue
    
    # Second try: Use the original URL
    download_url, filename = await fetch_mediafire_page(share_url)
    if download_url:
        return download_url, filename
    
    # Third try: Use requests library as fallback
    try:
        import requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(share_url, headers=headers, timeout=30, verify=False)
        if response.status_code == 200:
            html = response.text
            
            # Look for download URL in JavaScript
            patterns = [
                r'kNO\s*=\s*"([^"]+)"',
                r'window\.location\s*=\s*"([^"]+)"',
                r'location\.href\s*=\s*"([^"]+)"',
                r'"downloadUrl"\s*:\s*"([^"]+)"',
                r'<a[^>]+id="downloadButton"[^>]+href="([^"]+)"',
                r'<a[^>]+class="download-link"[^>]+href="([^"]+)"',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    download_url = match.group(1)
                    if download_url.startswith('/'):
                        download_url = 'https://www.mediafire.com' + download_url
                    
                    # Extract filename
                    filename = extract_filename_from_html(html)
                    if filename:
                        return download_url, filename
                    return download_url, None
    except:
        pass
    
    return None, None

async def fetch_mediafire_page(url):
    """Fetch MediaFire page and extract download link"""
    ssl_context = get_ssl_context()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=ssl_context, headers=headers, timeout=30) as response:
                if response.status != 200:
                    return None, None
                
                html = await response.text()
                
                # Extract filename
                filename = extract_filename_from_html(html)
                
                # Extract download URL using multiple patterns
                patterns = [
                    r'kNO\s*=\s*"([^"]+)"',
                    r'window\.location\s*=\s*"([^"]+)"',
                    r'location\.href\s*=\s*"([^"]+)"',
                    r'"downloadUrl"\s*:\s*"([^"]+)"',
                    r'<a[^>]+id="downloadButton"[^>]+href="([^"]+)"',
                    r'<a[^>]+class="download-link"[^>]+href="([^"]+)"',
                    r'<a[^>]+data-download-link="([^"]+)"',
                    r'<a[^>]+href="(https?://download[^"]*\.mediafire\.com[^"]+)"',
                    r'(https?://download[^"]*\.mediafire\.com[^"\s]+)',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, html)
                    if match:
                        download_url = match.group(1)
                        # Clean up URL
                        download_url = download_url.replace('\\/', '/')
                        download_url = download_url.replace('\/', '/')
                        if download_url.startswith('/'):
                            download_url = 'https://www.mediafire.com' + download_url
                        return download_url, filename
                
                # If no pattern matched, try to find any download link
                link_pattern = r'<a[^>]+href="([^"]+\.(?:pdf|zip|rar|mp4|mp3|mkv|avi|jpg|jpeg|png|gif))"'
                matches = re.findall(link_pattern, html, re.IGNORECASE)
                for link in matches:
                    if 'mediafire' not in link and link.startswith('http'):
                        return link, filename
                
                return None, filename
                
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return None, None

def extract_filename_from_html(html):
    """Extract filename from HTML content"""
    # Method 1: Check title
    title_match = re.search(r'<title>([^<]+)</title>', html)
    if title_match:
        title = title_match.group(1)
        # Remove common words
        title = re.sub(r'(?:Download|MediaFire|File|from|-\s*)', '', title)
        title = title.strip()
        if title and '.' in title:
            return title
    
    # Method 2: Check og:title
    og_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og_match:
        title = og_match.group(1)
        title = re.sub(r'(?:Download|MediaFire|File|from|-\s*)', '', title)
        title = title.strip()
        if title and '.' in title:
            return title
    
    # Method 3: Check filename in JavaScript
    js_patterns = [
        r'"filename"\s*:\s*"([^"]+)"',
        r'filename\s*=\s*"([^"]+)"',
        r'var\s+fileName\s*=\s*"([^"]+)"',
        r'file_name\s*=\s*"([^"]+)"',
    ]
    for pattern in js_patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    
    # Method 4: Check download button data attributes
    data_patterns = [
        r'<a[^>]+data-filename="([^"]+)"',
        r'<a[^>]+data-name="([^"]+)"',
    ]
    for pattern in data_patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    
    # Method 5: Check for filename in URL links
    link_pattern = r'<a[^>]+href="[^"]*/([^"/]+\.(?:pdf|zip|rar|mp4|mp3|mkv|avi|jpg|jpeg|png|gif))"'
    matches = re.findall(link_pattern, html, re.IGNORECASE)
    if matches:
        return matches[0]
    
    return None

def sanitize_filename(filename):
    """Sanitize filename"""
    if not filename:
        return None
    
    # Remove invalid characters
    invalid_chars = r'[<>:"/\\|?*]'
    filename = re.sub(invalid_chars, '_', filename)
    
    # Remove extra spaces
    filename = ' '.join(filename.split()).strip()
    
    # Remove duplicate extensions
    parts = filename.split('.')
    if len(parts) > 2:
        ext = parts[-1] if parts[-1] else 'pdf'
        name = '.'.join(parts[:-1])
        filename = f"{name}.{ext}"
    
    # Limit length
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
        download_url, filename = await get_mediafire_download_link(url)
        
        if not download_url:
            # Try with alternative approach: direct API
            try:
                # Some MediaFire files have direct download links
                file_id = re.search(r'/([a-zA-Z0-9]{15,})', url)
                if file_id:
                    api_url = f"https://www.mediafire.com/api/1.5/file/get_info.php?r=123&key=your_api_key&quick_key={file_id.group(1)}"
                    # Try to get from API (though you need API key)
                    pass
            except:
                pass
            
            await status_msg.edit_text(
                "❌ **Failed to extract download link from MediaFire**\n\n"
                "This could be due to:\n"
                "• File requires login/captcha\n"
                "• File is private/deleted\n"
                "• MediaFire changed their page structure\n\n"
                "**Try this instead:**\n"
                "1. Open the MediaFire link in browser\n"
                "2. Click download to get direct link\n"
                "3. Use that direct link with /upload"
            )
            return
        
        # Get filename if not found
        if not filename:
            filename = extract_filename_from_url(url)
        
        if not filename:
            filename = f"mediafire_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # Sanitize filename
        filename = sanitize_filename(filename)
        if not filename:
            filename = f"mediafire_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # Ensure extension
        if '.' not in filename:
            ext_match = re.search(r'\.(pdf|zip|rar|mp4|mp3|mkv|avi|jpg|jpeg|png|gif)$', download_url, re.IGNORECASE)
            if ext_match:
                filename += '.' + ext_match.group(1)
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
                # Send file
                await message.reply_document(
                    document=file_path,
                    caption=(
                        f"📄 `{filename}`\n"
                        f"📊 {size_human}\n"
                        f"🔗 [MediaFire]({url})"
                    )
                )
                
                # Log channel
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