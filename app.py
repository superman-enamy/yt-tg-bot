import asyncio
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
import os
import re
import uuid
import httpx
import time
import math
from dotenv import load_dotenv
from fastapi import FastAPI
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

load_dotenv()

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = FastAPI()
bot = None


def get_video_metadata(filepath):
    try:
        import subprocess, json
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)
        
        duration = int(float(data['format']['duration']))
        width = 0
        height = 0
        for stream in data.get('streams', []):
            if stream['codec_type'] == 'video':
                width = int(stream['width'])
                height = int(stream['height'])
                break
        return duration, width, height
    except Exception:
        return 0, 0, 0

def extract_video_id(url: str):
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

def generate_progress_bar(percentage):
    filled_length = int(math.floor(percentage / 10))
    bar = '█' * filled_length + '▒' * (10 - filled_length)
    return f"[{bar}] {percentage:.1f}%"

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

@app.on_event("startup")
async def on_startup():
    global bot
    if API_ID and API_HASH and BOT_TOKEN:
        bot = Client("my_bot", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
        
        async def execute_download(client, chat_id, url, video_id, quality, status_msg):
            savenow_api_key = "dfcb6d76f2f6a9894gjkege8a4ab232222"
            start_url = f"https://p.savenow.to/ajax/download.php?copyright=0&allow_extended_duration=1&format={quality}&url={url}&api={savenow_api_key}"
            
            async with httpx.AsyncClient(timeout=60.0) as http:
                try:
                    start_resp = await http.get(start_url)
                    start_data = start_resp.json()
                except Exception as e:
                    return await status_msg.edit_text(f"Error starting download: {e}")
                
                if not start_data.get("success") or not start_data.get("progress_url"):
                    return await status_msg.edit_text(f"Could not initialize download. Response:{start_data}")
                    
                progress_url = start_data["progress_url"]
                
                await status_msg.edit_text("Processing video on server... (0%)")
                dl_url = None
                
                # Sanitize filename
                raw_title = start_data.get("info", {}).get("title", "video")
                clean_title = "".join(c for c in raw_title if c.isalnum() or c in " ._-").strip()
                filename = f"{clean_title}.mp4"
                video_title = filename
                
                last_update = time.time()
                while True:
                    try:
                        prog_resp = await http.get(progress_url)
                        prog_data = prog_resp.json()
                        prog_value = int(prog_data.get("progress", 0)) / 10.0
                        
                        if time.time() - last_update >= 2.0:
                            prog_bar = generate_progress_bar(prog_value)
                            text = f"🔄 **Processing on server...**{prog_bar}{prog_data.get('text', '')}"
                            try:
                                await status_msg.edit_text(text)
                            except Exception:
                                pass
                            last_update = time.time()
                        
                        if str(prog_data.get("success")) == "1" or prog_value >= 100:
                            dl_url = prog_data.get("download_url")
                            break
                        
                        import asyncio
                        await asyncio.sleep(2)
                    except Exception as e:
                        return await status_msg.edit_text(f"Error polling progress: {e}")
                
                if not dl_url:
                     return await status_msg.edit_text("Download link not found after processing.")

                await status_msg.edit_text(f"📥 Downloading to bot server... ({filename})")
                import uuid
                local_path = f"{uuid.uuid4()}_video.mp4"
                
                try:
                    async with httpx.AsyncClient(timeout=None) as dl_http:
                        async with dl_http.stream('GET', dl_url) as resp:
                            resp.raise_for_status()
                            total_size = int(resp.headers.get('content-length', 0))
                            downloaded_size = 0
                            last_update_time = time.time()
                            
                            with open(local_path, 'wb') as f:
                                async for chunk in resp.aiter_bytes():
                                    f.write(chunk)
                                    downloaded_size += len(chunk)
                                    
                                    now = time.time()
                                    if now - last_update_time >= 2.0:
                                        if total_size > 0:
                                            percentage = (downloaded_size / total_size) * 100
                                            prog_bar = generate_progress_bar(percentage)
                                            text = f"📥 **Downloading {quality}p to bot server...**{prog_bar}{format_bytes(downloaded_size)} / {format_bytes(total_size)}"
                                        else:
                                            text = f"📥 **Downloading {quality}p to bot server...**Downloaded: {format_bytes(downloaded_size)}"
                                        try:
                                            await status_msg.edit_text(text)
                                        except Exception:
                                            pass
                                        last_update_time = now
                except Exception as e:
                    return await status_msg.edit_text(f"Error downloading video: {e}")

                # Fix video metadata using FFmpeg so it can be forwarded/seeked
                await status_msg.edit_text(f"🔧 Fixing video metadata for {quality}p...")
                fixed_path = f"{uuid.uuid4()}_fixed.mp4"
                try:
                    import subprocess
                    subprocess.run(["ffmpeg", "-y", "-i", local_path, "-c", "copy", "-movflags", "+faststart", fixed_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    import os
                    os.remove(local_path)
                    local_path = fixed_path
                except Exception as e:
                    print("FFmpeg fix failed:", e)
                    # Continue with original if it fails
                await status_msg.edit_text("Fetching video thumbnail...")
                thumb_path = f"{uuid.uuid4()}_thumb.jpg"
                video_title = filename
                
                try:
                    thumb_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    async with httpx.AsyncClient() as meta_http:
                        thumb_resp = await meta_http.get(thumb_url)
                        if thumb_resp.status_code == 200:
                            with open(thumb_path, 'wb') as tf:
                                tf.write(thumb_resp.content)
                        else:
                            thumb_url_hq = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                            thumb_resp = await meta_http.get(thumb_url_hq)
                            if thumb_resp.status_code == 200:
                                with open(thumb_path, 'wb') as tf:
                                    tf.write(thumb_resp.content)
                except Exception as e:
                    print("Could not get thumbnail:", e)
                    pass

                # Step 4: Upload to Telegram
                last_upload_time = [time.time()]
                async def progress(current, total):
                    now = time.time()
                    if now - last_upload_time[0] >= 2.0:
                        percentage = (current / total) * 100 if total > 0 else 0
                        prog_bar = generate_progress_bar(percentage)
                        text = f"📤 **Uploading {quality}p to Telegram...**{video_title}{prog_bar}{format_bytes(current)} / {format_bytes(total)}"
                        try:
                            await status_msg.edit_text(text)
                        except Exception:
                            pass
                        last_upload_time[0] = now

                await status_msg.edit_text(f"📤 **Uploading {quality}p to Telegram...**Starting upload...")
                try:
                    duration, width, height = get_video_metadata(local_path)
                    kwargs = {
                        "chat_id": chat_id,
                        "video": local_path,
                        "caption": video_title,
                        "supports_streaming": True,
                        "progress": progress
                    }
                    if duration > 0:
                        kwargs["duration"] = duration
                    if width > 0:
                        kwargs["width"] = width
                    if height > 0:
                        kwargs["height"] = height
                        
                    if os.path.exists(thumb_path):
                        kwargs["thumb"] = thumb_path
                        
                    await client.send_video(**kwargs)
                    await status_msg.delete()
                except Exception as e:
                    await status_msg.edit_text(f"Error uploading to Telegram: {e}")
                finally:
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    if os.path.exists(thumb_path):
                        os.remove(thumb_path)

        @bot.on_message(filters.command("start"))
        async def start_cmd(client, message):
            await message.reply_text("Send me a YouTube link and I'll download it for you!You can also specify quality directly: `https://youtu.be/... -q 720p`")

        @bot.on_message(filters.text & filters.regex(r"(youtube\.com|youtu\.be)"))
        async def process_video(client, message: Message):
            text = message.text.strip()
            
            quality_match = re.search(r"-q\s*(1080|720|480|360|240)p?", text, re.IGNORECASE)
            quality = quality_match.group(1) if quality_match else None
            
            url = re.sub(r"-q\s*(1080|720|480|360|240)p?", "", text, flags=re.IGNORECASE).strip()
            
            video_id = extract_video_id(url)
            if not video_id:
                return await message.reply_text("Could not extract Video ID.")
                
            if quality:
                status_msg = await message.reply_text(f"Preparing {quality}p download...")
                await execute_download(client, message.chat.id, url, video_id, quality, status_msg)
            else:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("1080p", callback_data=f"dl_{video_id}_1080")],
                    [InlineKeyboardButton("720p", callback_data=f"dl_{video_id}_720")],
                    [InlineKeyboardButton("480p", callback_data=f"dl_{video_id}_480")],
                    [InlineKeyboardButton("360p", callback_data=f"dl_{video_id}_360")],
                    [InlineKeyboardButton("240p", callback_data=f"dl_{video_id}_240")]
                ])
                await message.reply_text("Please choose video quality:", reply_markup=keyboard)

        @bot.on_callback_query(filters.regex(r"^dl_"))
        async def on_quality_selected(client, callback_query: CallbackQuery):
            data = callback_query.data
            _, video_id, quality = data.split("_")
            url = f"https://youtu.be/{video_id}"
            
            await callback_query.answer()
            status_msg = await callback_query.message.edit_text(f"Preparing {quality}p download...")
            await execute_download(client, callback_query.message.chat.id, url, video_id, quality, status_msg)
        
        await bot.start()
        print("Telegram bot started successfully!")
    else:
        print("WARNING: Telegram credentials not set. Bot will not start.")

@app.on_event("shutdown")
async def on_shutdown():
    global bot
    if bot:
        await bot.stop()

@app.get("/")
def ping():
    return {"status": "awake", "bot_configured": bot is not None}
