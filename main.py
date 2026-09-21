import os
import io
import csv
import json
import base64
import hmac
import hashlib
import time
import threading
import logging
import html as html_lib
import tempfile
from queue import Queue
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

import requests
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    InputMediaDocument,
    InputMediaPhoto,
)
from flask import Flask, request, jsonify, Response, stream_with_context, send_file
from flask_cors import CORS

# ==========================================================================
#  CONFIGURATION
# ==========================================================================
TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ.get("GITHUB_REPO", "opio87512-cpu/scribed")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/contents"

ADMIN_IDS = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "8429521561,8244142809").split(",")
    if x.strip()
]

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS", "https://opio87512-cpu.github.io"
    ).split(",")
    if o.strip()
]

WEBAPP_URL = os.environ.get(
    "WEBAPP_URL", "https://opio87512-cpu.github.io/scribed/"
)

# --- NEW: Google Gemini API Key for AI Study Buddy ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# ==========================================================================
#  LOGGING
# ==========================================================================
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
log = logging.getLogger("astu")

# ==========================================================================
#  FLASK + CORS
# ==========================================================================
app = Flask(__name__)
CORS(app, resources={r"/api/*": {
    "origins": ALLOWED_ORIGINS,
    "expose_headers": ["Content-Range", "Accept-Ranges", "Content-Length"],
}})

# ==========================================================================
#  BOT
# ==========================================================================
bot = telebot.TeleBot(TOKEN, parse_mode=None)

# ==========================================================================
#  GITHUB MUTEX + READ CACHE
# ==========================================================================
_gh_lock = threading.RLock()
_read_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 60

# ==========================================================================
#  FILE NAMES
# ==========================================================================
DATA_FILE = "materials.json"
VIDEOS_FILE = "videos.json"
SUBS_FILE = "subs.json"
EXAMS_FILE = "exams.json"
NEWS_FILE = "news.json"
EVENTS_FILE = "global_events.json"
FAVS_FILE = "favorites.json"
STATS_FILE = "stats.json"
REQUESTS_FILE = "requests.json"
USERS_FILE = "users.json"


# ==========================================================================
#  TIME HELPERS
# ==========================================================================
def _now_iso():
    """Current UTC time as ISO string (machine-friendly, sortable)."""
    return datetime.utcnow().isoformat(timespec="seconds")


def _now_pretty():
    """Current UTC time as a human-readable string."""
    return datetime.utcnow().strftime("%b %d, %Y - %H:%M")


