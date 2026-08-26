import os
import re
import time
import queue
import threading
import subprocess

import requests
import telebot
from telebot import types
from flask import Flask

# Your Telegram Bot Token
BOT_TOKEN = "8969647277:AAF3jTCal-ZdqYqghm7ln0mrcTZUcTg3o6U"

# threaded=False: telebot won't spawn an unbounded thread per update.
# We manage our own bounded worker pool below instead.
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

try:
    bot.remove_webhook()
    time.sleep(1)
except Exception:
    pass

app = Flask(__name__)


@app.route('/')
def home():
    return "TikTok Downloader is running."


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_TELEGRAM_BYTES = 50 * 1024 * 1024  # Telegram bot API upload limit
DOWNLOAD_DIR = "downloads"
MAX_WORKERS = 2
STATUS_EDIT_INTERVAL = 3  # seconds between progress message edits (Telegram throttling)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

job_queue = queue.Queue()
jobs = {}           # job_id -> dict with everything needed to process it
cancel_events = {}  # job_id -> threading.Event

_job_counter = 0
_job_counter_lock = threading.Lock()


def next_job_id():
    global _job_counter
    with _job_counter_lock:
        _job_counter += 1
        return _job_counter


# ---------------------------------------------------------------------------
# Safe filenames
# ---------------------------------------------------------------------------

def safe_filename(title, fallback="tiktok", max_len=60):
    if not title:
        title = fallback
    title = re.sub(r'#\S+', '', title)             # remove hashtags
    title = re.sub(r'[\\/:*?"<>|]', '', title)      # forbidden filesystem chars
    title = re.sub(r'\s+', ' ', title).strip()
    title = title.strip('. ')                        # no trailing dots/spaces
    if not title:
        title = fallback
    title = title[:max_len].strip('. ')
    return title or fallback


# ---------------------------------------------------------------------------
# TikWM metadata + direct media helpers
# ---------------------------------------------------------------------------

def fetch_tikwm_data(url):
    """Query TikWM for metadata + direct media URLs (video, audio, or images)."""
    tikwm_api = "https://www.tikwm.com/api/"
    for params in ({"url": url, "hd": 1}, {"url": url}):
        try:
            r = requests.get(tikwm_api, params=params, timeout=15)
            if r.status_code == 200:
                j = r.json()
                if j.get('code') == 0:
                    data = j.get('data', {}) or {}
                    if data:
                        return data
        except Exception:
            continue
    return {}


def extract_image_urls(data):
    """Normalize TikWM's various slideshow response shapes into a flat URL list."""
    urls = []
    images = data.get('images')
    if isinstance(images, list) and images:
        for img in images:
            if isinstance(img, str):
                urls.append(img)
            elif isinstance(img, dict):
                u = img.get('url') or img.get('src')
                if u:
                    urls.append(u)
        if urls:
            return urls

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


def probe_remote_size(url, timeout=15):
    """Range-probe a direct media URL to get its real byte size when TikWM
    doesn't report one."""
    headers = {"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"}
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=timeout)
        cr = r.headers.get('Content-Range')  # "bytes 0-0/1234567"
        r.close()
        if cr and '/' in cr:
            total = cr.rsplit('/', 1)[-1]
            if total.isdigit():
                return int(total)
        cl = r.headers.get('Content-Length')
        if r.status_code == 200 and cl and cl.isdigit():
            return int(cl)
    except Exception:
        pass
    return None


def build_video_candidates(data):
    """Rank possible video sources, proving size via TikWM fields or a Range probe."""
    raw = [
        ("HD", data.get('hdplay'), data.get('hd_size')),
        ("Standard", data.get('play'), data.get('size')),
        ("Watermarked", data.get('wmplay'), data.get('wm_size')),
    ]
    candidates = []
    for label, url, size in raw:
        if not url:
            continue
        if not size:
            size = probe_remote_size(url)
        candidates.append({"label": label, "url": url, "size": size})
    return candidates


def pick_best_fitting(candidates):
    fitting = [c for c in candidates if c['size'] and c['size'] <= MAX_TELEGRAM_BYTES]
    return fitting[0] if fitting else None


