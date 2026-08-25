import telebot
from telebot.types import InputMediaPhoto
import requests
import os
import threading
import time
import yt_dlp
from flask import Flask

# Your Telegram Bot Token
BOT_TOKEN = "8969647277:AAF3jTCal-ZdqYqghm7ln0mrcTZUcTg3o6U"
bot = telebot.TeleBot(BOT_TOKEN)

# --- THE FIX: Forcefully clear any stuck webhooks or old sessions ---
try:
    bot.remove_webhook()
except Exception:
    pass
# -------------------------------------------------------------------

# Web Server for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "TikTok Downloader is running."

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Send me a TikTok link and I will download the video or photo slideshow for you!")

@bot.message_handler(func=lambda message: "tiktok.com" in message.text)
def handle_tiktok_link(message):
    raw_url = message.text
    url = raw_url.split('?')[0] # Clean tracking data
    
    status_msg = bot.reply_to(message, "⏳ Analyzing TikTok link...")

    try:
        # Step 1: Check if it's a photo post using the free TikWM API
        tikwm_api = "https://www.tikwm.com/api/"
        response = requests.get(tikwm_api, params={"url": url, "hd": 1}, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            # If the 'images' array exists in the data, it's a photo slideshow!
            if data.get('code') == 0 and 'images' in data.get('data', {}):
                bot.edit_message_text("📸 Photo slideshow detected! Sending images...", chat_id=message.chat.id, message_id=status_msg.message_id)
                
                images = data['data']['images']
                media_group = []
                
                for i, img_url in enumerate(images):
                    if i == 10: 
                        break
                    media_group.append(InputMediaPhoto(img_url))
                
                bot.send_media_group(message.chat.id, media_group)
                bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)
                return  

        # Step 2: Fallback to yt-dlp for video extraction
        bot.edit_message_text("🎥 Video detected! Processing with yt-dlp...", chat_id=message.chat.id, message_id=status_msg.message_id)
        
        file_name = f"{message.chat.id}_{message.message_id}.mp4"
        ydl_opts = {
            'format': 'best',
            'outtmpl': file_name,
            'quiet': True,
            'no_warnings': True,
            'max_filesize': 45000000, 
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        if os.path.exists(file_name):
            bot.edit_message_text("⏳ Uploading video to Telegram...", chat_id=message.chat.id, message_id=status_msg.message_id)
            with open(file_name, 'rb') as video_file:
                bot.send_video(
                    chat_id=message.chat.id,
                    video=video_file,
                    supports_streaming=True
                )
            bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)
            os.remove(file_name)
        else:
            bot.edit_message_text("❌ Error: Could not download the video.", chat_id=message.chat.id, message_id=status_msg.message_id)
            
    except Exception as e:
        bot.edit_message_text(f"❌ System Error: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)
        if 'file_name' in locals() and os.path.exists(file_name):
            os.remove(file_name)

def run_bot():
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 409:
                time.sleep(15)
            else:
                time.sleep(5)
        except Exception:
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