def _parse_iso(s):
    """Best-effort ISO / legacy string parser. Returns datetime or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%b %d, %Y - %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


# ==========================================================================
#  SECURITY HELPERS
# ==========================================================================
def escape_md(text):
    return html_lib.escape(str(text if text is not None else ""))


def verify_init_data(init_data, max_age=86400):
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(
        b"WebAppData", TOKEN.encode(), hashlib.sha256
    ).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        return None

    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        return None
    if time.time() - auth_date > max_age:
        return None

    try:
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def get_auth_user():
    init_data = None
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            init_data = body.get("initData")
    if not init_data:
        init_data = request.form.get("initData")
    if not init_data:
        init_data = request.headers.get("X-Telegram-Init-Data")
    user = verify_init_data(init_data)
    if user:
        try:
            threading.Thread(
                target=register_user, args=(user, "webapp"), daemon=True
            ).start()
        except Exception:
            pass
    return user


def is_admin(user):
    return user and int(user.get("id", 0)) in ADMIN_IDS


# ==========================================================================
#  USER TRACKING + ANALYTICS
# ==========================================================================
def register_user(user, source="bot"):
    """
    Register or refresh a user in users.json.

    Stores: user_id, username, full_name, joined_at, last_active, active.
    Safe to call on every interaction — cheap and idempotent.
    """
    if not user or not user.get("id"):
        return
    uid = str(user["id"])
    now = _now_iso()

    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full_name = (first + " " + last).strip() or user.get("username") or f"User {uid}"
    username = user.get("username") or ""

    def mutator(data):
        if not isinstance(data, dict):
            data = {}
        existing = data.get(uid, {})
        data[uid] = {
            "user_id": uid,
            "username": username or existing.get("username", ""),
            "first_name": first or existing.get("first_name", ""),
            "last_name": last or existing.get("last_name", ""),
            "full_name": full_name or existing.get("full_name", ""),
            "joined_at": existing.get("joined_at") or existing.get("joined") or now,
            "last_active": now,
            "active": True,
            "source": source,
        }
        return True

    try:
        update_json(USERS_FILE, mutator)
    except Exception:
        log.exception("register_user failed for %s", uid)


def touch_user(uid, source="interaction"):
    """
    Update last_active for a user without touching other fields.
    Cheap enough to call on every bot interaction.
    """
    uid = str(uid)
    now = _now_iso()

    def mutator(data):
        if not isinstance(data, dict):
            return False
        if uid not in data:
            return False
        data[uid]["last_active"] = now
        data[uid]["active"] = True
        data[uid]["source"] = source
        return True

    try:
        update_json(USERS_FILE, mutator)
    except Exception:
        pass


def mark_user_inactive(uid):
    uid = str(uid)

    def mutator(data):
        if isinstance(data, dict) and uid in data:
            data[uid]["active"] = False
            return True
        return False

    try:
        update_json(USERS_FILE, mutator)
    except Exception:
        log.exception("mark_user_inactive failed for %s", uid)


def get_active_users():
    """Return a list of active user chat_ids (ints) for broadcasting."""
    data = load_json(USERS_FILE)
    if not isinstance(data, dict):
        return []
    return [
        int(uid) for uid, info in data.items()
        if isinstance(info, dict) and info.get("active", True)
    ]


def get_user_stats():
    """Return (total, active, inactive) counts."""
    data = load_json(USERS_FILE)
    if not isinstance(data, dict):
        return 0, 0, 0
    total = len(data)
    active = sum(1 for v in data.values() if isinstance(v, dict) and v.get("active", True))
    return total, active, total - active


def get_user_analytics():
    """
    Returns a rich analytics dict:
      {
        total, active_flag, inactive_flag,
        active_today, active_7d, active_30d,
        new_today, new_7d,
        recent: [list of most recent joins with pretty info]
      }
    """
    data = load_json(USERS_FILE)
    if not isinstance(data, dict):
        data = {}

    now = datetime.utcnow()
    cutoff_today = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    total = len(data)
    active_flag = 0
    inactive_flag = 0
    active_today = 0
    active_7d = 0
    active_30d = 0
    new_today = 0
    new_7d = 0

    entries = []
    for uid, info in data.items():
        if not isinstance(info, dict):
            continue
        if info.get("active", True):
            active_flag += 1
        else:
            inactive_flag += 1

        last = _parse_iso(info.get("last_active"))
        joined = _parse_iso(info.get("joined_at") or info.get("joined"))

        if last:
            if last >= cutoff_today:
                active_today += 1
            if last >= cutoff_7d:
                active_7d += 1
            if last >= cutoff_30d:
                active_30d += 1

        if joined:
            if joined >= cutoff_today:
                new_today += 1
            if joined >= cutoff_7d:
                new_7d += 1

        entries.append({
            "user_id": uid,
            "username": info.get("username", ""),
            "full_name": info.get("full_name") or (
                (info.get("first_name", "") + " " + info.get("last_name", "")).strip()
            ) or "Unknown",
            "joined_at": info.get("joined_at") or info.get("joined") or "",
            "last_active": info.get("last_active") or "",
            "active": bool(info.get("active", True)),
            "joined_dt": joined,
        })

    entries.sort(key=lambda e: e["joined_dt"] or datetime.min, reverse=True)

    return {
        "total": total,
        "active_flag": active_flag,
        "inactive_flag": inactive_flag,
        "active_today": active_today,
        "active_7d": active_7d,
        "active_30d": active_30d,
        "new_today": new_today,
        "new_7d": new_7d,
        "recent": entries[:10],
        "all": entries,
    }


def build_users_csv():
    """Return the users.json content as a CSV string (UTF-8)."""
    analytics = get_user_analytics()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "user_id", "username", "full_name",
        "joined_at", "last_active", "active", "source",
    ])

    raw = load_json(USERS_FILE)
    if isinstance(raw, dict):
        for uid, info in raw.items():
            if not isinstance(info, dict):
                continue
            writer.writerow([
                info.get("user_id", uid),
                "@" + info["username"] if info.get("username") else "",
                info.get("full_name") or (
                    (info.get("first_name", "") + " " + info.get("last_name", "")).strip()
                ),
                info.get("joined_at") or info.get("joined") or "",
                info.get("last_active") or "",
                "yes" if info.get("active", True) else "no",
                info.get("source", ""),
            ])
    return buf.getvalue()


# ==========================================================================
#  GITHUB STORAGE
# ==========================================================================
def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _gh_request(method, filename, **kwargs):
    url = f"{GITHUB_API_BASE}/{filename}"
    return requests.request(
        method, url, headers=_github_headers(), timeout=15, **kwargs
    )


def _load_json_unlocked(filename):
    try:
        resp = _gh_request("GET", filename, params={"ref": GITHUB_BRANCH})
        if resp.status_code == 200:
            content_b64 = resp.json()["content"]
            decoded = base64.b64decode(content_b64).decode("utf-8")
            return json.loads(decoded) if decoded.strip() else {}
        if resp.status_code == 404:
            return {}
        log.warning(
            "GitHub load failed for %s: %s %s",
            filename, resp.status_code, resp.text[:200],
        )
        return {}
    except Exception as e:
        log.exception("GitHub load error for %s: %s", filename, e)
        return {}


def _save_json_unlocked(filename, data, retries=3):
    content_str = json.dumps(data, indent=4)
    encoded = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    for attempt in range(retries):
        try:
            sha = None
            get_resp = _gh_request(
                "GET", filename, params={"ref": GITHUB_BRANCH}
            )
            if get_resp.status_code == 200:
                sha = get_resp.json().get("sha")

            payload = {
                "message": f"Update {filename}",
                "content": encoded,
                "branch": GITHUB_BRANCH,
            }
            if sha:
                payload["sha"] = sha

            put_resp = _gh_request("PUT", filename, json=payload)
            if put_resp.status_code in (200, 201):
                return True
            if put_resp.status_code in (409, 422):
                log.warning(
                    "GitHub conflict on %s (attempt %d), retrying",
                    filename, attempt + 1,
                )
                time.sleep(0.5 * (attempt + 1))
                continue
            log.warning(
                "GitHub save failed for %s: %s %s",
                filename, put_resp.status_code, put_resp.text[:200],
            )
            return False
        except Exception as e:
            log.exception("GitHub save error for %s: %s", filename, e)
            time.sleep(0.5)
    return False


def _cache_get(filename):
    with _cache_lock:
        entry = _read_cache.get(filename)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
    return None


def _cache_set(filename, data):
    with _cache_lock:
        _read_cache[filename] = {"data": data, "ts": time.time()}


def _cache_invalidate(filename=None):
    with _cache_lock:
        if filename:
            _read_cache.pop(filename, None)
        else:
            _read_cache.clear()


def load_json(filename):
    cached = _cache_get(filename)
    if cached is not None:
        return cached
    with _gh_lock:
        data = _load_json_unlocked(filename)
    _cache_set(filename, data)
    return data


def save_json(filename, data):
    with _gh_lock:
        ok = _save_json_unlocked(filename, data)
    if ok:
        _cache_invalidate(filename)
    return ok


def update_json(filename, mutator, retries=3):
    with _gh_lock:
        for attempt in range(retries):
            data = _load_json_unlocked(filename)
            result = mutator(data)
            if _save_json_unlocked(filename, data):
                _cache_invalidate(filename)
                return result
            time.sleep(0.5 * (attempt + 1))
        return None


# ==========================================================================
#  BACKGROUND WORKERS
# ==========================================================================
_notify_queue = Queue()


def _notify_worker():
    while True:
        job = None
        try:
            job = _notify_queue.get()
            if job is None:
                continue
            chat_ids, text, markup = job
            sent = 0
            blocked = 0
            failed = 0

            for uid in chat_ids:
                try:
                    bot.send_message(
                        uid, text, parse_mode="HTML",
                        reply_markup=markup, disable_web_page_preview=True,
                    )
                    sent += 1
                    time.sleep(0.045)

                except telebot.apihelper.ApiTelegramException as e:
                    msg = str(e).lower()
                    if "too many requests" in msg or "retry" in msg or "429" in msg:
                        log.warning("Rate limited on uid=%s — backing off 5s", uid)
                        time.sleep(5)
                        try:
                            bot.send_message(
                                uid, text, parse_mode="HTML",
                                reply_markup=markup, disable_web_page_preview=True,
                            )
                            sent += 1
                        except Exception as retry_err:
                            log.warning("Retry failed for uid=%s: %s", uid, retry_err)
                            failed += 1
                    elif ("blocked" in msg or "chat not found" in msg
                          or "deactivated" in msg or "user is deactivated" in msg
                          or "forbidden" in msg):
                        blocked += 1
                        try:
                            threading.Thread(
                                target=mark_user_inactive,
                                args=(uid,),
                                daemon=True,
                            ).start()
                        except Exception:
                            pass
                    else:
                        failed += 1
                        log.warning("notify to %s failed: %s", uid, e)

                except Exception as e:
                    failed += 1
                    log.warning("notify to %s error: %s", uid, e)

            log.info(
                "notify sent=%d/%d blocked=%d failed=%d",
                sent, len(chat_ids), blocked, failed,
            )
        except Exception:
            log.exception("notify worker crashed")
        finally:
            if job is not None:
                try:
                    _notify_queue.task_done()
                except Exception:
                    pass


threading.Thread(target=_notify_worker, daemon=True, name="notify").start()


def enqueue_notify(chat_ids, text, markup=None):
    if not chat_ids:
        return
    _notify_queue.put((list(chat_ids), text, markup))


def notify_all_subscribers(title, date_str, link):
    try:
        all_subs = load_json(SUBS_FILE)
    except Exception:
        return
    notified = set()
    for _, uids in (all_subs or {}).items():
        for uid in uids:
            notified.add(str(uid))

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📰 Read Post", url=link))
    markup.row(InlineKeyboardButton(
        "🚀 Open App", web_app=WebAppInfo(url=WEBAPP_URL)
    ))
    text = (
        f"📢 <b>New Announcement</b>\n\n"
        f"📌 <b>{escape_md(title)}</b>\n"
        f"🗓 {escape_md(date_str)}"
    )
    enqueue_notify(notified, text, markup)


# ==========================================================================
#  NEW-UPLOAD BROADCAST  →  every registered user
# ==========================================================================
def _find_year_sem_for_course(course_code):
    for y, sems in CURRICULUM.items():
        for s, courses in sems.items():
            for c in courses:
                if c["code"] == course_code:
                    return y, s
    return "", ""


def broadcast_new_upload(file_data):
    try:
        active_users = get_active_users()
    except Exception:
        log.exception("broadcast_new_upload: failed to get active users")
        return

    if not active_users:
        log.info("broadcast_new_upload: no active users to notify")
        return

    course_code = file_data.get("course_code", "")
    course_name = file_data.get("course_name") or course_display(course_code) or course_code
    material_type = (file_data.get("material_type") or "").upper()
    title = file_data.get("title") or "Untitled"
    kind = file_data.get("kind", "material")

    year, semester = _find_year_sem_for_course(course_code)

    type_emoji = {
        "NOTE": "📝", "ASSIGNMENT": "📄",
        "MID": "📝", "MID EXAM": "📝",
        "FINAL": "📝", "FINAL EXAM": "📝",
        "TEST": "⏳", "VIDEO": "📺",
        "OUTLINE": "📋", "COURSE OUTLINE": "📋",
    }.get(material_type, "📁")

    lines = [
        "🔔 <b>NEW MATERIAL UPLOADED!</b> 📚",
        "",
        f"📖 <b>Course:</b> {escape_md(course_name)}",
    ]
    if year and semester:
        sem_label = "Semester I" if semester == "1" else "Semester II"
        lines.append(f"🎓 <b>Year/Semester:</b> Year {year} · {sem_label}")
    lines.append(f"📁 <b>Type:</b> {type_emoji} {escape_md(material_type)}")
    lines.append(f"📝 <b>Title:</b> {escape_md(title)}")
    lines.append("")
    lines.append("Tap below to access it directly in the portal! 👇")

    text = "\n".join(lines)

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(
            "📥 View Material",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )
    )

    log.info(
        "Broadcasting new %s '%s' (%s) → %d users",
        kind, title, course_code, len(active_users),
    )
    enqueue_notify(active_users, text, markup)


PENDING_VIDEOS = {}
PENDING_UPLOADS = {}
UPLOAD_STATES = {}
UPDATE_SESSIONS = {}


def _cleanup_worker():
    while True:
        time.sleep(300)
        now = time.time()
        for store in (PENDING_VIDEOS, PENDING_UPLOADS, UPDATE_SESSIONS):
            for key in list(store.keys()):
                entry = store.get(key)
                if isinstance(entry, dict) and now - entry.get("_created", now) > 3600:
                    store.pop(key, None)
        for key in list(UPLOAD_STATES.keys()):
            entry = UPLOAD_STATES.get(key)
            if isinstance(entry, dict) and now - entry.get("_created", now) > 3600:
                UPLOAD_STATES.pop(key, None)


threading.Thread(target=_cleanup_worker, daemon=True, name="cleanup").start()


def _stamp(d):
    d["_created"] = time.time()
    return d


# ==========================================================================
#  CURRICULUM
# ==========================================================================
CURRICULUM = {
    "2": {
        "2": [
            {"code": "ECEg2202", "title": "Electronic Circuit II", "cr": 4},
            {"code": "ECEg2204", "title": "Signals and System Analysis", "cr": 3},
            {"code": "EPCE2202", "title": "Electromagnetic Field", "cr": 3},
            {"code": "ECEg2208", "title": "Eng. Application Software", "cr": 1},
            {"code": "Math2103", "title": "Computational methods", "cr": 3},
            {"code": "Math2201", "title": "Linear Algebra", "cr": 3},
        ],
    },
    "3": {
        "1": [
            {"code": "ECEg3201", "title": "Digital Logic Design", "cr": 4},
            {"code": "EPCE3201", "title": "Network Analysis & Synthesis", "cr": 3},
            {"code": "ECEg3103", "title": "Probability & Random Proc.", "cr": 3},
            {"code": "ECEg3205", "title": "Digital Signal Processing", "cr": 3},
            {"code": "LART2002", "title": "Gen. Psychology & Life Skills", "cr": 3},
            {"code": "Phys2208", "title": "Applied Modern Physics", "cr": 3},
        ],
        "2": [
            {"code": "ECEg3202", "title": "Intro to Comm. Systems", "cr": 4},
            {"code": "Phys3202", "title": "Solid State Physics", "cr": 3},
            {"code": "LART1003", "title": "History of Ethiopia & the Horn", "cr": 3},
            {"code": "ECEg3306", "title": "Microelectronic Devices & Circuits", "cr": 3},
            {"code": "ECEg3318", "title": "Optoelectronics", "cr": 3},
            {"code": "CSEg2202", "title": "Object Oriented Programming", "cr": 3},
            {"code": "SEng4208", "title": "Intro to Artificial Intelligence", "cr": 3},
            {"code": "EPCE3304", "title": "Intro to Control Systems", "cr": 3},
            {"code": "EPCE3302", "title": "Intro to Electrical Machines", "cr": 3},
        ],
    },
}


def course_display(code):
    if not code:
        return ""
    for _, sems in CURRICULUM.items():
        for _, courses in sems.items():
            for c in courses:
                if c["code"] == code:
                    return f"{code} — {c['title']}"
    return code


# ==========================================================================
#  MATERIAL HELPERS
# ==========================================================================
def save_material_batch(course_code, mat_type, files, title):
    now = datetime.now().strftime("%b %d, %Y - %H:%M")

    def mutator(data):
        key = f"{course_code}_{mat_type}"
        data.setdefault(key, [])
        for f in files:
            data[key].append({
                "file_id": f["file_id"],
                "name": title if len(files) == 1
                        else f"{title} - {f['file_name']}",
                "content_type": f["content_type"],
                "title": title,
                "date_added": now,
                "opens": 0,
            })
        return True

    ok = update_json(DATA_FILE, mutator) is not None

    if ok:
        try:
            threading.Thread(
                target=broadcast_new_upload,
                args=({
                    "course_code": course_code,
                    "material_type": mat_type,
                    "title": title,
                    "kind": "material",
                },),
                daemon=True,
            ).start()
        except Exception:
            log.exception("Failed to schedule broadcast for new material")

    return ok


def delete_material_by_index(course_code, mat_type, index):
    removed_holder = {}

    def mutator(data):
        key = f"{course_code}_{mat_type}"
        arr = data.get(key, [])
        if 0 <= index < len(arr):
            removed_holder["item"] = arr.pop(index)
            if not arr:
                data.pop(key, None)
            return True
        return False

    ok = update_json(DATA_FILE, mutator)
    if ok:
        return removed_holder.get("item", {}).get("name", "File")
    return None


def add_approved_video(course_code, title, url):
    now = datetime.now().strftime("%b %d, %Y - %H:%M")

    def mutator(data):
        data.setdefault(course_code, []).append({
            "title": title, "url": url, "date_added": now, "opens": 0,
        })
        return True

    ok = update_json(VIDEOS_FILE, mutator) is not None

    if ok:
        try:
            threading.Thread(
                target=broadcast_new_upload,
                args=({
                    "course_code": course_code,
                    "material_type": "video",
                    "title": title,
                    "kind": "video",
                },),
                daemon=True,
            ).start()
        except Exception:
            log.exception("Failed to schedule broadcast for new video")

    return ok


def bump_stat(course_code, kind, name):
    def mutator(data):
        key = f"{kind}:{course_code}:{name}"
        data[key] = int(data.get(key, 0)) + 1
        return True

    update_json(STATS_FILE, mutator)


# ==========================================================================
#  INLINE KEYBOARDS
# ==========================================================================
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🚀 Open ASTU ECE Portal",
                             web_app=WebAppInfo(url=WEBAPP_URL)),
        InlineKeyboardButton("📤 Upload Material",
                             callback_data="main_upload"),
    )
    return markup


def year_keyboard(action):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Year II", callback_data=f"{action}y_2"),
        InlineKeyboardButton("Year III", callback_data=f"{action}y_3"),
    )
    markup.row(InlineKeyboardButton("⬅️ Back to Main Menu",
                                    callback_data="back_main"))
    return markup


def semester_keyboard(year, action):
    markup = InlineKeyboardMarkup()
    sems = sorted(CURRICULUM.get(year, {}).keys())
    buttons = []
    for s in sems:
        label = "Semester I" if s == "1" else "Semester II"
        buttons.append(InlineKeyboardButton(
            label, callback_data=f"{action}s_{year}_{s}"
        ))
    if buttons:
        markup.row(*buttons)
    else:
        markup.row(InlineKeyboardButton("⚠️ No semesters available",
                                        callback_data="ignore"))

    back_target = {
        "f": "main_find", "u": "main_upload", "a": "back_main",
        "d": "back_main", "v": "back_main", "p": "back_main",
    }.get(action, "back_main")
    markup.row(InlineKeyboardButton("⬅️ Back to Years", callback_data=back_target))
    return markup


def subject_keyboard(year, semester, action):
    markup = InlineKeyboardMarkup()
    courses = (CURRICULUM.get(year, {}) or {}).get(semester, [])
    if courses:
        for c in courses:
            markup.row(InlineKeyboardButton(
                c["title"], callback_data=f"{action}c_{c['code']}"
            ))
    else:
        markup.row(InlineKeyboardButton("⚠️ Subjects coming soon!",
                                        callback_data="ignore"))
    markup.row(InlineKeyboardButton("⬅️ Back to Semesters",
                                    callback_data=f"{action}y_{year}"))
    return markup


def material_type_keyboard(course_code, action):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📋 Course Outline", callback_data=f"{action}m_{course_code}_outline"),
    )
    markup.row(
        InlineKeyboardButton("📝 Note", callback_data=f"{action}m_{course_code}_note"),
        InlineKeyboardButton("📄 Assignment", callback_data=f"{action}m_{course_code}_assignment"),
    )
    markup.row(
        InlineKeyboardButton("📝 Mid Exam", callback_data=f"{action}m_{course_code}_mid"),
        InlineKeyboardButton("📝 Final Exam", callback_data=f"{action}m_{course_code}_final"),
    )
    markup.row(InlineKeyboardButton("⏳ Test", callback_data=f"{action}m_{course_code}_test"))
    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    return markup


def finish_upload_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("✅ Finish Upload", callback_data="finish_upload"))
    return markup


def build_delete_list(course_code, material_type):
    materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
    markup = InlineKeyboardMarkup()
    if materials:
        for idx, item in enumerate(materials):
            markup.row(InlineKeyboardButton(
                f"❌ Delete: {item['name']}",
                callback_data=f"delitem_{course_code}_{material_type}_{idx}",
            ))
        text = (f"Select the file you want to delete for "
                f"{course_display(course_code)} ({material_type.upper()}):")
    else:
        text = f"✅ No files remain for {course_display(course_code)} ({material_type.upper()})."
    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    return text, markup


def group_by_title(course_code, material_type):
    materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
    groups = {}
    for idx, item in enumerate(materials):
        title = item.get("title") or item.get("name") or "Untitled"
        groups.setdefault(title, []).append(idx)
    return groups, materials


def build_update_folder_list(course_code, material_type):
    groups, _ = group_by_title(course_code, material_type)
    markup = InlineKeyboardMarkup()
    if groups:
        for title, indices in groups.items():
            sess_id = os.urandom(4).hex()
            UPDATE_SESSIONS[sess_id] = _stamp({
                "course_code": course_code,
                "material_type": material_type,
                "title": title,
                "indices": indices,
            })
            markup.row(InlineKeyboardButton(
                f"📁 {title} ({len(indices)} file(s))",
                callback_data=f"updfolder_{sess_id}",
            ))
        text = (f"Select the folder you want to UPDATE for "
                f"{course_display(course_code)} ({material_type.upper()}):")
    else:
        text = f"✅ No files found for {course_display(course_code)} ({material_type.upper()}) to update."
    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    return text, markup


def build_update_folder_detail(sess_id):
    sess = UPDATE_SESSIONS.get(sess_id)
    if not sess:
        return "⚠️ This update session has expired. Please run /updatefile again.", None

    data = load_json(DATA_FILE)
    key = f"{sess['course_code']}_{sess['material_type']}"
    materials = data.get(key, [])

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔁 Replace Entire Folder",
                                    callback_data=f"updwhole_{sess_id}"))
    valid = [i for i in sess["indices"] if i < len(materials)]
    for pos, abs_idx in enumerate(valid):
        item = materials[abs_idx]
        markup.row(InlineKeyboardButton(
            f"✏️ Update: {item.get('name', 'File')}",
            callback_data=f"upditem_{sess_id}_{pos}",
        ))
    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    text = (
        f"📁 <b>{escape_md(sess['title'])}</b>\n"
        f"Course: {escape_md(course_display(sess['course_code']))} • "
        f"Type: {escape_md(sess['material_type'].upper())}\n\n"
        f"Choose to replace the whole folder's files at once, "
        f"or update a single file inside it."
    )
    return text, markup


def process_update_whole(chat_id, files, state):
    sess_id = state.get("sess_id")
    sess = UPDATE_SESSIONS.get(sess_id)
    if not sess:
        bot.send_message(chat_id, "⚠️ This update session expired. Please run /updatefile again.")
        return

    now = datetime.now().strftime("%b %d, %Y - %H:%M")
    title = sess["title"]

    def mutator(data):
        key = f"{sess['course_code']}_{sess['material_type']}"
        materials = data.get(key, [])
        for idx in sorted(sess["indices"], reverse=True):
            if idx < len(materials):
                materials.pop(idx)
        for f in files:
            materials.append({
                "file_id": f["file_id"],
                "name": title if len(files) == 1
                        else f"{title} - {f['file_name']}",
                "content_type": f["content_type"],
                "title": title,
                "date_added": now,
                "opens": 0,
            })
        data[key] = materials
        return True

    if update_json(DATA_FILE, mutator) is not None:
        bot.send_message(chat_id, f"✅ Folder \"{title}\" replaced with {len(files)} new file(s)!")
    else:
        bot.send_message(chat_id, "⚠️ Failed to save the update to GitHub. Please try again.")
    UPDATE_SESSIONS.pop(sess_id, None)


def process_update_single_file(message, sess_id, abs_index):
    chat_id = message.chat.id
    if message.content_type == 'document':
        file_id = message.document.file_id
        file_name = message.document.file_name or "document.pdf"
        content_type = "document"
    elif message.content_type == 'photo':
        file_id = message.photo[-1].file_id
        file_name = "photo.jpg"
        content_type = "photo"
    else:
        msg = bot.send_message(chat_id, "⚠️ Please send a file or photo to replace this item. Try again:")
        bot.register_next_step_handler(msg, process_update_single_file, sess_id, abs_index)
        return

    sess = UPDATE_SESSIONS.get(sess_id)
    if not sess:
        bot.send_message(chat_id, "⚠️ This update session expired. Please run /updatefile again.")
        return

    result_holder = {}

    def mutator(data):
        key = f"{sess['course_code']}_{sess['material_type']}"
        materials = data.get(key, [])
        if abs_index >= len(materials):
            return False
        item = materials[abs_index]
        old_name = item.get("name", "")
        title = item.get("title", sess["title"])
        item["file_id"] = file_id
        item["content_type"] = content_type
        item["date_added"] = datetime.now().strftime("%b %d, %Y - %H:%M")
        item["name"] = (f"{title} - {file_name}" if " - " in old_name else title)
        materials[abs_index] = item
        data[key] = materials
        result_holder["name"] = item["name"]
        return True

    ok = update_json(DATA_FILE, mutator)
    if ok is None:
        bot.send_message(chat_id, "⚠️ That file no longer exists.")
    elif ok:
        bot.send_message(chat_id, f"✅ Successfully updated \"{result_holder.get('name', 'item')}\"!")
    else:
        bot.send_message(chat_id, "⚠️ Failed to save the update to GitHub. Please try again.")
    UPDATE_SESSIONS.pop(sess_id, None)


# ==========================================================================
#  COMMAND HANDLERS
# ==========================================================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        register_user({
            "id": message.from_user.id,
            "username": message.from_user.username,
            "first_name": message.from_user.first_name,
            "last_name": message.from_user.last_name,
        }, source="bot")
    except Exception:
        log.exception("Failed to register user on /start")

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("g_"):
        payload = parts[1][2:]
        segments = payload.rsplit("_", 2)
        if len(segments) == 3:
            course_code, material_type, idx_str = segments
            try:
                idx = int(idx_str)
                materials = load_json(DATA_FILE).get(
                    f"{course_code}_{material_type}", []
                )
                if 0 <= idx < len(materials):
                    item = materials[idx]
                    threading.Thread(
                        target=bump_stat,
                        args=(course_code, material_type, item.get("name", "")),
                        daemon=True,
                    ).start()
                    if item.get("content_type") == "photo":
                        bot.send_photo(message.chat.id, item["file_id"],
                                       caption=item.get("name", ""))
                    else:
                        bot.send_document(message.chat.id, item["file_id"],
                                          caption=item.get("name", ""))
                else:
                    bot.send_message(message.chat.id,
                                     "⚠️ That file could not be found — it may have been removed.")
            except ValueError:
                bot.send_message(message.chat.id, "⚠️ That link looks invalid.")
        else:
            bot.send_message(message.chat.id, "⚠️ That link looks invalid.")
        return

    welcome_text = (
        "Welcome to the ASTU ECE Community Bot! 🚀\n\n"
        "Tap 'Open Portal' for the best experience, or use the chat menus below."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard())


@bot.message_handler(commands=['help'])
def send_help(message):
    text = (
        "🤖 <b>Available commands</b>\n\n"
        "/start — Main menu\n"
        "/help — This message\n\n"
        "<b>Student:</b>\n"
        "• Open the portal for materials, videos, GPA & more\n"
        "• Subscribe on a course page to get DM alerts\n\n"
        "<b>Admin only:</b>\n"
        "/setexam /deleteexam /setnews /deletenews /clearnews\n"
        "/setevent /clearevents\n"
        "/addfile /updatefile /deletefile\n"
        "/addvideo /deletevideo\n"
        "/stats /users /export_users /broadcast"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")


@bot.message_handler(commands=['cancel'])
def cancel_cmd(message):
    UPLOAD_STATES.pop(message.chat.id, None)
    bot.reply_to(message, "✅ Cancelled.")


# ---------- ADMIN: Analytics dashboard ----------
def _render_analytics_message(a):
    """Build the HTML text for /stats and /users."""
    lines = [
        "📊 <b>ASTU ECE Bot Analytics</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        "👥 <b>Users</b>",
        f"• Total registered: <b>{a['total']}</b>",
        f"• Active flag: <b>{a['active_flag']}</b>  ·  Inactive: {a['inactive_flag']}",
        "",
        "🔥 <b>Activity</b>",
        f"• Active in last 24h: <b>{a['active_today']}</b>",
        f"• Active in last 7 days: <b>{a['active_7d']}</b>",
        f"• Active in last 30 days: {a['active_30d']}",
        "",
        "🆕 <b>New signups</b>",
        f"• Today: <b>{a['new_today']}</b>",
        f"• This week: <b>{a['new_7d']}</b>",
    ]

    recent = a.get("recent", [])
    if recent:
        lines.append("")
        lines.append("🕒 <b>Recently joined</b>")
        for u in recent[:5]:
            handle = "@" + u["username"] if u["username"] else "—"
            name = u["full_name"] or "Unknown"
            joined = u["joined_at"] or "—"
            lines.append(
                f"• {escape_md(name)} ({escape_md(handle)})\n"
                f"   <code>{escape_md(u['user_id'])}</code> · {escape_md(joined)}"
            )

    lines.append("")
    lines.append("Use /export_users to download the full list as CSV.")
    return "\n".join(lines)


@bot.message_handler(commands=['stats', 'users'])
def admin_stats(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    # Refresh the caller's own activity stamp
    try:
        touch_user(message.from_user.id, source="bot")
    except Exception:
        pass

    analytics = get_user_analytics()
    bot.send_message(
        message.chat.id,
        _render_analytics_message(analytics),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@bot.message_handler(commands=['export_users'])
def admin_export_users(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    analytics = get_user_analytics()
    if analytics["total"] == 0:
        bot.reply_to(message, "ℹ️ No users registered yet.")
        return

    try:
        csv_text = build_users_csv()
    except Exception as e:
        log.exception("export_users failed")
        bot.reply_to(message, f"⚠️ Failed to build CSV: {e}")
        return

    filename = f"astu_ece_users_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    bio = io.BytesIO(csv_text.encode("utf-8"))
    bio.name = filename

    caption = (
        f"📄 <b>User Export</b>\n"
        f"• Total: <b>{analytics['total']}</b>\n"
        f"• Active (24h): <b>{analytics['active_today']}</b>\n"
        f"• Active (7d): <b>{analytics['active_7d']}</b>\n"
        f"• New today: <b>{analytics['new_today']}</b>"
    )

    try:
        bot.send_document(
            message.chat.id,
            bio,
            caption=caption,
            parse_mode="HTML",
            visible_file_name=filename,
        )
    except Exception as e:
        log.exception("send_document for export failed")
        bot.reply_to(message, f"⚠️ Failed to send CSV: {e}")


# ---------- ADMIN: Broadcast ----------
@bot.message_handler(commands=['broadcast'])
def admin_broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            "Usage: <code>/broadcast Your message here</code>",
            parse_mode="HTML",
        )
        return

    body = parts[1].strip()
    active_users = get_active_users()
    if not active_users:
        bot.reply_to(message, "ℹ️ No active users to broadcast to.")
        return

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🚀 Open Portal",
                             web_app=WebAppInfo(url=WEBAPP_URL))
    )
    text = f"📢 <b>Announcement</b>\n\n{escape_md(body)}"

    enqueue_notify(active_users, text, markup)
    bot.reply_to(
        message,
        f"✅ Broadcast queued for <b>{len(active_users)}</b> active users.",
        parse_mode="HTML",
    )


# ---------- ADMIN: other commands ----------
@bot.message_handler(commands=['setexam'])
def admin_set_exam(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        bot.reply_to(message,
                     "Usage: /setexam [CourseCode] [YYYY-MM-DD] [Exam Title]")
        return
    code, date_str, title = parts[1], parts[2], parts[3]

    def mutator(data):
        data[code] = {"date": date_str, "title": title}
        return True

    update_json(EXAMS_FILE, mutator)
    bot.reply_to(message, f"✅ Countdown for '{title}' ({code}) set to {date_str}.")


@bot.message_handler(commands=['deleteexam'])
def admin_delete_exam(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /deleteexam [CourseCode]")
        return
    code = parts[1]

    def mutator(data):
        data.pop(code, None)
        return True

    update_json(EXAMS_FILE, mutator)
    bot.reply_to(message, f"✅ Countdown for {code} removed.")


def _publish_news(title, link):
    def mutator(data):
        if not isinstance(data, list):
            data = []
        item = {
            "id": os.urandom(4).hex(),
            "title": title,
            "link": link,
            "date": datetime.now().strftime("%b %d, %Y - %H:%M"),
        }
        data.insert(0, item)
        del data[20:]
        return item

    result = update_json(NEWS_FILE, mutator)
    if result:
        notify_all_subscribers(result["title"], result["date"], result["link"])
    return result


@bot.message_handler(commands=['setnews'])
def admin_set_news(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    raw = message.text.replace("/setnews", "").strip()
    link, title = None, raw

    if message.reply_to_message and getattr(message.reply_to_message, "forward_from_chat", None):
        chat = message.reply_to_message.forward_from_chat
        msg_id = message.reply_to_message.forward_from_message_id
        if chat.username:
            link = f"https://t.me/{chat.username}/{msg_id}"
        else:
            internal = str(chat.id)
            internal = internal[4:] if internal.startswith("-100") else internal.lstrip("-")
            link = f"https://t.me/c/{internal}/{msg_id}"
        if not raw:
            title = "Announcement"
    elif "|" in raw:
        parts = raw.split("|", 1)
        title = parts[0].strip()
        link = parts[1].strip()

    if not title or not link:
        bot.reply_to(message,
                     "⚠️ <b>Invalid format.</b>\n\n"
                     "Option 1: <code>/setnews Title | https://t.me/channel/123</code>\n"
                     "Option 2: forward a post and reply with <code>/setnews Title</code>",
                     parse_mode="HTML")
        return

    item = _publish_news(title, link)
    if item:
        bot.reply_to(message,
                     f"✅ News published:\n<b>{escape_md(title)}</b>\n🔗 {escape_md(link)}",
                     parse_mode="HTML", disable_web_page_preview=True)


@bot.message_handler(commands=['deletenews'])
def admin_delete_news(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    news_list = load_json(NEWS_FILE)
    if not isinstance(news_list, list) or not news_list:
        bot.reply_to(message, "ℹ️ No news to delete.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        markup = InlineKeyboardMarkup()
        for item in news_list:
            t = item.get("title", "Untitled")
            label = t if len(t) <= 40 else t[:37] + "..."
            markup.row(InlineKeyboardButton(f"❌ {label}",
                                            callback_data=f"delnews_{item['id']}"))
        bot.reply_to(message, "Select the news item you want to delete:",
                     reply_markup=markup)
        return
    nid = parts[1].strip()

    def mutator(data):
        return [n for n in (data if isinstance(data, list) else []) if n.get("id") != nid]

    update_json(NEWS_FILE, mutator)
    bot.reply_to(message, "✅ News item removed.")


@bot.message_handler(commands=['clearnews'])
def admin_clear_news(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    save_json(NEWS_FILE, [])
    bot.reply_to(message, "✅ All news items cleared.")


@bot.message_handler(commands=['setevent'])
def admin_set_event(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        bot.reply_to(message,
                     "Usage: /setevent [Start] [End] [Title]\n"
                     "(Use the same date twice for a one-day event.)")
        return
    start_date, end_date, title = parts[1], parts[2], parts[3]

    def mutator(data):
        if not isinstance(data, list):
            data = []
        data.append({"start": start_date, "end": end_date, "title": title})
        return True

    update_json(EVENTS_FILE, mutator)
    bot.reply_to(message, f"✅ Event '{title}' set from {start_date} to {end_date}.")


@bot.message_handler(commands=['clearevents'])
def admin_clear_events(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    save_json(EVENTS_FILE, [])
    bot.reply_to(message, "✅ All global countdown events cleared.")


@bot.message_handler(commands=['addfile'])
def admin_add_file_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year to ADD official material:",
                     reply_markup=year_keyboard('a'))


@bot.message_handler(commands=['updatefile'])
def admin_update_file_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year of the material you want to UPDATE:",
                     reply_markup=year_keyboard('p'))


@bot.message_handler(commands=['addvideo'])
def admin_add_video_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year to ADD a video link:",
                     reply_markup=year_keyboard('v'))


@bot.message_handler(commands=['deletefile'])
def admin_delete_file_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year to DELETE material from:",
                     reply_markup=year_keyboard('d'))


@bot.message_handler(commands=['deletevideo'])
def admin_delete_video_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = load_json(VIDEOS_FILE)
    if not data:
        bot.send_message(message.chat.id, "ℹ️ No approved videos found to delete.")
        return
    markup = InlineKeyboardMarkup()
    has_videos = False
    for course_code, vids in data.items():
        for idx, v in enumerate(vids):
            has_videos = True
            markup.row(InlineKeyboardButton(
                f"❌ [{course_display(course_code)}] {v.get('title', 'Video')}",
                callback_data=f"delvid_{course_code}_{idx}",
            ))
    if not has_videos:
        bot.send_message(message.chat.id, "ℹ️ No approved videos found to delete.")
        return
    bot.send_message(message.chat.id, "Select the video you want to delete:",
                     reply_markup=markup)


# ==========================================================================
#  CALLBACK HANDLERS
# ==========================================================================
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    def _edit(text, markup=None):
        try:
            bot.edit_message_text(
                text, chat_id=call.message.chat.id,
                message_id=call.message.message_id, reply_markup=markup,
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Refresh last_active on every button press
    try:
        touch_user(call.from_user.id, source="callback")
    except Exception:
        pass

    data = call.data

    if data == "main_find":
        _edit("Select your academic year to FIND materials:", year_keyboard('f'))

    elif data == "main_upload":
        _edit("Select your academic year to UPLOAD materials:", year_keyboard('u'))

    elif data == "back_main":
        _edit(
            "Welcome to the ASTU ECE Community Bot! 🚀\n\n"
            "Tap 'Open Portal' for the best experience, or use the chat menus below.",
            main_menu_keyboard(),
        )

    elif data.startswith(("fy_", "uy_", "ay_", "dy_", "vy_", "py_")):
        action = data[0]
        year = data.split('_')[1]
        roman = {"2": "II", "3": "III"}.get(year, year)
        _edit(f"Year {roman} selected.\nChoose your semester:",
              semester_keyboard(year, action))

    elif data.startswith(("fs_", "us_", "as_", "ds_", "vs_", "ps_")):
        parts = data.split('_')
        action = parts[0][0]
        year, semester = parts[1], parts[2]
        _edit("Select the subject:",
              subject_keyboard(year, semester, action))

    elif data.startswith(("fc_", "uc_", "ac_", "dc_", "vc_", "pc_")):
        parts = data.split('_')
        action = parts[0][0]
        course_code = parts[1]

        if action == 'v':
            try:
                msg = bot.edit_message_text(
                    f"Course: <b>{escape_md(course_display(course_code))}</b>\n\n"
                    f"Reply with the <b>Video Title</b> and <b>URL</b> separated by a new line.\n\n"
                    f"Example:\n"
                    f"<code>Lecture 1 Introduction\nhttps://youtube.com/watch?v=...</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                )
                bot.register_next_step_handler(msg, process_admin_add_video, course_code)
            except Exception:
                pass
        else:
            _edit(f"Course: <b>{escape_md(course_display(course_code))}</b>\nSelect the type of material:",
                  material_type_keyboard(course_code, action))

    elif data.startswith(("fm_", "um_", "am_", "dm_", "pm_")):
        parts = data.split('_')
        action = parts[0][0]
        course_code = parts[1]
        material_type = parts[2]

        if action == 'f':
            materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
            if not materials:
                bot.send_message(call.message.chat.id,
                                 f"ℹ️ No {material_type.upper()} files available yet for {course_display(course_code)}.")
            else:
                bot.send_message(call.message.chat.id,
                                 f"📚 Found {len(materials)} file(s) for {course_display(course_code)}:")
                for item in materials:
                    if item.get("content_type") == "photo":
                        bot.send_photo(call.message.chat.id, item["file_id"],
                                       caption=item.get("name", ""))
                    else:
                        bot.send_document(call.message.chat.id, item["file_id"],
                                          caption=item.get("name", ""))

        elif action in ('u', 'a'):
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            label = "Upload" if action == 'u' else "Admin save"
            msg = bot.send_message(
                call.message.chat.id,
                f"📤 <b>{label} mode: {escape_md(course_display(course_code))} ({escape_md(material_type.upper())})</b>\n\n"
                f"Send your file(s) now — you can send as many as you want.\n\n"
                f"👇 When done, click <b>Finish Upload</b>.",
                parse_mode="HTML",
                reply_markup=finish_upload_keyboard(),
            )
            UPLOAD_STATES[call.message.chat.id] = _stamp({
                "course_code": course_code,
                "material_type": material_type,
                "action": "user" if action == 'u' else "admin",
                "files": [],
                "status_msg_id": msg.message_id,
            })

        elif action == 'd':
            materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
            if not materials:
                bot.send_message(call.message.chat.id,
                                 f"ℹ️ No files found under {course_display(course_code)} ({material_type.upper()}) to delete.")
            else:
                text, markup = build_delete_list(course_code, material_type)
                _edit(text, markup)

        elif action == 'p':
            text, markup = build_update_folder_list(course_code, material_type)
            _edit(text, markup)

    elif data == "finish_upload":
        chat_id = call.message.chat.id
        if chat_id not in UPLOAD_STATES:
            bot.answer_callback_query(call.id, "Upload session expired or already finished.")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            return

        state = UPLOAD_STATES[chat_id]
        files = state["files"]

        if not files:
            bot.answer_callback_query(call.id,
                                      "You haven't sent any files yet! Send them first.",
                                      show_alert=True)
            return

        if state["action"] == "admin":
            state["awaiting_title"] = True
            _edit(f"✏️ Got {len(files)} file(s). Please type a <b>title</b> for this folder.")
            return

        if state["action"] == "update_whole":
            _edit(f"🔄 Replacing the folder with {len(files)} new file(s)...")
            process_update_whole(chat_id, files, state)
            UPLOAD_STATES.pop(chat_id, None)
            return

        _edit(f"🔄 Processing your {len(files)} file(s)...")
        process_files(chat_id, files, state, call.from_user)
        UPLOAD_STATES.pop(chat_id, None)

    elif data.startswith("delitem_"):
        parts = data.split('_')
        course_code, material_type, idx = parts[1], parts[2], int(parts[3])
        deleted_name = delete_material_by_index(course_code, material_type, idx)
        if deleted_name:
            bot.answer_callback_query(call.id, f"✅ Deleted {deleted_name}")
        else:
            bot.answer_callback_query(call.id, "⚠️ Could not delete.", show_alert=True)
        text, markup = build_delete_list(course_code, material_type)
        _edit(text, markup)

    elif data.startswith("updfolder_"):
        sess_id = data.split('_', 1)[1]
        text, markup = build_update_folder_detail(sess_id)
        _edit(text, markup)

    elif data.startswith("updwhole_"):
        sess_id = data.split('_', 1)[1]
        sess = UPDATE_SESSIONS.get(sess_id)
        if not sess:
            bot.answer_callback_query(call.id,
                                      "This update session expired. Run /updatefile again.",
                                      show_alert=True)
            return
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        msg = bot.send_message(
            call.message.chat.id,
            f"🔁 <b>Replacing folder \"{escape_md(sess['title'])}\"</b>\n\n"
            f"Send the new file(s) now, then click <b>Finish Upload</b>.",
            parse_mode="HTML",
            reply_markup=finish_upload_keyboard(),
        )
        UPLOAD_STATES[call.message.chat.id] = _stamp({
            "course_code": sess["course_code"],
            "material_type": sess["material_type"],
            "action": "update_whole",
            "sess_id": sess_id,
            "files": [],
            "status_msg_id": msg.message_id,
        })

    elif data.startswith("upditem_"):
        parts = data.split('_')
        sess_id, pos = parts[1], int(parts[2])
        sess = UPDATE_SESSIONS.get(sess_id)
        if not sess or pos >= len(sess["indices"]):
            bot.answer_callback_query(call.id,
                                      "This update session expired. Run /updatefile again.",
                                      show_alert=True)
            return
        abs_index = sess["indices"][pos]
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        msg = bot.send_message(call.message.chat.id,
                               "📥 Send the new file (or photo) to replace this item:")
        bot.register_next_step_handler(msg, process_update_single_file, sess_id, abs_index)

    elif data.startswith("delvid_"):
        parts = data.split('_')
        course_code, idx = parts[1], int(parts[2])
        removed_holder = {}

        def mutator(vdata):
            arr = vdata.get(course_code, [])
            if 0 <= idx < len(arr):
                removed_holder["item"] = arr.pop(idx)
                if not arr:
                    vdata.pop(course_code, None)
                return True
            return False

        ok = update_json(VIDEOS_FILE, mutator)
        if ok:
            _edit(f"✅ Deleted video: {escape_md(removed_holder.get('item', {}).get('title', 'Video'))}")
        else:
            _edit("⚠️ Video not found.")

    elif data.startswith("delnews_"):
        if call.from_user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
            return
        nid = data.split('_', 1)[1]

        def mutator(ndata):
            return [n for n in (ndata if isinstance(ndata, list) else [])
                    if n.get("id") != nid]

        update_json(NEWS_FILE, mutator)
        _edit("✅ News item deleted.")

    elif data.startswith("approve_vid_"):
        req_id = data.split('_', 2)[2]
        v_data = PENDING_VIDEOS.pop(req_id, None)
        if not v_data:
            bot.answer_callback_query(call.id, "Request expired or already handled.")
            return
        for admin in ADMIN_IDS:
            try:
                bot.send_message(
                    admin,
                    f"✅ Video '{escape_md(v_data['title'])}' for "
                    f"{escape_md(course_display(v_data['course']))} from "
                    f"{escape_md(v_data['username'])} approved!\n"
                    f"Use /addvideo to add it to the official list.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id,
                                          message_id=call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass

    elif data.startswith("reject_vid_"):
        req_id = data.split('_', 2)[2]
        PENDING_VIDEOS.pop(req_id, None)
        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, "❌ Video submission rejected.")
            except Exception:
                pass
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id,
                                          message_id=call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass

    elif data.startswith("approve_upload_"):
        req_id = data.split('_', 2)[2]
        up = PENDING_UPLOADS.pop(req_id, None)
        if not up:
            bot.answer_callback_query(call.id, "Request expired or already handled.")
            return
        for admin in ADMIN_IDS:
            try:
                bot.send_message(
                    admin,
                    f"✅ Upload batch from {escape_md(up.get('username', 'Student'))} approved.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        try:
            bot.send_message(
                up["chat_id"],
                f"✅ Good news! Your batch of {len(up['files'])} file(s) "
                f"for {course_display(up['course_code'])} has been approved.\n\n"
                f"They will be organized and added to the official portal soon.",
            )
        except Exception:
            pass
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id,
                                          message_id=call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass

    elif data.startswith("reject_upload_"):
        req_id = data.split('_', 2)[2]
        up = PENDING_UPLOADS.pop(req_id, None)
        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, "❌ Upload batch rejected.")
            except Exception:
                pass
        if up:
            try:
                bot.send_message(
                    up["chat_id"],
                    f"❌ Your submitted batch of {len(up['files'])} file(s) was not approved.",
                )
            except Exception:
                pass
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id,
                                          message_id=call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass

    else:
        bot.answer_callback_query(call.id)


# ==========================================================================
#  MESSAGE HANDLERS
# ==========================================================================
@bot.message_handler(content_types=['document', 'photo'])
def handle_media(message):
    chat_id = message.chat.id
    if chat_id not in UPLOAD_STATES:
        return
    state = UPLOAD_STATES[chat_id]
    if state.get("awaiting_title"):
        return

    file_id = None
    file_name = None
    content_type = "document"

    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "document.pdf"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_name = "photo.jpg"
        content_type = "photo"

    if not file_id:
        return

    state["files"].append({
        "file_id": file_id,
        "file_name": file_name,
        "content_type": content_type,
        "message_id": message.message_id,
    })

    try:
        count = len(state["files"])
        bot.edit_message_text(
            f"📥 <b>Collected {count} file(s) so far.</b>\n\n"
            f"Keep sending more, or click <b>Finish Upload</b> when done.",
            chat_id=chat_id,
            message_id=state["status_msg_id"],
            parse_mode="HTML",
            reply_markup=finish_upload_keyboard(),
        )
    except Exception:
        pass


@bot.message_handler(
    content_types=['text'],
    func=lambda m: UPLOAD_STATES.get(m.chat.id, {}).get("awaiting_title")
                   and not (m.text or "").startswith('/'),
)
def handle_title_input(message):
    chat_id = message.chat.id
    state = UPLOAD_STATES.get(chat_id)
    if not state:
        return
    title = (message.text or "").strip()
    if not title:
        bot.send_message(chat_id, "Please send a non-empty title.")
        return

    state["title"] = title
    state["awaiting_title"] = False
    files = state["files"]
    bot.send_message(chat_id, f"🔄 Saving \"{title}\" ({len(files)} file(s))...")
    process_files(chat_id, files, state, message.from_user, title=title)
    UPLOAD_STATES.pop(chat_id, None)


@bot.message_handler(content_types=['text'])
def fallback_text(message):
    if (message.text or "").startswith('/'):
        return
    # Silent tracking (don't spam users)
    try:
        touch_user(message.from_user.id, source="text")
    except Exception:
        pass
    bot.reply_to(message,
                 "🤖 I don't understand that. Try /help, or open the portal "
                 "with /start.")


def process_admin_add_video(message, course_code):
    if not message.text:
        bot.reply_to(message, "⚠️ Please send text only. Start over with /addvideo")
        return
    parts = message.text.strip().split('\n', 1)
    if len(parts) != 2:
        bot.reply_to(message,
                     "⚠️ Invalid format. Put the <b>Title</b> on the first line "
                     "and the <b>URL</b> on the second.\n\nStart over with /addvideo",
                     parse_mode="HTML")
        return

    title, url = parts[0].strip(), parts[1].strip()
    if add_approved_video(course_code, title, url):
        bot.reply_to(message, f"✅ Added video '{title}' to {course_display(course_code)}!")
        try:
            subs = load_json(SUBS_FILE).get(course_code, [])
            if subs:
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton(
                    "🚀 Open App", web_app=WebAppInfo(url=WEBAPP_URL)
                ))
                text = (
                    f"📺 <b>New Tutorial Video!</b>\n\n"
                    f"📚 <b>Course:</b> {escape_md(course_display(course_code))}\n"
                    f"📝 <b>Title:</b> {escape_md(title)}\n\n"
                    f"Open the Portal to watch it."
                )
                enqueue_notify(subs, text, markup)
        except Exception:
            pass
    else:
        bot.reply_to(message, "⚠️ Failed to save video to GitHub.")


def send_as_album(chat_id, files, caption=None):
    docs = [f for f in files if f["content_type"] == "document"]
    photos = [f for f in files if f["content_type"] == "photo"]

    def chunks(lst, n=10):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    for group in chunks(docs):
        if len(group) == 1:
            bot.send_document(chat_id, group[0]["file_id"], caption=caption)
        else:
            media = [
                InputMediaDocument(
                    f["file_id"],
                    caption=caption if i == 0 else None,
                )
                for i, f in enumerate(group)
            ]
            bot.send_media_group(chat_id, media)

    for group in chunks(photos):
        if len(group) == 1:
            bot.send_photo(chat_id, group[0]["file_id"], caption=caption)
        else:
            media = [
                InputMediaPhoto(
                    f["file_id"],
                    caption=caption if i == 0 else None,
                )
                for i, f in enumerate(group)
            ]
            bot.send_media_group(chat_id, media)


def process_files(chat_id, files, state, user, title=None):
    course_code = state["course_code"]
    material_type = state["material_type"]
    action = state["action"]

    if action == "admin":
        ok = save_material_batch(course_code, material_type, files,
                                 title or "Untitled")
        if ok:
            bot.send_message(chat_id,
                             f"✅ Saved \"{title}\" ({len(files)} file(s)) under "
                             f"{course_display(course_code)} ({material_type.upper()})!")
            send_as_album(chat_id, files, caption=title)
            try:
                subs = load_json(SUBS_FILE).get(course_code, [])
                if subs:
                    markup = InlineKeyboardMarkup()
                    markup.row(InlineKeyboardButton(
                        "🚀 Open App", web_app=WebAppInfo(url=WEBAPP_URL)
                    ))
                    text = (
                        f"🔔 <b>New Material Added!</b>\n\n"
                        f"📚 <b>Course:</b> {escape_md(course_display(course_code))}\n"
                        f"📂 <b>Type:</b> {escape_md(material_type.upper())}\n"
                        f"📝 <b>Title:</b> {escape_md(title or 'Untitled')}\n\n"
                        f"Open the Portal to download it."
                    )
                    enqueue_notify(subs, text, markup)
            except Exception:
                pass
        else:
            bot.send_message(chat_id, "⚠️ Failed to save to GitHub. Please try again.")

    elif action == "user":
        req_id = os.urandom(4).hex()
        PENDING_UPLOADS[req_id] = _stamp({
            "course_code": course_code,
            "material_type": material_type,
            "files": files,
            "chat_id": chat_id,
            "username": user.username or "Student",
        })
        admin_text = (
            f"📥 New Chat Upload (Batch of {len(files)} files)\n"
            f"From: @{user.username or 'Student'}\n\n"
            f"Course: {course_display(course_code)}\n"
            f"Type: {material_type.upper()}"
        )
        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, admin_text)
                for f in files:
                    bot.forward_message(admin, chat_id, f["message_id"])
                markup = InlineKeyboardMarkup()
                markup.row(
                    InlineKeyboardButton("✅ Approve All",
                                         callback_data=f"approve_upload_{req_id}"),
                    InlineKeyboardButton("❌ Reject All",
                                         callback_data=f"reject_upload_{req_id}"),
                )
                bot.send_message(admin, "Review this batch upload:", reply_markup=markup)
            except Exception:
                pass
        bot.send_message(chat_id,
                         f"✅ Thank you! Your batch of {len(files)} file(s) "
                         f"has been sent for review.")


# ==========================================================================
#  FLASK — WEBHOOK + HEALTH CHECK
# ==========================================================================
@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    if WEBHOOK_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(incoming, WEBHOOK_SECRET):
            return "forbidden", 403
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200


@app.route('/')
def webhook():
    return "Bot is awake and running!", 200


@app.route('/api/health')
def api_health():
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "webhook_secret_configured": bool(WEBHOOK_SECRET),
    }), 200


# ==========================================================================
#  FLASK — WEBAPP API
# ==========================================================================
@app.route('/api/config', methods=['GET'])
def api_config():
    return jsonify({
        "webapp_url": WEBAPP_URL,
    }), 200


@app.route('/api/curriculum', methods=['GET'])
def api_curriculum():
    return jsonify(CURRICULUM), 200


@app.route('/api/materials', methods=['GET'])
def get_materials():
    data = load_json(DATA_FILE)
    
    # Inject the direct_pdf_url into each item
    for key, items in data.items():
        # Key format is "COURSE_CODE_MATERIAL_TYPE" (e.g., "ECEg2202_note")
        parts = key.split('_')
        course_code = parts[0]
        mat_type = parts[1] if len(parts) > 1 else 'unknown'
        
        for idx, item in enumerate(items):
            if item.get("content_type") == "document":
                # Point this to our new proxy endpoint
                item["direct_pdf_url"] = f"/api/serve_pdf/{course_code}/{mat_type}/{idx}"
            else:
                item["direct_pdf_url"] = ""
                
    return jsonify(data), 200


PDF_CACHE_DIR = os.path.join(tempfile.gettempdir(), "astu_pdf_cache")
PDF_CACHE_MAX_FILES = 40
_pdf_dl_lock = threading.Lock()


def _cache_pdf(file_id):
    """Download a Telegram file once into a local cache and return its path.
    Serving from disk lets Flask answer HTTP Range requests, so PDF.js can
    load pages progressively instead of downloading the whole file first."""
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    path = os.path.join(PDF_CACHE_DIR, hashlib.sha1(file_id.encode()).hexdigest() + ".pdf")
    if os.path.exists(path):
        os.utime(path, None)
        return path
    with _pdf_dl_lock:
        if os.path.exists(path):
            return path
        info = bot.get_file(file_id)
        url = f"https://api.telegram.org/file/bot{TOKEN}/{info.file_path}"
        r = requests.get(url, stream=True, timeout=(10, 60))
        r.raise_for_status()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        os.replace(tmp, path)
        # Evict least-recently-used files
        files = sorted(
            (os.path.join(PDF_CACHE_DIR, n) for n in os.listdir(PDF_CACHE_DIR) if n.endswith(".pdf")),
            key=os.path.getmtime,
        )
        for old in files[:-PDF_CACHE_MAX_FILES]:
            try:
                os.remove(old)
            except OSError:
                pass
    return path


@app.route('/api/serve_pdf/<course_code>/<mat_type>/<int:idx>', methods=['GET'])
def serve_pdf(course_code, mat_type, idx):
    # 0. Only verified Telegram Mini App users may download files.
    #    (verify_init_data directly: get_auth_user would re-register the user on every Range request)
    init_data = request.headers.get("X-Telegram-Init-Data") or request.args.get("initData")
    if not verify_init_data(init_data):
        return jsonify({"error": "Unauthorized"}), 401

    # 1. Find the file record in the database
    materials = load_json(DATA_FILE).get(f"{course_code}_{mat_type}", [])
    if idx < 0 or idx >= len(materials):
        return jsonify({"error": "File not found"}), 404

    file_id = materials[idx].get("file_id")
    if not file_id:
        return jsonify({"error": "Invalid file record"}), 400

    # 2. Fetch (or reuse) the file from Telegram
    try:
        path = _cache_pdf(file_id)
    except Exception as e:
        if "too big" in str(e).lower():
            return jsonify({"error": "File too large for the Telegram Bot API (20 MB limit)"}), 413
        log.error(f"Failed to fetch file {file_id}: {e}")
        return jsonify({"error": "Failed to fetch file from Telegram"}), 500

    # 3. Refuse non-PDF documents (docx, pptx, ...) so the viewer can explain why
    try:
        with open(path, "rb") as f:
            if b"%PDF-" not in f.read(1024):
                return jsonify({"error": "Not a PDF"}), 415
    except OSError as e:
        log.error(f"Failed to read cached PDF: {e}")
        return jsonify({"error": "Internal server error"}), 500

    # 4. conditional=True enables Range / If-Modified-Since handling
    resp = send_file(path, mimetype="application/pdf", conditional=True)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route('/api/send_file', methods=['POST'])
def api_send_file():
    """Send a material to the student's private chat with the bot (download/share)."""
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    try:
        idx = int(body.get("idx"))
    except (TypeError, ValueError):
        return jsonify({"error": "Bad request"}), 400
    key = f"{body.get('course', '')}_{body.get('type', '')}"

    materials = load_json(DATA_FILE).get(key, [])
    if idx < 0 or idx >= len(materials):
        return jsonify({"error": "File not found"}), 404
    item = materials[idx]
    file_id = item.get("file_id")
    if not file_id:
        return jsonify({"error": "Invalid file record"}), 400

    try:
        bot.send_document(user["id"], file_id, caption=(item.get("name") or "")[:1000] or None)
    except Exception as e:
        msg = str(e).lower()
        if "403" in msg or "blocked" in msg or "initiate" in msg or "chat not found" in msg:
            return jsonify({"error": "bot_blocked"}), 403
        log.error(f"send_file failed for {user.get('id')}: {e}")
        return jsonify({"error": "Failed to send file"}), 500
    return jsonify({"ok": True}), 200


