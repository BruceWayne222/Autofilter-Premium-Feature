import os
import re
import ssl
import aiohttp
import certifi
from urllib.parse import urlparse, urljoin, unquote
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import ADMINS, LOG_CHANNEL
from database.users_chats_db import db
from Script import script

# SSL Context for downloads
def get_ssl_context():
    """Create SSL context that handles certificate issues"""
    try:
        # Try using certifi first
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        return ssl_context
    except:
        # Fallback to disabled verification (for MediaFire)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

async def download_file(url, file_path):
    """Download file with proper SSL handling"""
    ssl_context = get_ssl_context()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=ssl_context, timeout=300) as response:
                if response.status == 200:
                    with open(file_path, 'wb') as f:
                        f.write(await response.read())
                    return True, "Download successful"
                return False, f"HTTP Error: {response.status}"
    except aiohttp.ClientSSLError as e:
        # If SSL fails, retry with disabled verification
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=ssl_context, timeout=300) as response:
                    if response.status == 200:
                        with open(file_path, 'wb') as f:
                            f.write(await response.read())
                        return True, "Download successful (SSL bypassed)"
                    return False, f"HTTP Error: {response.status}"
        except Exception as e:
            return False, f"SSL retry failed: {str(e)}"
    except Exception as e:
        return False, f"Download failed: {str(e)}"

# MediaFire URL extractor
def extract_mediafire_url(text):
    """Extract MediaFire URL from text"""
    pattern = r'(?:https?://)?(?:www\.)?mediafire\.com/(?:file|view|download)/([a-zA-Z0-9]+)'
    match = re.search(pattern, text)
    if match:
        return f"https://www.mediafire.com/file/{match.group(1)}/"
    return None

def extract_filename_from_mediafire(html):
    """Extract original filename from MediaFire page"""
    # Method 1: Look for filename in page title
    title_pattern = r'<title>([^<]+)</title>'
    match = re.search(title_pattern, html)
    if match:
        title = match.group(1)
        # Remove "MediaFire" from title
        filename = title.replace('MediaFire', '').strip()
        if filename:
            return filename
    
    # Method 2: Look for filename in download button
    name_pattern = r'<a[^>]+id="downloadButton"[^>]+data-filename="([^"]+)"'
    match = re.search(name_pattern, html)
    if match:
        return match.group(1)
    
    # Method 3: Look for filename in the page
    name_pattern2 = r'"filename":"([^"]+)"'
    match = re.search(name_pattern2, html)
    if match:
        return match.group(1)
    
    # Method 4: Look for filename in URL
    name_pattern3 = r'<meta property="og:title" content="([^"]+)"'
    match = re.search(name_pattern3, html)
    if match:
        return match.group(1)
    
    return None

def sanitize_filename(filename):
    """Remove invalid characters from filename"""
    # Remove invalid characters for Windows/Linux
    invalid_chars = r'[<>:"/\\|?*]'
    filename = re.sub(invalid_chars, '_', filename)
    
    # Remove extra spaces
    filename = ' '.join(filename.split())
    
    # Limit filename length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:195] + ext
    
    return filename

async def get_mediafire_download_link(share_url):
    """Get actual download link and filename from MediaFire share URL"""
    ssl_context = get_ssl_context()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(share_url, ssl=ssl_context) as response:
                if response.status != 200:
                    return None, None
                
                html = await response.text()
                
                # Extract filename first
                filename = extract_filename_from_mediafire(html)
                
                # Extract download link from HTML
                # Method 1: Look for 'kNO' variable (common in MediaFire)
                pattern1 = r'kNO\s*=\s*"([^"]+)"'
                match1 = re.search(pattern1, html)
                if match1:
                    return match1.group(1), filename
                
                # Method 2: Look for download button link
                pattern2 = r'<a[^>]+id="downloadButton"[^>]+href="([^"]+)"'
                match2 = re.search(pattern2, html)
                if match2:
                    return match2.group(1), filename
                
                # Method 3: Look for any direct file link
                pattern3 = r'(?:https?://)?(?:download\d+\.mediafire\.com/[^"\']+)'
                match3 = re.search(pattern3, html)
                if match3:
                    return match3.group(0), filename
                
                return None, filename
    except Exception as e:
        print(f"MediaFire extract error: {e}")
        return None, None

