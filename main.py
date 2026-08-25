import telebot
import os
import requests
import threading
import time
from flask import Flask

# 1. Your Telegram Bot Token is now directly in the file
BOT_TOKEN = "8969647277:AAG4RC0IxDRLMwr_VIzU-z_3VxQZlUd9ubo"
bot = telebot.TeleBot(BOT_TOKEN)

# Dummy web server to keep Render awake
app = Flask(__name__)

@app.route('/')
def home():
    return "TikTok Downloader Bot is running!"

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Welcome! Send me a TikTok link and I will fetch the video for you.")

# Listen for TikTok links
@bot.message_handler(func=lambda message: "tiktok.com" in message.text)
def handle_tiktok_link(message):
    url = message.text
    bot.reply_to(message, "TikTok link detected. Downloading video... please wait.")
    
    try:
        # ⚠️ Replace these with your specific TikTok API endpoint from RapidAPI
        api_url = "https://example-tiktok-downloader.p.rapidapi.com/download"
        querystring = {"url": url}
        
        # 2. You can also hardcode your RapidAPI key here if you want
        headers = {
            "X-RapidAPI-Key": "5759757fc4f740878f7ae4f373b97f6baf3c7068955",
            "X-RapidAPI-Host": "example-tiktok-downloader.p.rapidapi.com"
        }
        
        # Request the video link
        response = requests.get(api_url, headers=headers, params=querystring)
        data = response.json()
        
        # Check for the video URL (the exact word 'video_url' depends on your RapidAPI provider)
        if 'video_url' in data:
            direct_video_url = data['video_url']
            
            # Download the video into memory
            video_bytes = requests.get(direct_video_url).content
            
            # Send the video to Telegram
            bot.send_video(
                chat_id=message.chat.id,
                video=video_bytes,
                supports_streaming=True
            )
        else:
            bot.reply_to(message, "Could not extract the video from this link.")

    except Exception as e:
        bot.reply_to(message, f"System error: {str(e)}")

def run_bot():
    while True:
        try:
            bot.infinity_polling()
        except Exception:
            time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
