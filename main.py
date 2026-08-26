import os
import re
import time
import queue
import logging
import threading
import subprocess

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import telebot
from telebot import types
from flask import Flask, request

# ---------------------------------------------------------------------------
# Logging — timestamps + levels, replaces bare print() so Render's log
# viewer shows exactly what failed and when.
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tiktok_bot")

# ---------------------------------------------------------------------------
# Your Telegram Bot Token
# ---------------------------------------------------------------------------

BOT_TOKEN = "8969647277:AAF3jTCal-ZdqYqghm7ln0mrcTZUcTg3o6U"

# threaded=False: telebot won't spawn an unbounded thread per update.
# We manage our own bounded worker pool below instead.
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Shared HTTP session with automatic retries on connection errors, timeouts,
# and 429/5xx responses — covers transient network blips without each call
# site needing its own retry loop.
# ---------------------------------------------------------------------------

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

http_session = requests.Session()
_retry = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
http_session.mount("https://", HTTPAdapter(max_retries=_retry))
http_session.mount("http://", HTTPAdapter(max_retries=_retry))
http_session.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
})


def safe_bot_call(fn, *args, **kwargs):
    """Call a telebot method, swallowing the extremely common and harmless
    'message is not modified' error, logging anything else, and never
    raising into a handler or worker thread."""
    try:
        return fn(*args, **kwargs)
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            return None
        log.warning("Telegram API error in %s: %s", getattr(fn, "__name__", fn), e)
    except Exception as e:
        log.warning("Unexpected error in %s: %s", getattr(fn, "__name__", fn), e)
    return None


@app.route('/')
def home():
    return "TikTok Downloader is running."


@app.route('/health')
def health():
    return {"status": "ok", "queued_jobs": job_queue.qsize(), "active_jobs": len(jobs)}


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

def resolve_tiktok_url(url, timeout=10):
    """Follow redirects on shortened TikTok links (vm.tiktok.com, vt.tiktok.com,
    /t/<slug>) so TikWM gets a canonical URL instead of one it has to resolve
    itself — some shortlink forms trip TikWM's anti-bot layer more than a
    fully resolved link does."""
    try:
        r = http_session.head(url, allow_redirects=True, timeout=timeout)
        if r.url and r.url != url:
            return r.url
    except Exception:
        pass
    try:
        with http_session.get(url, allow_redirects=True, timeout=timeout, stream=True) as r:
            final = r.url
        return final or url
    except Exception as e:
        log.warning("Failed to resolve short URL %s: %s", url, e)
        return url


def fetch_tikwm_data(url, retries=2):
    """Query TikWM for metadata + direct media URLs (video, audio, or images).
    Tries both known hosts and both hd param variants, retries briefly on
    rate-limiting, and logs the actual failure reason server-side."""
    hosts = ("https://www.tikwm.com/api/", "https://tikwm.com/api/")
    param_variants = ({"url": url, "hd": 1}, {"url": url})

    last_error = None
    saw_403 = False
    for attempt in range(retries + 1):
        for host in hosts:
            for params in param_variants:
                try:
                    r = http_session.get(host, params=params, timeout=15)
                    if r.status_code == 403:
                        saw_403 = True
                        last_error = f"HTTP 403 from {host} (likely blocked/WAF)"
                        continue
                    if r.status_code == 429:
                        last_error = "rate limited (429)"
                        continue
                    if r.status_code != 200:
                        last_error = f"HTTP {r.status_code} from {host}: {r.text[:200]!r}"
                        continue
                    try:
                        j = r.json()
                    except ValueError:
                        last_error = f"non-JSON response from {host}: {r.text[:200]!r}"
                        continue
                    if j.get('code') == 0:
                        data = j.get('data', {}) or {}
                        if data:
                            return data
                        last_error = "empty data in response"
                    else:
                        last_error = f"API code {j.get('code')}: {j.get('msg')}"
                except requests.exceptions.RequestException as e:
                    last_error = f"network error: {e}"
        if attempt < retries:
            time.sleep(2)

    if saw_403:
        log.error("TikWM blocked the request for %s (403 on all attempts) — likely a "
                   "server-level IP block, not transient. Last detail: %s", url, last_error)
    else:
        log.error("TikWM fetch failed for %s: %s", url, last_error)
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
    try:
        r = http_session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=timeout)
        cr = r.headers.get('Content-Range')  # "bytes 0-0/1234567"
        r.close()
        if cr and '/' in cr:
            total = cr.rsplit('/', 1)[-1]
            if total.isdigit():
                return int(total)
        cl = r.headers.get('Content-Length')
        if r.status_code == 200 and cl and cl.isdigit():
            return int(cl)
    except Exception as e:
        log.warning("Size probe failed for %s: %s", url, e)
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
    last_edit = 0
    try:
        with http_session.get(url, stream=True, timeout=timeout) as r:
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
                        safe_bot_call(bot.edit_message_text, text, chat_id, status_id, reply_markup=markup)
        return True
    except Exception as e:
        log.warning("Download failed for %s: %s", url, e)
        return False