@app.route('/api/videos', methods=['GET'])
def get_videos():
    return jsonify(load_json(VIDEOS_FILE)), 200


@app.route('/api/exams', methods=['GET'])
def get_exams():
    return jsonify(load_json(EXAMS_FILE)), 200


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    news = load_json(NEWS_FILE)
    if isinstance(news, dict):
        if news.get("text") or news.get("image"):
            news = [{
                "id": "legacy",
                "title": news.get("text", "Announcement"),
                "link": "#", "date": "",
            }]
        else:
            news = []
    if not isinstance(news, list):
        news = []

    events = load_json(EVENTS_FILE)
    if isinstance(events, dict):
        events = []
    return jsonify({"news": news, "events": events}), 200


@app.route('/api/leaderboard', methods=['GET'])
def api_leaderboard():
    stats = load_json(STATS_FILE) or {}
    top = sorted(
        ((k, v) for k, v in stats.items() if isinstance(v, int)),
        key=lambda x: x[1], reverse=True,
    )[:20]
    return jsonify([{"resource": k, "opens": v} for k, v in top]), 200


@app.route('/api/track_open', methods=['POST'])
def api_track_open():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    course = body.get("course")
    kind = body.get("kind")
    name = body.get("name")
    if not (course and kind and name):
        return jsonify({"error": "Missing fields"}), 400
    threading.Thread(target=bump_stat, args=(course, kind, name), daemon=True).start()
    return jsonify({"status": "ok"}), 200


