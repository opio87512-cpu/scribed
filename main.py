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


def extract_image_urls(data):
    """
    TikWM's API returns slideshow images in a couple of different shapes
    depending on the post/version. This normalizes all of them into a
    flat list of direct image URLs.
    """
    urls = []

    # Shape 1: data['images'] = ["https://...", "https://...", ...]
    images = data.get('images')
    if isinstance(images, list) and images:
        for img in images:
            if isinstance(img, str):
                urls.append(img)
            elif isinstance(img, dict):
                # occasionally each item is a dict with a url field
                u = img.get('url') or img.get('src')
                if u:
                    urls.append(u)
        if urls:
            return urls

    # Shape 2: data['image_post_info']['images'] = [{ "display_image": { "url_list": [...] } }, ...]
    image_post = data.get('image_post_info') or data.get('images_post_info') or {}
    if isinstance(image_post, dict):
        for img in image_post.get('images', []):
            if not isinstance(img, dict):
                continue
            display = img.get('display_image') or img.get('image') or {}
            url_list = display.get('url_list') or []
            if url_list:
                urls.append(url_list[0])

    return urls


def fetch_tikwm_data(url):
    """
    Query TikWM, trying a couple of param combinations since the 'hd' flag
    (meant for video) can sometimes interfere with slideshow parsing.
    """
    tikwm_api = "https://www.tikwm.com/api/"
    for params in ({"url": url, "hd": 1}, {"url": url}):
        try:
            response = requests.get(tikwm_api, params=params, timeout=15)
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get('code') == 0:
                    data = resp_json.get('data', {}) or {}
                    if data:
                        return data
        except Exception:
            continue
    return {}


@bot.message_handler(func=lambda message: message.text and "tiktok.com" in message.text)
def handle_tiktok_link(message):
    raw_url = message.text
    url = raw_url.split('?')[0]  # Clean tracking data
    is_photo_url = "/photo/" in url

    status_msg = bot.reply_to(message, "⏳ Analyzing TikTok link...")
    file_name = None

    try:
        # Step 1: Check if it's a photo post using the free TikWM API
        data = fetch_tikwm_data(url)
        images = extract_image_urls(data)

        if images:
            bot.edit_message_text(
                "📸 Photo slideshow detected! Sending images...",
                chat_id=message.chat.id, message_id=status_msg.message_id
            )

            media_group = [InputMediaPhoto(img_url) for img_url in images[:10]]

            try:
                bot.send_media_group(message.chat.id, media_group)
            except Exception:
                # Fallback: some CDNs reject Telegram's fetch when batched.
                # Try sending them one at a time instead.
                sent_any = False
                for img_url in images[:10]:
                    try:
                        bot.send_photo(message.chat.id, img_url)
                        sent_any = True
                    except Exception:
                        continue
                if not sent_any:
                    raise

            bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)
            return

        if is_photo_url:
            # yt-dlp does not support TikTok's /photo/ URL format at all
            # (it throws "Unsupported URL"), so don't even try it here.
            bot.edit_message_text(
                "❌ This looks like a photo slideshow, but I couldn't extract the images "
                "from it right now. TikWM may be having trouble with this post — try again "
                "in a bit, or send the link again.",
                chat_id=message.chat.id, message_id=status_msg.message_id
            )
            return

        # Step 2: Fallback to yt-dlp for video extraction
        bot.edit_message_text(
            "🎥 Video detected! Processing with yt-dlp...",
            chat_id=message.chat.id, message_id=status_msg.message_id
        )

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
            bot.edit_message_text(
                "⏳ Uploading video to Telegram...",
                chat_id=message.chat.id, message_id=status_msg.message_id
            )
            with open(file_name, 'rb') as video_file:
                bot.send_video(
                    chat_id=message.chat.id,
                    video=video_file,
                    supports_streaming=True
                )
            bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)
            os.remove(file_name)
        else:
            bot.edit_message_text(
                "❌ Error: Could not download the video or photo. The link may be a slideshow "
                "post that TikWM couldn't parse, or the video is unavailable.",
                chat_id=message.chat.id, message_id=status_msg.message_id
            )

    except Exception as e:
        try:
            bot.edit_message_text(f"❌ System Error: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)
        except Exception:
            pass
        if file_name and os.path.exists(file_name):
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
