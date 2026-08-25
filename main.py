import telebot
import os
import requests
import threading
import time
from flask import Flask

# 1. Your Telegram Token
BOT_TOKEN = "8969647277:AAG4RC0IxDRLMwr_VIzU-z_3VxQZlUd9ubo"
bot = telebot.TeleBot(BOT_TOKEN)

# Web Server for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "TikTok Bot is online and monitoring."

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Send me a TikTok link and I will download the video without a watermark!")

@bot.message_handler(func=lambda message: "tiktok.com" in message.text)
def handle_tiktok_link(message):
    url = message.text
    status_msg = bot.reply_to(message, "⏳ Connecting to Cobalt extraction server...")
    
    try:
        # 2. Use the free Cobalt API (Requires a POST request)
        api_url = "https://api.cobalt.tools/api/json"
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        payload = {
            "url": url
        }
        
        bot.edit_message_text("⏳ Fetching video metadata...", chat_id=message.chat.id, message_id=status_msg.message_id)
        
        # 3. Call the API
        response = requests.post(api_url, headers=headers, json=payload, timeout=15)
        response.raise_for_status() 
        data = response.json()
        
        # 4. Check for API errors (Cobalt returns 'status': 'error' if it fails)
        if data.get('status') == 'error':
            error_message = data.get('text', 'Unknown error')
            bot.edit_message_text(f"❌ Error from API: {error_message}", chat_id=message.chat.id, message_id=status_msg.message_id)
            return

        # 5. Extract the watermark-free video URL
        video_url = data.get('url')
        
        if not video_url:
            bot.edit_message_text(f"❌ Could not find the video URL. Raw data:\n{data}", chat_id=message.chat.id, message_id=status_msg.message_id)
            return
        
        # 6. Download the video to the server
        bot.edit_message_text("⏳ Downloading video to server...", chat_id=message.chat.id, message_id=status_msg.message_id)
        video_response = requests.get(video_url, timeout=30)
        video_response.raise_for_status()
        
        # 7. Upload to Telegram
        bot.edit_message_text("⏳ Uploading to Telegram...", chat_id=message.chat.id, message_id=status_msg.message_id)
        bot.send_video(
            chat_id=message.chat.id,
            video=video_response.content,
            supports_streaming=True
        )
        
        # Delete the "loading" message once finished
        bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)

    except requests.exceptions.Timeout:
        bot.edit_message_text("❌ Error: Connection timed out. The video might be too large.", chat_id=message.chat.id, message_id=status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ System Error: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)

def run_bot():
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception:
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