@app.route('/api/upload', methods=['POST'])
def handle_webapp_upload():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['file']
    course = request.form.get('course', 'Unknown')
    mat_type = request.form.get('type', 'Unknown')
    username = user.get("username") or user.get("first_name", "Student")
    chat_id = user.get("id")

    file_bytes = file.read()
    if not file_bytes:
        return jsonify({"error": "Empty file"}), 400
    if len(file_bytes) > 45 * 1024 * 1024:
        return jsonify({"error": "File exceeds 45 MB limit"}), 413

    admin_text = (
        f"🌐 WEB APP Upload from {escape_md(username)}\n"
        f"Course: {escape_md(course_display(course))}\n"
        f"Type: {escape_md(mat_type.upper())}"
    )

    file_id = None
    used_admin = None
    for admin in ADMIN_IDS:
        try:
            msg = bot.send_document(
                admin,
                io.BytesIO(file_bytes),
                caption=admin_text,
                parse_mode="HTML",
                visible_file_name=file.filename,
            )
            file_id = msg.document.file_id
            used_admin = admin
            break
        except Exception as e:
            log.warning("upload to admin %s failed: %s", admin, e)

    if not file_id:
        return jsonify({"error": "Failed to forward file to any admin"}), 500

    req_id = os.urandom(4).hex()
    PENDING_UPLOADS[req_id] = _stamp({
        "course_code": course,
        "material_type": mat_type,
        "files": [{
            "file_id": file_id,
            "file_name": file.filename,
            "content_type": "document",
        }],
        "chat_id": int(chat_id) if chat_id else used_admin,
        "username": username,
    })

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_upload_{req_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload_{req_id}"),
    )
    for admin in ADMIN_IDS:
        try:
            bot.send_message(
                admin,
                f"Review web upload from {escape_md(username)} "
                f"({escape_md(course_display(course))} / {escape_md(mat_type.upper())}):",
                parse_mode="HTML",
                reply_markup=markup,
            )
        except Exception:
            pass

    return jsonify({"status": "success"}), 200


