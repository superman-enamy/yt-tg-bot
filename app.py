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
            headers = {
                'accept': '*/*',
                'accept-language': 'en-US,en;q=0.9',
                'cache-control': 'no-cache',
                'dnt': '1',
                'origin': 'https://frame.y2meta-uk.com',
                'pragma': 'no-cache',
                'priority': 'u=1, i',
                'referer': 'https://frame.y2meta-uk.com/',
                'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
                'sec-ch-ua-mobile': '?1',
                'sec-ch-ua-platform': '"Android"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'cross-site',
                'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Mobile Safari/537.36'
            }

            async with httpx.AsyncClient(timeout=60.0) as http:
                # Step 1: Get Key
                try:
                    key_headers = headers.copy()
                    key_headers['content-type'] = 'application/json'
                    key_resp = await http.get(f"https://cnv.cx/v2/sanity/key?id={video_id}", headers=key_headers)
                    key_data = key_resp.json()
                    auth_key = key_data.get("key")
                except Exception as e:
                    return await status_msg.edit_text(f"Error fetching key: {e}")
                    
                if not auth_key:
                    return await status_msg.edit_text("Could not get auth key.")

                # Step 2: Converter
                await status_msg.edit_text("Generating download link...")
                conv_headers = headers.copy()
                conv_headers['content-type'] = 'application/x-www-form-urlencoded'
                conv_headers['key'] = auth_key
                
                payload = {
                    "link": f"https://youtu.be/{video_id}",
                    "format": "mp4",
                    "audioBitrate": "128",
                    "videoQuality": quality,
                    "filenameStyle": "pretty",
                    "vCodec": "h264"
                }
                
                try:
                    conv_resp = await http.post("https://cnv.cx/v2/converter", headers=conv_headers, data=payload)
                    conv_data = conv_resp.json()
                    dl_url = conv_data.get("url")
                    filename = conv_data.get("filename", f"{video_id}.mp4")
                except Exception as e:
                    return await status_msg.edit_text(f"Error converting: {e}")
                    
                if not dl_url:
                    return await status_msg.edit_text(f"Could not get download link. Response:\n{conv_data}")
                    
                # Step 3: Download
                await status_msg.edit_text(f"Downloading video to server... ({filename})")
                local_path = f"{uuid.uuid4()}_{filename}"
                
                dl_headers = {
                    "Referer": "https://v38.www-y2mate.com/",
                    "User-Agent": headers['user-agent']
                }
                
                try:
                    async with httpx.AsyncClient(timeout=None) as dl_http:
                        async with dl_http.stream('GET', dl_url, headers=dl_headers) as resp:
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
                                            text = f"📥 **Downloading {quality}p to server...**\n\n{filename}\n{prog_bar}\n{format_bytes(downloaded_size)} / {format_bytes(total_size)}"
                                        else:
                                            text = f"📥 **Downloading {quality}p to server...**\n\n{filename}\nDownloaded: {format_bytes(downloaded_size)}"
                                        try:
                                            await status_msg.edit_text(text)
                                        except Exception:
                                            pass
                                        last_update_time = now
                except Exception as e:
                    return await status_msg.edit_text(f"Error downloading video: {e}")
                    
                # Step 3.5: Download Thumbnail
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
                        text = f"📤 **Uploading {quality}p to Telegram...**\n\n{video_title}\n{prog_bar}\n{format_bytes(current)} / {format_bytes(total)}"
                        try:
                            await status_msg.edit_text(text)
                        except Exception:
                            pass
                        last_upload_time[0] = now

                await status_msg.edit_text(f"📤 **Uploading {quality}p to Telegram...**\n\nStarting upload...")
                try:
                    kwargs = {
                        "chat_id": chat_id,
                        "video": local_path,
                        "caption": video_title,
                        "supports_streaming": True,
                        "progress": progress
                    }
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
            await message.reply_text("Send me a YouTube link and I'll download it for you!\n\nYou can also specify quality directly: `https://youtu.be/... -q 720p`")

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