def pick_mp3_bitrate(duration_seconds, safety_ratio=0.9):
    """Pick the highest standard MP3 bitrate that should safely fit under the limit."""
    if not duration_seconds or duration_seconds <= 0:
        return 128
    max_bytes = MAX_TELEGRAM_BYTES * safety_ratio
    max_kbps = (max_bytes * 8) / (1000 * duration_seconds)
    for candidate in (320, 256, 192, 128, 96, 64, 48, 32):
        if candidate <= max_kbps:
            return candidate
    return 32


def download_with_progress(url, dest_path, cancel_event, chat_id, status_id, markup, label, timeout=60):
    """Stream a direct media URL to disk, editing the status message with
    progress (throttled) and bailing out if cancelled."""
    headers = {"User-Agent": "Mozilla/5.0"}
    last_edit = 0
    try:
        with requests.get(url, stream=True, headers=headers, timeout=timeout) as r:
            r.raise_for_status()
            total = int(r.headers.get('Content-Length') or 0)
            downloaded = 0
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if cancel_event.is_set():
                        return False
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_edit > STATUS_EDIT_INTERVAL:
                        last_edit = now
                        if total:
                            pct = downloaded * 100 // total
                            text = f"⏳ {label}... {pct}% ({downloaded // 1024 // 1024}MB/{total // 1024 // 1024}MB)"
                        else:
                            text = f"⏳ {label}... {downloaded // 1024 // 1024}MB"
                        try:
                            bot.edit_message_text(text, chat_id, status_id, reply_markup=markup)
                        except Exception:
                            pass
        return True
    except Exception:
        return False


def send_slideshow(chat_id, images):
    media_group = [types.InputMediaPhoto(u) for u in images[:10]]
    try:
        bot.send_media_group(chat_id, media_group)
    except Exception:
        for u in images[:10]:
            try:
                bot.send_photo(chat_id, u)
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Send me a TikTok link — video, photo slideshow, or audio, your choice.")