@app.route('/api/upload_video', methods=['POST'])
def handle_video_upload():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    course = body.get('course')
    title = body.get('title')
    url = body.get('url')
    if not (course and title and url):
        return jsonify({"error": "Invalid data"}), 400

    username = user.get("username") or user.get("first_name", "Student")

    req_id = os.urandom(4).hex()
    PENDING_VIDEOS[req_id] = _stamp({
        "course": course, "title": title, "url": url, "username": username,
    })

    admin_text = (
        f"📺 New Video Submission from {escape_md(username)}\n\n"
        f"Course: {escape_md(course_display(course))}\n"
        f"Title: {escape_md(title)}\n"
        f"URL: {escape_md(url)}"
    )
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Approve Video",
                             callback_data=f"approve_vid_{req_id}"),
        InlineKeyboardButton("❌ Reject",
                             callback_data=f"reject_vid_{req_id}"),
    )
    for admin in ADMIN_IDS:
        try:
            bot.send_message(admin, admin_text, parse_mode="HTML",
                             reply_markup=markup)
        except Exception:
            pass

    return jsonify({"status": "pending_approval"}), 200


@app.route('/api/subscribe', methods=['POST'])
def handle_subscribe():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    uid = str(user["id"])
    body = request.get_json(silent=True) or {}
    course = body.get("course")
    is_subbing = bool(body.get("subscribe", True))
    if not course:
        return jsonify({"error": "Missing course"}), 400

    def mutator(data):
        arr = data.setdefault(course, [])
        if is_subbing and uid not in arr:
            arr.append(uid)
        elif not is_subbing and uid in arr:
            arr.remove(uid)
        if not arr:
            data.pop(course, None)
        return True

    update_json(SUBS_FILE, mutator)
    return jsonify({"status": "success"}), 200