def send_slideshow(chat_id, images):
    media_group = [types.InputMediaPhoto(u) for u in images[:10]]
    if safe_bot_call(bot.send_media_group, chat_id, media_group) is None:
        for u in images[:10]:
            safe_bot_call(bot.send_photo, chat_id, u)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    safe_bot_call(bot.reply_to, message, "Send me a TikTok link — video, photo slideshow, or audio, your choice.")


@bot.message_handler(func=lambda m: m.text and "tiktok.com" in m.text)
def handle_link(message):
    try:
        _handle_link_impl(message)
    except Exception:
        log.exception("Unhandled error in handle_link for chat %s", message.chat.id)
        safe_bot_call(bot.reply_to, message, "❌ Something went wrong processing that link. Please try again.")


def _handle_link_impl(message):
    raw = message.text.strip()
    url = raw.split('?')[0]
    status = safe_bot_call(bot.reply_to, message, "⏳ Fetching info...")
    if status is None:
        return  # couldn't even send the first reply; nothing more we can do

    # Shortened links (vm./vt.tiktok.com, /t/<slug>) get resolved to their
    # canonical form first — TikWM handles those more reliably.
    if "/t/" in url or "vm.tiktok.com" in url or "vt.tiktok.com" in url:
        url = resolve_tiktok_url(url)

    data = fetch_tikwm_data(url)
    if not data:
        safe_bot_call(
            bot.edit_message_text,
            "❌ Couldn't fetch this link. It may be private, deleted, or TikWM is having issues. "
            "Check the bot's logs for the specific reason if this keeps happening.",
            message.chat.id, status.message_id
        )
        return

    images = extract_image_urls(data)
    if images:
        safe_bot_call(bot.edit_message_text, "📸 Photo slideshow detected! Sending images...",
                       message.chat.id, status.message_id)
        send_slideshow(message.chat.id, images)
        safe_bot_call(bot.delete_message, message.chat.id, status.message_id)
        return

    candidates = build_video_candidates(data)
    best = pick_best_fitting(candidates)
    title = safe_filename(data.get('title'))
    duration = data.get('duration') or 0

    if not best:
        known_sizes = [c['size'] for c in candidates if c['size']]
        if known_sizes:
            mb = min(known_sizes) / (1024 * 1024)
            safe_bot_call(
                bot.edit_message_text,
                f"❌ Too large for Telegram's 50MB bot limit (smallest available: {mb:.1f} MB).",
                message.chat.id, status.message_id
            )
        else:
            safe_bot_call(
                bot.edit_message_text,
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
    safe_bot_call(
        bot.edit_message_text,
        f"✅ Found: {title}{size_note}\nHow would you like it?",
        message.chat.id, status.message_id, reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel:"))
def on_cancel(call):
    try:
        job_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        safe_bot_call(bot.answer_callback_query, call.id, "Invalid request.")
        return

    ev = cancel_events.get(job_id)
    if ev:
        ev.set()
    job = jobs.get(job_id)
    if job:
        safe_bot_call(bot.edit_message_text, "🚫 Cancelled.", job['chat_id'], job['status_message_id'])
    safe_bot_call(bot.answer_callback_query, call.id, "Cancelled")


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl:"))
def on_choose(call):
    try:
        _, job_id_str, mode = call.data.split(":")
        job_id = int(job_id_str)
    except (IndexError, ValueError):
        safe_bot_call(bot.answer_callback_query, call.id, "Invalid request.")
        return

    job = jobs.get(job_id)
    if not job:
        safe_bot_call(bot.answer_callback_query, call.id, "This request expired, send the link again.")
        return

    safe_bot_call(bot.answer_callback_query, call.id, "Queued")
    cancel_events[job_id] = threading.Event()

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{job_id}"))
    safe_bot_call(bot.edit_message_text, "⏳ Queued...", job['chat_id'], job['status_message_id'], reply_markup=markup)

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
        safe_bot_call(bot.edit_message_text, text, chat_id, status_id, reply_markup=markup)

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
            try:
                with open(video_path, 'rb') as f:
                    if mode == "video":
                        bot.send_video(chat_id, f, supports_streaming=True, caption=title[:1024], timeout=120)
                    else:
                        bot.send_document(chat_id, f, caption=title[:1024],
                                           visible_file_name=f"{title}.mp4", timeout=120)
            except telebot.apihelper.ApiTelegramException as e:
                log.warning("Upload failed for job %s: %s", job_id, e)
                edit_status(f"❌ Telegram rejected the upload: {e}")
                return

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
            except subprocess.TimeoutExpired:
                log.warning("ffmpeg timed out for job %s", job_id)
                edit_status("❌ Audio conversion timed out.")
                return
            except subprocess.CalledProcessError as e:
                log.warning("ffmpeg failed for job %s: %s", job_id, e.stderr[:300] if e.stderr else e)
                edit_status("❌ Audio conversion failed.")
                return

            if ev.is_set():
                edit_status("🚫 Cancelled.")
                return

            edit_status("⏳ Uploading audio...", cancel_markup)
            try:
                with open(audio_path, 'rb') as f:
                    bot.send_audio(chat_id, f, title=title[:64], timeout=120)
            except telebot.apihelper.ApiTelegramException as e:
                log.warning("Audio upload failed for job %s: %s", job_id, e)
                edit_status(f"❌ Telegram rejected the upload: {e}")
                return

        safe_bot_call(bot.delete_message, chat_id, status_id)

    except Exception as e:
        log.exception("Unhandled error in process_job %s (%s)", job_id, mode)
        edit_status(f"❌ Unexpected error: {e}")
    finally:
        for p in (video_path, audio_path, src_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    log.warning("Failed to clean up %s: %s", p, e)
        jobs.pop(job_id, None)
        cancel_events.pop(job_id, None)


def worker_loop():
    while True:
        job_id, mode = job_queue.get()
        try:
            process_job(job_id, mode)
        except Exception:
            log.exception("Worker crashed processing job %s (%s)", job_id, mode)
        finally:
            job_queue.task_done()


for _ in range(MAX_WORKERS):
    threading.Thread(target=worker_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Transport: webhook (preferred) with polling fallback
#
# Polling (getUpdates) inherently conflicts if more than one process talks
# to Telegram at once — that's the source of the 409 errors. Webhooks avoid
# this entirely: Telegram pushes updates to our own HTTPS endpoint instead,
# so there's no shared "who gets the next update" race. Render assigns
# every web service a public HTTPS URL in RENDER_EXTERNAL_URL, so we use
# that when present and fall back to polling only if it's missing (e.g.
# running locally).
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = BOT_TOKEN.split(":")[0]  # not secret-secret, but unguessable enough to keep randoms out
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL")


@app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception:
        log.exception("Failed to process incoming webhook update")
    return '', 200


def setup_webhook():
    if not PUBLIC_URL:
        log.info("RENDER_EXTERNAL_URL not set; falling back to polling.")
        return False
    try:
        bot.remove_webhook()
        time.sleep(1)
        full_url = PUBLIC_URL.rstrip('/') + WEBHOOK_PATH
        bot.set_webhook(url=full_url)
        log.info("Webhook set to %s", full_url)
        return True
    except Exception:
        log.exception("Failed to set webhook, falling back to polling.")
        return False


def run_bot_polling():
    backoff = 15
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 409:
                log.warning("409 conflict — another instance is polling. Backing off %ss.", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            log.warning("Telegram API error while polling: %s", e)
            time.sleep(5)
        except Exception:
            log.exception("Unexpected error in polling loop")
            time.sleep(5)
        backoff = 15


if __name__ == "__main__":
    if not setup_webhook():
        try:
            bot.remove_webhook()
            time.sleep(1)
        except Exception:
            pass
        threading.Thread(target=run_bot_polling, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