@bot.message_handler(func=lambda m: m.text and "tiktok.com" in m.text)
def handle_link(message):
    raw = message.text.strip()
    url = raw.split('?')[0]
    status = bot.reply_to(message, "⏳ Fetching info...")

    data = fetch_tikwm_data(url)
    if not data:
        bot.edit_message_text(
            "❌ Couldn't fetch this link. It may be private, deleted, or TikWM is having issues.",
            message.chat.id, status.message_id
        )
        return

    images = extract_image_urls(data)
    if images:
        bot.edit_message_text("📸 Photo slideshow detected! Sending images...", message.chat.id, status.message_id)
        send_slideshow(message.chat.id, images)
        try:
            bot.delete_message(message.chat.id, status.message_id)
        except Exception:
            pass
        return

    candidates = build_video_candidates(data)
    best = pick_best_fitting(candidates)
    title = safe_filename(data.get('title'))
    duration = data.get('duration') or 0

    if not best:
        known_sizes = [c['size'] for c in candidates if c['size']]
        if known_sizes:
            mb = min(known_sizes) / (1024 * 1024)
            bot.edit_message_text(
                f"❌ Too large for Telegram's 50MB bot limit (smallest available: {mb:.1f} MB).",
                message.chat.id, status.message_id
            )
        else:
            bot.edit_message_text(
                "❌ Couldn't reliably determine this video's file size, so I won't risk a failed "
                "upload. Try again later.",
                message.chat.id, status.message_id
            )
        return

    job_id = next_job_id()
    jobs[job_id] = {
        "chat_id": message.chat.id,
        "status_message_id": status.message_id,
        "url": url,
        "data": data,
        "best_video": best,
        "title": title,
        "duration": duration,
    }

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🎬 Video", callback_data=f"dl:{job_id}:video"),
        types.InlineKeyboardButton("📄 Document", callback_data=f"dl:{job_id}:document"),
        types.InlineKeyboardButton("🎵 Audio", callback_data=f"dl:{job_id}:audio"),
    )
    markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{job_id}"))

    size_note = f" (~{best['size'] / (1024 * 1024):.1f} MB)" if best['size'] else ""
    bot.edit_message_text(
        f"✅ Found: {title}{size_note}\nHow would you like it?",
        message.chat.id, status.message_id, reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel:"))
def on_cancel(call):
    job_id = int(call.data.split(":")[1])
    ev = cancel_events.get(job_id)
    if ev:
        ev.set()
    job = jobs.get(job_id)
    if job:
        try:
            bot.edit_message_text("🚫 Cancelled.", job['chat_id'], job['status_message_id'])
        except Exception:
            pass
    bot.answer_callback_query(call.id, "Cancelled")


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl:"))
def on_choose(call):
    _, job_id_str, mode = call.data.split(":")
    job_id = int(job_id_str)
    job = jobs.get(job_id)
    if not job:
        bot.answer_callback_query(call.id, "This request expired, send the link again.")
        return

    bot.answer_callback_query(call.id, "Queued")
    cancel_events[job_id] = threading.Event()

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{job_id}"))
    try:
        bot.edit_message_text("⏳ Queued...", job['chat_id'], job['status_message_id'], reply_markup=markup)
    except Exception:
        pass

    job_queue.put((job_id, mode))


# ---------------------------------------------------------------------------
# Worker pool
# ---------------------------------------------------------------------------

def process_job(job_id, mode):
    job = jobs.get(job_id)
    if not job:
        return

    chat_id = job['chat_id']
    status_id = job['status_message_id']
    ev = cancel_events.get(job_id, threading.Event())
    title = job['title']
    base = os.path.join(DOWNLOAD_DIR, f"{chat_id}_{job_id}")
    video_path = base + ".mp4"
    audio_path = base + ".mp3"
    src_path = base + ".src"

    cancel_markup = types.InlineKeyboardMarkup()
    cancel_markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{job_id}"))

    def edit_status(text, markup=None):
        try:
            bot.edit_message_text(text, chat_id, status_id, reply_markup=markup)
        except Exception:
            pass

    try:
        if mode in ("video", "document"):
            ok = download_with_progress(
                job['best_video']['url'], video_path, ev,
                chat_id, status_id, cancel_markup, "Downloading video"
            )
            if ev.is_set():
                edit_status("🚫 Cancelled.")
                return
            if not ok or not os.path.exists(video_path):
                edit_status("❌ Download failed.")
                return

            edit_status("⏳ Uploading...", cancel_markup)
            with open(video_path, 'rb') as f:
                if mode == "video":
                    bot.send_video(chat_id, f, supports_streaming=True, caption=title[:1024], timeout=120)
                else:
                    bot.send_document(chat_id, f, caption=title[:1024],
                                       visible_file_name=f"{title}.mp4", timeout=120)

        elif mode == "audio":
            source_url = job['data'].get('music') or job['best_video']['url']
            ok = download_with_progress(
                source_url, src_path, ev,
                chat_id, status_id, cancel_markup, "Downloading audio"
            )
            if ev.is_set():
                edit_status("🚫 Cancelled.")
                return
            if not ok:
                edit_status("❌ Download failed.")
                return

            bitrate = pick_mp3_bitrate(job.get('duration'))
            edit_status(f"🎵 Converting audio ({bitrate}kbps)...", cancel_markup)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", src_path, "-vn", "-acodec", "libmp3lame",
                     "-b:a", f"{bitrate}k", audio_path],
                    check=True, capture_output=True, timeout=180,
                )
            except FileNotFoundError:
                edit_status("⚠️ ffmpeg isn't installed on the server — can't extract audio.")
                return
            except subprocess.CalledProcessError:
                edit_status("❌ Audio conversion failed.")
                return

            if ev.is_set():
                edit_status("🚫 Cancelled.")
                return

            edit_status("⏳ Uploading audio...", cancel_markup)
            with open(audio_path, 'rb') as f:
                bot.send_audio(chat_id, f, title=title[:64], timeout=120)

        try:
            bot.delete_message(chat_id, status_id)
        except Exception:
            pass

    except Exception as e:
        edit_status(f"❌ Error: {e}")
    finally:
        for p in (video_path, audio_path, src_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        jobs.pop(job_id, None)
        cancel_events.pop(job_id, None)


def worker_loop():
    while True:
        job_id, mode = job_queue.get()
        try:
            process_job(job_id, mode)
        except Exception:
            pass
        finally:
            job_queue.task_done()


for _ in range(MAX_WORKERS):
    threading.Thread(target=worker_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def run_bot():
    backoff = 15
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 409:
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            time.sleep(5)
        except Exception:
            time.sleep(5)
        backoff = 15


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