@app.route('/api/subscriptions', methods=['GET'])
def get_subs():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    uid = str(user["id"])
    subs = load_json(SUBS_FILE)
    return jsonify([c for c, users in (subs or {}).items() if uid in users]), 200


@app.route('/api/favorites', methods=['GET'])
def get_favorites():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    uid = str(user["id"])
    favs = load_json(FAVS_FILE)
    return jsonify(favs.get(uid, []) if isinstance(favs, dict) else []), 200


@app.route('/api/favorites', methods=['POST'])
def toggle_favorite():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    uid = str(user["id"])
    body = request.get_json(silent=True) or {}
    item = body.get("item")
    if not item or not item.get("id"):
        return jsonify({"error": "Invalid item"}), 400

    action_holder = {}

    def mutator(data):
        if not isinstance(data, dict):
            data = {}
        user_favs = data.setdefault(uid, [])
        existing = next(
            (i for i, f in enumerate(user_favs) if f.get("id") == item["id"]),
            None,
        )
        if existing is not None:
            user_favs.pop(existing)
            action_holder["action"] = "removed"
        else:
            user_favs.append(item)
            action_holder["action"] = "added"
        return True

    update_json(FAVS_FILE, mutator)
    return jsonify({"status": "ok", "action": action_holder.get("action")}), 200


