@Client.on_message(filters.private & filters.command("debug") & filters.user(ADMINS))
async def debug_mediafire(client, message):
    """Debug MediaFire extraction"""
    if len(message.command) < 2:
        await message.reply_text("Give me a MediaFire URL")
        return
    
    url = message.command[1]
    
    try:
        import requests
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=30)
        
        html = response.text[:2000]  # First 2000 chars
        
        # Save to file for debugging
        with open('debug_mediafire.html', 'w', encoding='utf-8') as f:
            f.write(html)
        
        # Check for key patterns
        has_kno = 'kNO' in html
        has_download = 'downloadButton' in html
        has_filename = 'filename' in html.lower()
        
        await message.reply_text(
            f"✅ **Debug Info:**\n\n"
            f"Status: {response.status_code}\n"
            f"Has kNO: {has_kno}\n"
            f"Has downloadButton: {has_download}\n"
            f"Has filename: {has_filename}\n\n"
            f"HTML saved to: debug_mediafire.html\n\n"
            f"**First 500 chars of HTML:**\n```\n{html[:500]}\n```"
        )
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")