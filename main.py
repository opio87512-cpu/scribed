import telebot
import os
import threading
import time
import yt_dlp
from flask import Flask

# Your Telegram Token
BOT_TOKEN = "8969647277:AAG4RC0IxDRLMwr_VIzU-z_3VxQZlUd9ubo"
bot = telebot.TeleBot(BOT_TOKEN)

# Web Server for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "TikTok yt-dlp Bot is online and monitoring."

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Send me a TikTok link and I will download the video for you!")

@bot.message_handler(func=lambda message: "tiktok.com" in message.text)
def handle_tiktok_link(message):
    url = message.text
    status_msg = bot.reply_to(message, "⏳ Processing with yt-dlp...")
    
    # Create a unique filename based on the chat and message ID so files don't overlap
    file_name = f"{message.chat.id}_{message.message_id}.mp4"
    
    # Configure yt-dlp settings
    ydl_opts = {
        'format': 'best',
        'outtmpl': file_name,
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        # 1. Extract and download the video to Render's disk
        bot.edit_message_text("⏳ Downloading video to server...", chat_id=message.chat.id, message_id=status_msg.message_id)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        # 2. Upload the local file to Telegram
        if os.path.exists(file_name):
            bot.edit_message_text("⏳ Uploading to Telegram...", chat_id=message.chat.id, message_id=status_msg.message_id)
            with open(file_name, 'rb') as video_file:
                bot.send_video(
                    chat_id=message.chat.id,
                    video=video_file,
                    supports_streaming=True
                )
            bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)
            
            # 3. Delete local file to save storage space
            os.remove(file_name)
        else:
            bot.edit_message_text("❌ Error: Download finished but the file was not found.", chat_id=message.chat.id, message_id=status_msg.message_id)
            
    except Exception as e:
        bot.edit_message_text(f"❌ System Error: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)
        # Ensure the broken file gets deleted if an error occurs
        if os.path.exists(file_name):
            os.remove(file_name)

def run_bot():
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except telebot.apihelper.ApiTelegramException as e:
            # If error 409 occurs (another instance is running), wait 15 seconds and try again
            if e.error_code == 409:
                print("Conflict error detected. Waiting for old instance to shut down...")
                time.sleep(15)
            else:
                time.sleep(5)
        except Exception:
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