@app.route('/api/post_news', methods=['POST'])
def api_post_news():
    user = get_auth_user()
    if not is_admin(user):
        return jsonify({"error": "Unauthorized"}), 403

    title = (request.form.get('title') or '').strip()
    link = (request.form.get('link') or '').strip()
    if not title or not link:
        return jsonify({"error": "Title and link are required"}), 400
    if not (link.startswith("https://t.me/") or link.startswith("http://t.me/")):
        return jsonify({"error": "Link must be a t.me URL"}), 400

    item = _publish_news(title, link)
    if not item:
        return jsonify({"error": "Failed to save"}), 500
    return jsonify({"status": "success", "item": item}), 200


@app.route('/api/delete_news', methods=['POST'])
def api_delete_news():
    user = get_auth_user()
    if not is_admin(user):
        return jsonify({"error": "Unauthorized"}), 403
    nid = (request.get_json(silent=True) or {}).get("id")
    if not nid:
        return jsonify({"error": "Missing id"}), 400

    def mutator(data):
        return [n for n in (data if isinstance(data, list) else [])
                if n.get("id") != nid]

    update_json(NEWS_FILE, mutator)
    return jsonify({"status": "success"}), 200


@app.route('/api/clear_news', methods=['POST'])
def api_clear_news():
    user = get_auth_user()
    if not is_admin(user):
        return jsonify({"error": "Unauthorized"}), 403
    save_json(NEWS_FILE, [])
    return jsonify({"status": "success"}), 200