async def get_filename_from_url(url):
    """Extract filename from URL as fallback"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = os.path.basename(path)
    
    # If filename is empty or has no extension, try to get from query params
    if not filename or '.' not in filename:
        query = parsed.query
        if 'filename=' in query:
            match = re.search(r'filename=([^&]+)', query)
            if match:
                filename = unquote(match.group(1))
    
    return filename if filename else "downloaded_file"

@Client.on_message(filters.private & filters.command("upload") & filters.user(ADMINS))
async def upload_file(client, message):
    """Handle /upload command for admins"""
    if len(message.command) < 2:
        await message.reply_text("❌ Please provide a URL\n\nExample: `/upload https://www.mediafire.com/file/xxx/`")
        return
    
    url = message.command[1].strip()
    
    # Check if it's a MediaFire URL
    if "mediafire.com" in url:
        status_msg = await message.reply_text("📥 Processing MediaFire link...")
        
        # Get actual download link and filename
        download_url, filename = await get_mediafire_download_link(url)
        
        if not download_url:
            await status_msg.edit_text("❌ Failed to extract download link from MediaFire")
            return
        
        # If no filename extracted, use a fallback
        if not filename:
            filename = await get_filename_from_url(url)
            if not filename:
                filename = f"mediafire_file_{int(os.path.getsize('')) or 'unknown'}"
        
        # Sanitize filename
        filename = sanitize_filename(filename)
        
        # Ensure file has proper extension
        if '.' not in filename:
            # Try to detect from URL or add .pdf as fallback
            if 'pdf' in url.lower():
                filename += '.pdf'
            elif 'zip' in url.lower():
                filename += '.zip'
            elif 'rar' in url.lower():
                filename += '.rar'
            elif 'mp4' in url.lower():
                filename += '.mp4'
            elif 'mp3' in url.lower():
                filename += '.mp3'
            else:
                filename += '.pdf'  # Default extension
        
        # Prepare file path
        file_path = f"./downloads/{filename}"
        
        # Ensure downloads directory exists
        os.makedirs("./downloads", exist_ok=True)
        
        # Update status
        await status_msg.edit_text(f"⬇️ Downloading: `{filename}`\n📊 Size: fetching...")
        
        # Download file
        success, result = await download_file(download_url, file_path)
        
        if success:
            # Get file size
            file_size = os.path.getsize(file_path)
            size_mb = file_size / (1024 * 1024)
            
            await status_msg.edit_text(f"✅ File downloaded successfully!\n📄 Name: `{filename}`\n📊 Size: {size_mb:.2f} MB\n\n📤 Uploading to Telegram...")
            
            try:
                # Send file to user with original filename
                await message.reply_document(
                    document=file_path,
                    caption=f"📄 `{filename}`\n📊 Size: {size_mb:.2f} MB\n🔗 Source: [MediaFire]({url})"
                )
                
                # Send to log channel if configured
                if LOG_CHANNEL:
                    await client.send_document(
                        chat_id=LOG_CHANNEL,
                        document=file_path,
                        caption=f"📥 Uploaded by: {message.from_user.mention}\n📄 Name: `{filename}`\n📊 Size: {size_mb:.2f} MB\n🔗 Source: {url}"
                    )
                
                await status_msg.edit_text(f"✅ File uploaded successfully!\n\n📄 `{filename}`\n📊 Size: {size_mb:.2f} MB")
                
                # Clean up
                os.remove(file_path)
                print(f"✅ Removed local file: {file_path}")
                
            except Exception as e:
                await status_msg.edit_text(f"❌ Upload failed: {str(e)}")
                # Don't delete file if upload failed, maybe try again later
        else:
            await status_msg.edit_text(f"❌ Download failed: {result}")
    else:
        await message.reply_text("❌ Currently only MediaFire links are supported")