@app.route('/api/submit_feedback', methods=['POST'])
def handle_feedback():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Feedback must be a JSON object"}), 400
    raw_rating = body.get("rating")
    if isinstance(raw_rating, bool) or not isinstance(raw_rating, (int, str)):
        return jsonify({"error": "Choose a whole-number rating from 1 to 5"}), 400
    try:
        rating = int(raw_rating)
    except (ValueError, TypeError):
        return jsonify({"error": "Choose a whole-number rating from 1 to 5"}), 400
    if not 1 <= rating <= 5:
        return jsonify({"error": "Choose a rating from 1 to 5"}), 400
    msg_content = body.get("message", "")
    username = user.get("username") or user.get("first_name", "Student")
    uid = user.get("id")

    stars = "⭐" * max(0, min(5, rating))
    admin_text = (
        f"📝 <b>New Bot Feedback</b>\n\n"
        f"👤 From: {escape_md(username)} (<code>{escape_md(uid)}</code>)\n"
        f"🌟 Rating: {stars} ({rating}/5)\n\n"
        f"💬 <b>Message:</b>\n{escape_md(msg_content)}"
    )
    for admin in ADMIN_IDS:
        try:
            bot.send_message(admin, admin_text, parse_mode="HTML")
        except Exception:
            pass
    return jsonify({"status": "success"}), 200


@app.route('/api/report_issue', methods=['POST'])
def handle_report_issue():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    item_type = body.get('type', 'Item')
    course = body.get('course', 'Unknown')
    title = body.get('title', 'Unknown')
    comment = (body.get('comment') or '').strip()
    username = user.get("username") or user.get("first_name", "Student")
    uid = user.get("id")

    comment_block = (
        f"\n💬 <b>Student Comment:</b>\n<i>{escape_md(comment)}</i>"
        if comment else "\n💬 <b>Student Comment:</b>\n<i>No comment provided.</i>"
    )
    admin_text = (
        f"⚠️ <b>Issue Reported</b>\n\n"
        f"👤 From: {escape_md(username)} (<code>{escape_md(uid)}</code>)\n"
        f"📚 Course: {escape_md(course_display(course))}\n"
        f"📂 Type: {escape_md(item_type)}\n"
        f"📄 Title: {escape_md(title)}\n"
        f"{comment_block}"
    )
    for admin in ADMIN_IDS:
        try:
            bot.send_message(admin, admin_text, parse_mode="HTML")
        except Exception:
            pass
    return jsonify({"status": "success"}), 200


@app.route('/api/request_resource', methods=['POST'])
def handle_request_resource():
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    course = (body.get("course") or "").strip()
    detail = (body.get("detail") or "").strip()
    if not course and not detail:
        return jsonify({"error": "Provide a course or detail"}), 400

    username = user.get("username") or user.get("first_name", "Student")
    uid = user.get("id")

    def mutator(data):
        if not isinstance(data, list):
            data = []
        data.insert(0, {
            "id": os.urandom(4).hex(),
            "course": course,
            "detail": detail,
            "username": username,
            "chat_id": uid,
            "date": datetime.now().strftime("%b %d, %Y - %H:%M"),
        })
        del data[50:]
        return True

    update_json(REQUESTS_FILE, mutator)

    admin_text = (
        f"🙋 <b>Resource Request</b>\n\n"
        f"👤 From: {escape_md(username)} (<code>{escape_md(uid)}</code>)\n"
        f"📚 Course: {escape_md(course_display(course) or '—')}\n"
        f"📝 Detail: {escape_md(detail or '—')}"
    )
    for admin in ADMIN_IDS:
        try:
            bot.send_message(admin, admin_text, parse_mode="HTML")
        except Exception:
            pass
    return jsonify({"status": "success"}), 200


# ==========================================================================
#  NEW: AI STUDY BUDDY ENDPOINT
# ==========================================================================
@app.route('/api/ask_ai', methods=['POST'])
def api_ask_ai():
    # 1. Verify the user is a legitimate Telegram user
    user = get_auth_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    if not GOOGLE_API_KEY:
        return jsonify({"error": "AI service is not configured. Missing GOOGLE_API_KEY."}), 500

    # Limit prompt length to avoid abuse and excessive token usage
    if len(prompt) > 4000:
        prompt = prompt[:4000]

    # 2. Call the Gemini API (Free Tier model)
    # FIXED: Changed model from gemini-2.5-flash to gemini-3.6-flash
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GOOGLE_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "systemInstruction": {
            "parts": [{"text": "You are a helpful AI study assistant for engineering students at ASTU (Adama Science and Technology University). Provide clear, concise, and educational answers. If asked about a specific course, provide relevant academic help."}]
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # 3. Extract the text from Gemini's response
        candidates = data.get("candidates", [])
        if candidates:
            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        else:
            text = "I couldn't generate a response. Please try again."
            
        return jsonify({"response": text}), 200

    except requests.exceptions.HTTPError as e:
        if response.status_code == 429:
            return jsonify({"error": "I'm thinking a lot right now. Please try again in a moment."}), 429
        log.error(f"Gemini API error: {e} - {response.text}")
        return jsonify({"error": "Failed to get AI response"}), 500
    except Exception as e:
        log.error(f"AI request failed: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ==========================================================================
#  ENTRY POINT
# ==========================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        try:
            bot.remove_webhook()
            kwargs = {"url": render_url + '/' + TOKEN}
            if WEBHOOK_SECRET:
                kwargs["secret_token"] = WEBHOOK_SECRET
            bot.set_webhook(**kwargs)
            log.info("Webhook set: %s", render_url)
        except Exception as e:
            log.exception("Failed to set webhook: %s", e)
    app.run(host="0.0.0.0", port=port)
