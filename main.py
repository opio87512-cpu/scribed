import os
import json
import base64
import requests
import telebot
from datetime import datetime
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    InputMediaDocument,
    InputMediaPhoto,
)
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- CONFIGURATION ---
TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
CORS(app)

# --- ADD YOUR ADMIN IDS HERE ---
ADMIN_IDS = [8429521561, 8244142809]  # Both admins are now active

# --- GITHUB-BACKED STORAGE ---
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = "opio87512-cpu/scribed"
GITHUB_BRANCH = "main"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/contents"

DATA_FILE = "materials.json"
VIDEOS_FILE = "videos.json"
SUBS_FILE = "subs.json"
EXAMS_FILE = "exams.json"


def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def load_json(filename):
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/{filename}",
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=10,
        )
        if resp.status_code == 200:
            content_b64 = resp.json()["content"]
            decoded = base64.b64decode(content_b64).decode("utf-8")
            return json.loads(decoded) if decoded.strip() else {}
        elif resp.status_code == 404:
            return {}
        else:
            print(f"GitHub load failed for {filename}: {resp.status_code} {resp.text}")
            return {}
    except Exception as e:
        print(f"GitHub load error for {filename}: {e}")
        return {}


def save_json(filename, data):
    try:
        content_str = json.dumps(data, indent=4)
        encoded = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        get_resp = requests.get(
            f"{GITHUB_API_BASE}/{filename}",
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=10,
        )
        sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

        payload = {
            "message": f"Update {filename}",
            "content": encoded,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(
            f"{GITHUB_API_BASE}/{filename}",
            headers=_github_headers(),
            json=payload,
            timeout=10,
        )
        if put_resp.status_code not in (200, 201):
            print(f"GitHub save failed for {filename}: {put_resp.status_code} {put_resp.text}")
            return False
        return True
    except Exception as e:
        print(f"GitHub save error for {filename}: {e}")
        return False


def save_material_batch(course_code, mat_type, files, title):
    """Save multiple files as one titled batch in a single GitHub write."""
    data = load_json(DATA_FILE)
    key = f"{course_code}_{mat_type}"
    if key not in data:
        data[key] = []

    # Get current date/time (e.g., Sep 09, 2026 - 14:32)
    now = datetime.now()
    date_str = now.strftime("%b %d, %Y - %H:%M")

    for f in files:
        item = {
            "file_id": f["file_id"],
            "name": title if len(files) == 1 else f"{title} - {f['file_name']}",
            "content_type": f["content_type"],
            "title": title,
            "date_added": date_str,
        }
        data[key].append(item)
    return save_json(DATA_FILE, data)


def delete_material_by_index(course_code, mat_type, index):
    data = load_json(DATA_FILE)
    key = f"{course_code}_{mat_type}"
    if key in data and 0 <= index < len(data[key]):
        removed = data[key].pop(index)
        if not data[key]:
            del data[key]
        if not save_json(DATA_FILE, data):
            return None
        return removed.get("name", "File")
    return None


# --- PENDING APPROVAL QUEUES & UPLOAD CACHES ---
PENDING_VIDEOS = {}
PENDING_UPLOADS = {}
UPLOAD_STATES = {}
UPDATE_SESSIONS = {}


def add_approved_video(course_code, title, url):
    data = load_json(VIDEOS_FILE)
    if course_code not in data:
        data[course_code] = []

    date_str = datetime.now().strftime("%b %d, %Y - %H:%M")
    data[course_code].append({"title": title, "url": url, "date_added": date_str})
    return save_json(VIDEOS_FILE, data)


# --- CURRICULUM DATABASE ---
CURRICULUM = {
    "2": {
        "1": [
            ("Math2101", "Applied Mathematics III"),
            ("ECEg2201", "Electronics Circuit I"),
            ("EPCE2101", "Fundamentals of Electrical Eng."),
            ("CSEg2101", "Data Structures & Algorithms"),
            ("LART1004", "Geography of Ethiopia & the Horn"),
        ],
        "2": [
            ("ECEg2202", "Electronic Circuit II"),
            ("ECEg2204", "Signals and System Analysis"),
            ("EPCE2202", "Electromagnetic Field"),
            ("ECEg2208", "Eng. Application Software"),
            ("Math2103", "Computational methods"),
            ("Math2201", "Linear Algebra"),
        ],
    },
    "3": {
        "1": [
            ("ECEg3201", "Digital Logic Design"),
            ("EPCE3201", "Network Analysis & Synthesis"),
            ("ECEg3103", "Probability & Random Proc."),
            ("ECEg3205", "Digital Signal Processing"),
            ("LART2002", "Gen. Psychology & Life Skills"),
            ("Phys2208", "Applied Modern Physics"),
        ],
        "2": [
            ("ECEg3202", "Intro to Comm. Systems"),
            ("Phys3202", "Solid State Physics"),
            ("LART1003", "History of Ethiopia & the Horn"),
            ("ECEg3306", "Microelectronic Devices & Circuits"),
            ("ECEg3318", "Optoelectronics"),
            ("CSEg2202", "Object Oriented Programming"),
            ("SEng4208", "Intro to Artificial Intelligence"),
            ("EPCE3304", "Intro to Control Systems"),
            ("EPCE3302", "Intro to Electrical Machines"),
        ],
    },
    "4": {
        "1": [
            ("ECEg4201", "Comp. Architecture & Org."),
            ("ECEg4203", "Digital Communication"),
            ("ECEg4205", "EM Waves & Guide Structure"),
            ("SOSC5003", "Entrepreneurship & Bus. Dev."),
            ("ECEg4206", "Eng. Research & Dev Methodology"),
            ("EPCE3206", "Intro to Power Systems"),
            ("EPCE3207", "Electrical Measurement & Inst."),
        ],
        "2": [
            ("ECEg4202", "Microprocessor & Interfacing"),
            ("ECEg4204", "Antenna & Radio Wave Prop."),
            ("ECEg4208", "Data Comm. & Computer Networks"),
            ("SOSC2002", "Introduction to Economics"),
            ("IETP4203", "Integrated Engineering Project"),
            ("ECEg4310", "Microwave Devices & Systems"),
            ("ECEg4312", "Integrated Circuit Technology"),
        ],
    },
    "5": {
        "1": [
            ("ECEg5201", "Wireless & Mobile Comm."),
            ("ECEg5203", "Capstone Project"),
            ("ECEg5207", "Final Year Project Phase I"),
            ("ECEg5307", "VLSI Design"),
            ("CSEg5307", "Advanced Network"),
            ("ECEg5315", "Embedded & Real Time Systems"),
            ("EPCE4302", "Prog. Logic Controllers & Robotics"),
            ("EPCE4306", "Introduction to Mechatronics"),
            ("ECEg5321", "Biomedical Inst. & Analysis"),
            ("EPCE3202", "Power Electronics"),
        ],
        "2": [
            ("SOSC5011", "Project Mgt. for Engineers"),
            ("ECEg5202", "Final Year Project Phase II"),
            ("ECEg5302", "Optics & Optical Comm."),
            ("ECEg5304", "Analysis & Design of Digital IC"),
            ("ECEg5306", "Telecom Networks & Switching"),
            ("ECEg5308", "Intro to Computer Vision"),
            ("ECEg5310", "Satellite Communication"),
            ("ECEg5312", "Digital Hardware Design"),
            ("ECEg5314", "Digital Image Processing"),
            ("ECEg5316", "Semiconductor Devices"),
        ],
    },
}


# --- INLINE KEYBOARDS ---
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    webapp_url = "https://opio87512-cpu.github.io/scribed/"
    markup.row(InlineKeyboardButton("🚀 Open ASTU ECE Portal", web_app=WebAppInfo(url=webapp_url)))
    markup.row(InlineKeyboardButton("📤 Upload Material (Chat)", callback_data="main_upload"))
    return markup


def year_keyboard(action):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Year II", callback_data=f"{action}y_2"),
        InlineKeyboardButton("Year III", callback_data=f"{action}y_3"),
    )
    markup.row(
        InlineKeyboardButton("Year IV", callback_data=f"{action}y_4"),
        InlineKeyboardButton("Year V", callback_data=f"{action}y_5"),
    )
    markup.row(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_main"))
    return markup


def semester_keyboard(year, action):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Semester I", callback_data=f"{action}s_{year}_1"),
        InlineKeyboardButton("Semester II", callback_data=f"{action}s_{year}_2"),
    )
    back_target = "main_find" if action == 'f' else ("main_upload" if action == 'u' else "back_main")
    markup.row(InlineKeyboardButton("⬅️ Back to Years", callback_data=back_target))
    return markup


def subject_keyboard(year, semester, action):
    markup = InlineKeyboardMarkup()
    if year in CURRICULUM and semester in CURRICULUM[year]:
        for course_code, course_title in CURRICULUM[year][semester]:
            markup.row(InlineKeyboardButton(course_title, callback_data=f"{action}c_{course_code}"))
    else:
        markup.row(InlineKeyboardButton("⚠️ Subjects coming soon!", callback_data="ignore"))
    markup.row(InlineKeyboardButton("⬅️ Back to Semesters", callback_data=f"{action}y_{year}"))
    return markup


def material_type_keyboard(course_code, action):
    markup = InlineKeyboardMarkup()
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
            markup.row(InlineKeyboardButton(f"❌ Delete: {item['name']}", callback_data=f"delitem_{course_code}_{material_type}_{idx}"))
        text = f"Select the file you want to delete for {course_code} ({material_type.upper()}):"
    else:
        text = f"✅ No files remain for {course_code} ({material_type.upper()})."
    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    return text, markup


def group_by_title(course_code, material_type):
    """Group the stored items of a course/type by their folder title.
    Returns a dict: title -> list of absolute indices into data[key]."""
    materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
    groups = {}
    for idx, item in enumerate(materials):
        title = item.get("title") or item.get("name") or "Untitled"
        groups.setdefault(title, []).append(idx)
    return groups, materials


def build_update_folder_list(course_code, material_type):
    """Builds the list of folders (grouped by title) available to update,
    creating a short-lived UPDATE_SESSIONS entry for each folder."""
    groups, materials = group_by_title(course_code, material_type)
    markup = InlineKeyboardMarkup()
    if groups:
        for title, indices in groups.items():
            sess_id = os.urandom(4).hex()
            UPDATE_SESSIONS[sess_id] = {
                "course_code": course_code,
                "material_type": material_type,
                "title": title,
                "indices": indices,
            }
            markup.row(InlineKeyboardButton(f"📁 {title} ({len(indices)} file(s))", callback_data=f"updfolder_{sess_id}"))
        text = f"Select the folder you want to UPDATE for {course_code} ({material_type.upper()}):"
    else:
        text = f"✅ No files found for {course_code} ({material_type.upper()}) to update."
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
    markup.row(InlineKeyboardButton("🔁 Replace Entire Folder", callback_data=f"updwhole_{sess_id}"))

    valid_indices = [i for i in sess["indices"] if i < len(materials)]
    for pos, abs_idx in enumerate(valid_indices):
        item = materials[abs_idx]
        markup.row(InlineKeyboardButton(f"✏️ Update: {item.get('name', 'File')}", callback_data=f"upditem_{sess_id}_{pos}"))

    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    text = (
        f"📁 **{sess['title']}**\n"
        f"Course: {sess['course_code']} • Type: {sess['material_type'].upper()}\n\n"
        f"Choose to replace the whole folder's files at once, or update a single file inside it."
    )
    return text, markup


def process_update_whole(chat_id, files, state):
    sess_id = state.get("sess_id")
    sess = UPDATE_SESSIONS.get(sess_id)
    if not sess:
        bot.send_message(chat_id, "⚠️ This update session expired. Please run /updatefile again.")
        return

    data = load_json(DATA_FILE)
    key = f"{sess['course_code']}_{sess['material_type']}"
    materials = data.get(key, [])

    # Remove the old items belonging to this folder (highest index first, so
    # popping doesn't shift the remaining indices we still need to remove).
    for idx in sorted(sess["indices"], reverse=True):
        if idx < len(materials):
            materials.pop(idx)

    title = sess["title"]
    now = datetime.now().strftime("%b %d, %Y - %H:%M")
    for f in files:
        item = {
            "file_id": f["file_id"],
            "name": title if len(files) == 1 else f"{title} - {f['file_name']}",
            "content_type": f["content_type"],
            "title": title,
            "date_added": now,
        }
        materials.append(item)

    data[key] = materials
    if save_json(DATA_FILE, data):
        bot.send_message(chat_id, f"✅ Folder \"{title}\" was fully replaced with {len(files)} new file(s)!")
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

    data = load_json(DATA_FILE)
    key = f"{sess['course_code']}_{sess['material_type']}"
    materials = data.get(key, [])

    if abs_index >= len(materials):
        bot.send_message(chat_id, "⚠️ That file no longer exists.")
        UPDATE_SESSIONS.pop(sess_id, None)
        return

    item = materials[abs_index]
    old_name = item.get("name", "")
    title = item.get("title", sess["title"])

    item["file_id"] = file_id
    item["content_type"] = content_type
    item["date_added"] = datetime.now().strftime("%b %d, %Y - %H:%M")
    # Keep a consistent name: "<title> - <filename>" when it was part of a
    # multi-file folder, otherwise just the title.
    if " - " in old_name:
        item["name"] = f"{title} - {file_name}"
    else:
        item["name"] = title

    materials[abs_index] = item
    data[key] = materials

    if save_json(DATA_FILE, data):
        bot.send_message(chat_id, f"✅ Successfully updated \"{item['name']}\"!")
    else:
        bot.send_message(chat_id, "⚠️ Failed to save the update to GitHub. Please try again.")

    UPDATE_SESSIONS.pop(sess_id, None)


# --- COMMAND HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("g_"):
        payload = parts[1][2:]
        segments = payload.rsplit("_", 2)
        if len(segments) == 3:
            course_code, material_type, idx_str = segments
            try:
                idx = int(idx_str)
                materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
                if 0 <= idx < len(materials):
                    item = materials[idx]
                    if item.get("content_type") == "photo":
                        bot.send_photo(message.chat.id, item["file_id"], caption=item.get("name", ""))
                    else:
                        bot.send_document(message.chat.id, item["file_id"], caption=item.get("name", ""))
                else:
                    bot.send_message(message.chat.id, "⚠️ That file could not be found — it may have been removed.")
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


@bot.message_handler(commands=['setexam'])
def admin_set_exam(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        bot.reply_to(message, "Usage: /setexam [CourseCode] [YYYY-MM-DD] [Exam Title]")
        return
    code, date_str, title = parts[1], parts[2], parts[3]
    
    data = load_json(EXAMS_FILE)
    data[code] = {"date": date_str, "title": title}
    save_json(EXAMS_FILE, data)
    bot.reply_to(message, f"✅ Countdown for '{title}' ({code}) set to {date_str}.")


@bot.message_handler(commands=['addfile'])
def admin_add_file_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year to ADD official material:", reply_markup=year_keyboard('a'))


@bot.message_handler(commands=['updatefile'])
def admin_update_file_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year of the material you want to UPDATE:", reply_markup=year_keyboard('p'))


@bot.message_handler(commands=['addvideo'])
def admin_add_video_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year to ADD a video link:", reply_markup=year_keyboard('v'))


@bot.message_handler(commands=['deletefile'])
def admin_delete_file_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "Select the Year to DELETE material from:", reply_markup=year_keyboard('d'))


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
            markup.row(InlineKeyboardButton(f"❌ [{course_code}] {v.get('title', 'Video')}", callback_data=f"delvid_{course_code}_{idx}"))

    if not has_videos:
        bot.send_message(message.chat.id, "ℹ️ No approved videos found to delete.")
        return
    bot.send_message(message.chat.id, "Select the video you want to delete:", reply_markup=markup)


# --- CALLBACK HANDLERS ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data == "main_find":
        bot.edit_message_text("Select your academic year to FIND materials:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=year_keyboard('f'))

    elif call.data == "main_upload":
        bot.edit_message_text("Select your academic year to UPLOAD materials:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=year_keyboard('u'))

    elif call.data == "back_main":
        bot.edit_message_text("Welcome to the ASTU ECE Community Bot! 🚀\n\nTap 'Open Portal' for the best experience, or use the chat menus below.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=main_menu_keyboard())

    elif call.data.startswith("fy_") or call.data.startswith("uy_") or call.data.startswith("ay_") or call.data.startswith("dy_") or call.data.startswith("vy_") or call.data.startswith("py_"):
        action = call.data[0]
        year = call.data.split('_')[1]
        roman_years = {"2": "II", "3": "III", "4": "IV", "5": "V"}
        bot.edit_message_text(f"Year {roman_years[year]} Selected.\nChoose your semester:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=semester_keyboard(year, action))

    elif call.data.startswith("fs_") or call.data.startswith("us_") or call.data.startswith("as_") or call.data.startswith("ds_") or call.data.startswith("vs_") or call.data.startswith("ps_"):
        parts = call.data.split('_')
        action = parts[0][0]
        year, semester = parts[1], parts[2]
        bot.edit_message_text("Select the subject:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=subject_keyboard(year, semester, action))

    elif call.data.startswith("fc_") or call.data.startswith("uc_") or call.data.startswith("ac_") or call.data.startswith("dc_") or call.data.startswith("vc_") or call.data.startswith("pc_"):
        parts = call.data.split('_')
        action = parts[0][0]
        course_code = parts[1]

        if action == 'v':
            msg = bot.edit_message_text(
                f"Course: {course_code}\n\n"
                f"Please reply to this message with the **Video Title** and **URL** separated by a new line.\n\n"
                f"Example:\n"
                f"Lecture 1 Introduction\n"
                f"https://youtube.com/watch?v=...",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_admin_add_video, course_code)
        else:
            bot.edit_message_text(f"Course: {course_code}\nSelect the type of material:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=material_type_keyboard(course_code, action))

    elif call.data.startswith("fm_") or call.data.startswith("um_") or call.data.startswith("am_") or call.data.startswith("dm_") or call.data.startswith("pm_"):
        parts = call.data.split('_')
        action = parts[0][0]
        course_code = parts[1]
        material_type = parts[2]

        if action == 'f':
            materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
            if not materials:
                bot.send_message(call.message.chat.id, f"ℹ️ No {material_type.upper()} files available yet for {course_code}.")
            else:
                bot.send_message(call.message.chat.id, f"📚 Found {len(materials)} file(s) for {course_code}:")
                for item in materials:
                    if item.get("content_type") == "photo":
                        bot.send_photo(call.message.chat.id, item["file_id"], caption=item.get("name", ""))
                    else:
                        bot.send_document(call.message.chat.id, item["file_id"], caption=item.get("name", ""))

        elif action == 'u':
            bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
            msg = bot.send_message(
                call.message.chat.id,
                f"📤 **Upload mode started for {course_code} ({material_type.upper()})**\n\n"
                "Please send your file(s) now. You can send as many as you want.\n\n"
                "👇 **When you are done sending, click the button below!**",
                parse_mode="Markdown",
                reply_markup=finish_upload_keyboard()
            )
            UPLOAD_STATES[call.message.chat.id] = {
                "course_code": course_code,
                "material_type": material_type,
                "action": "user",
                "files": [],
                "status_msg_id": msg.message_id
            }

        elif action == 'a':
            bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
            msg = bot.send_message(
                call.message.chat.id,
                f"📥 **Admin save mode started for {course_code} ({material_type.upper()})**\n\n"
                "Please send your file(s) now. You can send as many as you want.\n\n"
                "👇 **When you are done sending, click the button below!**",
                parse_mode="Markdown",
                reply_markup=finish_upload_keyboard()
            )
            UPLOAD_STATES[call.message.chat.id] = {
                "course_code": course_code,
                "material_type": material_type,
                "action": "admin",
                "files": [],
                "status_msg_id": msg.message_id
            }

        elif action == 'd':
            materials = load_json(DATA_FILE).get(f"{course_code}_{material_type}", [])
            if not materials:
                bot.send_message(call.message.chat.id, f"ℹ️ No files found under {course_code} ({material_type.upper()}) to delete.")
            else:
                text, markup = build_delete_list(course_code, material_type)
                bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

        elif action == 'p':
            text, markup = build_update_folder_list(course_code, material_type)
            bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "finish_upload":
        chat_id = call.message.chat.id
        if chat_id not in UPLOAD_STATES:
            bot.answer_callback_query(call.id, "Upload session expired or already finished.")
            bot.delete_message(chat_id, call.message.message_id)
            return

        state = UPLOAD_STATES[chat_id]
        files = state["files"]

        if not files:
            bot.answer_callback_query(call.id, "You haven't sent any files yet! Send them first.", show_alert=True)
            return

        if state["action"] == "admin":
            state["awaiting_title"] = True
            bot.edit_message_text(
                f"✏️ Got {len(files)} file(s). Please type a *title* for this folder.",
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )
            return

        if state["action"] == "update_whole":
            bot.edit_message_text(f"🔄 Replacing the folder with {len(files)} new file(s)...", chat_id=chat_id, message_id=call.message.message_id)
            process_update_whole(chat_id, files, state)
            UPLOAD_STATES.pop(chat_id, None)
            return

        bot.edit_message_text(f"🔄 Processing your {len(files)} file(s)...", chat_id=chat_id, message_id=call.message.message_id)
        process_files(chat_id, files, state, call.from_user)
        UPLOAD_STATES.pop(chat_id, None)

    elif call.data.startswith("delitem_"):
        parts = call.data.split('_')
        course_code, material_type, idx = parts[1], parts[2], int(parts[3])
        deleted_name = delete_material_by_index(course_code, material_type, idx)
        if deleted_name:
            bot.answer_callback_query(call.id, f"✅ Deleted {deleted_name}")
        else:
            bot.answer_callback_query(call.id, "⚠️ Could not delete.", show_alert=True)
        text, markup = build_delete_list(course_code, material_type)
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data.startswith("updfolder_"):
        sess_id = call.data.split('_', 1)[1]
        text, markup = build_update_folder_detail(sess_id)
        if markup is None:
            bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id)
        else:
            bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data.startswith("updwhole_"):
        sess_id = call.data.split('_', 1)[1]
        sess = UPDATE_SESSIONS.get(sess_id)
        if not sess:
            bot.answer_callback_query(call.id, "This update session expired. Run /updatefile again.", show_alert=True)
            return
        bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
        msg = bot.send_message(
            call.message.chat.id,
            f"🔁 **Replacing folder \"{sess['title']}\"**\n\n"
            "Please send the new file(s) now. You can send as many as you want.\n\n"
            "👇 **When you are done sending, click the button below!**",
            parse_mode="Markdown",
            reply_markup=finish_upload_keyboard()
        )
        UPLOAD_STATES[call.message.chat.id] = {
            "course_code": sess["course_code"],
            "material_type": sess["material_type"],
            "action": "update_whole",
            "sess_id": sess_id,
            "files": [],
            "status_msg_id": msg.message_id
        }

    elif call.data.startswith("upditem_"):
        parts = call.data.split('_')
        sess_id, pos = parts[1], int(parts[2])
        sess = UPDATE_SESSIONS.get(sess_id)
        if not sess or pos >= len(sess["indices"]):
            bot.answer_callback_query(call.id, "This update session expired. Run /updatefile again.", show_alert=True)
            return
        abs_index = sess["indices"][pos]
        bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
        msg = bot.send_message(call.message.chat.id, "📥 Please send the new file (or photo) to replace this item:")
        bot.register_next_step_handler(msg, process_update_single_file, sess_id, abs_index)

    elif call.data.startswith("delvid_"):
        parts = call.data.split('_')
        course_code, idx = parts[1], int(parts[2])
        data = load_json(VIDEOS_FILE)
        if course_code in data and 0 <= idx < len(data[course_code]):
            removed = data[course_code].pop(idx)
            if not data[course_code]:
                del data[course_code]
            if save_json(VIDEOS_FILE, data):
                bot.edit_message_text(f"✅ Successfully deleted video: {removed.get('title', 'Video')} from {course_code}!", chat_id=call.message.chat.id, message_id=call.message.message_id)
            else:
                bot.edit_message_text("⚠️ Error: Could not save the deletion.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        else:
            bot.edit_message_text("⚠️ Error: Video not found.", chat_id=call.message.chat.id, message_id=call.message.message_id)

    elif call.data.startswith("approve_vid_"):
        req_id = call.data.split('_')[2]
        if req_id in PENDING_VIDEOS:
            v_data = PENDING_VIDEOS[req_id]
            for admin in ADMIN_IDS:
                try:
                    bot.send_message(admin, f"✅ Video '{v_data['title']}' for {v_data['course']} from {v_data['username']} approved! You can now organize and add it using /addvideo.")
                except Exception:
                    pass
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            del PENDING_VIDEOS[req_id]
        else:
            bot.answer_callback_query(call.id, "Request expired or already handled.")

    elif call.data == "reject_vid":
        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, "❌ Video submission rejected.")
            except Exception:
                pass
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

    elif call.data.startswith("approve_upload_"):
        req_id = call.data.split('_', 2)[2]
        if req_id in PENDING_UPLOADS:
            up = PENDING_UPLOADS[req_id]
            for admin in ADMIN_IDS:
                try:
                    bot.send_message(admin, f"✅ Upload batch from @{up.get('username', 'Student')} approved! You can now download and organize these files, then use /addfile.")
                except Exception:
                    pass
            try:
                bot.send_message(up["chat_id"], f"✅ Good news! Your batch of {len(up['files'])} file(s) for {up['course_code']} has been approved by the admin.\n\nThey will be organized and added to the official portal soon.")
            except Exception:
                pass
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            del PENDING_UPLOADS[req_id]
        else:
            bot.answer_callback_query(call.id, "Request expired or already handled.")

    elif call.data.startswith("reject_upload_"):
        req_id = call.data.split('_', 2)[2]
        up = PENDING_UPLOADS.pop(req_id, None)
        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, "❌ Upload batch rejected.")
            except Exception:
                pass
        if up:
            try:
                bot.send_message(up["chat_id"], f"❌ Your submitted batch of {len(up['files'])} file(s) was not approved.")
            except Exception:
                pass
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)


@bot.message_handler(content_types=['document', 'photo'])
def handle_media(message):
    chat_id = message.chat.id
    if chat_id not in UPLOAD_STATES:
        return

    state = UPLOAD_STATES[chat_id]

    # Do not process media if we are waiting for the title
    if state.get("awaiting_title"):
        return

    file_id = None
    file_name = None
    content_type = "document"

    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "document.pdf"
        content_type = "document"
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
        "message_id": message.message_id
    })

    try:
        count = len(state["files"])
        bot.edit_message_text(
            f"📥 **Collected {count} file(s) so far.**\n\n"
            f"Keep sending more files, or click **Finish Upload** when you are done.",
            chat_id=chat_id,
            message_id=state["status_msg_id"],
            parse_mode="Markdown",
            reply_markup=finish_upload_keyboard()
        )
    except Exception:
        pass


def process_admin_add_video(message, course_code):
    if not message.text:
        bot.reply_to(message, "⚠️ Error: Please send text only. Start over with /addvideo")
        return

    parts = message.text.strip().split('\n', 1)
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Invalid format. You must put the **Title** on the first line and the **URL** on the second line.\n\nStart over with /addvideo", parse_mode="Markdown")
        return

    title = parts[0].strip()
    url = parts[1].strip()

    ok = add_approved_video(course_code, title, url)
    if ok:
        bot.reply_to(message, f"✅ Successfully added video '{title}' to {course_code}!")
        
        # --- BOT DIRECT MESSAGE NOTIFICATIONS ---
        subs = load_json(SUBS_FILE).get(course_code, [])
        if subs:
            alert = (
                f"📺 **New Tutorial Video!**\n\n"
                f"📚 **Course:** {course_code}\n"
                f"📝 **Title:** {title}\n\n"
                f"Open the Portal to watch it."
            )
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🚀 Open App", web_app=WebAppInfo(url="https://opio87512-cpu.github.io/scribed/")))
            
            for uid in subs:
                try:
                    bot.send_message(uid, alert, parse_mode="Markdown", reply_markup=markup)
                except Exception:
                    pass
    else:
        bot.reply_to(message, f"⚠️ Failed to save video to GitHub. Please check your token or server logs.")


@bot.message_handler(
    content_types=['text'],
    func=lambda m: UPLOAD_STATES.get(m.chat.id, {}).get("awaiting_title") and not m.text.startswith('/')
)
def handle_title_input(message):
    chat_id = message.chat.id
    state = UPLOAD_STATES[chat_id]
    title = message.text.strip()

    if not title:
        bot.send_message(chat_id, "Please send a non-empty title.")
        return

    state["title"] = title
    state["awaiting_title"] = False

    files = state["files"]
    bot.send_message(chat_id, f"🔄 Saving \"{title}\" folder ({len(files)} file(s))...")
    process_files(chat_id, files, state, message.from_user, title=title)
    UPLOAD_STATES.pop(chat_id, None)


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
                InputMediaDocument(f["file_id"], caption=caption if i == 0 else None)
                for i, f in enumerate(group)
            ]
            bot.send_media_group(chat_id, media)

    for group in chunks(photos):
        if len(group) == 1:
            bot.send_photo(chat_id, group[0]["file_id"], caption=caption)
        else:
            media = [
                InputMediaPhoto(f["file_id"], caption=caption if i == 0 else None)
                for i, f in enumerate(group)
            ]
            bot.send_media_group(chat_id, media)


def process_files(chat_id, files, state, user, title=None):
    course_code = state["course_code"]
    material_type = state["material_type"]
    action = state["action"]

    if action == "admin":
        ok = save_material_batch(course_code, material_type, files, title or "Untitled")
        if ok:
            bot.send_message(chat_id, f"✅ Saved \"{title}\" ({len(files)} file(s)) under {course_code} ({material_type.upper()})!")
            send_as_album(chat_id, files, caption=title)

            # --- BOT DIRECT MESSAGE NOTIFICATIONS ---
            subs = load_json(SUBS_FILE).get(course_code, [])
            if subs:
                alert = (
                    f"🔔 **New Material Added!**\n\n"
                    f"📚 **Course:** {course_code}\n"
                    f"📂 **Type:** {material_type.upper()}\n"
                    f"📝 **Title:** {title or 'Untitled'}\n\n"
                    f"Open the Portal to download it."
                )
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🚀 Open App", web_app=WebAppInfo(url="https://opio87512-cpu.github.io/scribed/")))
                
                for uid in subs:
                    try:
                        bot.send_message(uid, alert, parse_mode="Markdown", reply_markup=markup)
                    except Exception:
                        pass 

        else:
            bot.send_message(chat_id, "⚠️ Failed to save to GitHub. Please try again.")

    elif action == "user":
        req_id = os.urandom(4).hex()
        PENDING_UPLOADS[req_id] = {
            "course_code": course_code,
            "material_type": material_type,
            "files": files,
            "chat_id": chat_id,
            "username": user.username or "Student",
        }
        admin_text = f"📥 New Chat Upload (Batch of {len(files)} files)\nFrom: @{user.username or 'Student'}\n\nCourse: {course_code}\nType: {material_type.upper()}"

        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, admin_text)
                for f in files:
                    bot.forward_message(admin, chat_id, f["message_id"])
                markup = InlineKeyboardMarkup()
                markup.row(
                    InlineKeyboardButton("✅ Approve All", callback_data=f"approve_upload_{req_id}"),
                    InlineKeyboardButton("❌ Reject All", callback_data=f"reject_upload_{req_id}"),
                )
                bot.send_message(admin, f"Review this batch upload:", reply_markup=markup)
            except Exception:
                pass

        bot.send_message(chat_id, f"✅ Thank you! Your batch of {len(files)} file(s) has been sent for review.")


# --- FLASK SERVER & API ENDPOINTS ---
@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

@app.route('/api/upload', methods=['POST'])
def handle_webapp_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    course = request.form.get('course', 'Unknown')
    mat_type = request.form.get('type', 'Unknown')
    username = request.form.get('username', 'Student')
    chat_id_str = request.form.get('chat_id', '') 
    
    admin_text = f"🌐 WEB APP Upload from {username}\nCourse: {course}\nType: {mat_type.upper()}"
    try:
        # 1. Send to primary admin to generate Telegram file_id
        msg = bot.send_document(ADMIN_IDS[0], file.read(), caption=admin_text, visible_file_name=file.filename)
        file_id = msg.document.file_id
        
        # 2. Add to standard approval queue
        req_id = os.urandom(4).hex()
        PENDING_UPLOADS[req_id] = {
            "course_code": course,
            "material_type": mat_type,
            "files": [{"file_id": file_id, "file_name": file.filename, "content_type": "document"}],
            "chat_id": int(chat_id_str) if chat_id_str.isdigit() else ADMIN_IDS[0],
            "username": username,
        }
        
        # 3. Trigger approval keyboard
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_upload_{req_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload_{req_id}"),
        )
        bot.send_message(ADMIN_IDS[0], "Review the above web upload:", reply_markup=markup)
                
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "success"}), 200

@app.route('/api/upload_video', methods=['POST'])
def handle_video_upload():
    try:
        data = request.json
        if not data or 'course' not in data or 'url' not in data or 'title' not in data:
            return jsonify({"error": "Invalid data"}), 400

        req_id = str(os.urandom(4).hex())
        PENDING_VIDEOS[req_id] = {
            "course": data['course'],
            "title": data['title'],
            "url": data['url'],
            "username": data.get('username', 'Student'),
        }

        admin_text = f"📺 New Video Submission from {data.get('username', 'Student')}\n\nCourse: {data['course']}\nTitle: {data['title']}\nURL: {data['url']}"
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve Video", callback_data=f"approve_vid_{req_id}"),
            InlineKeyboardButton("❌ Reject", callback_data="reject_vid"),
        )

        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, admin_text, reply_markup=markup)
            except Exception:
                pass

        return jsonify({"status": "pending_approval"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/materials', methods=['GET'])
def get_materials():
    return jsonify(load_json(DATA_FILE)), 200

@app.route('/api/videos', methods=['GET'])
def get_videos():
    return jsonify(load_json(VIDEOS_FILE)), 200

@app.route('/api/subscribe', methods=['POST'])
def handle_subscribe():
    data = request.json
    chat_id = str(data.get('chat_id'))
    course = data.get('course')
    is_subbing = data.get('subscribe', True)
    
    subs = load_json(SUBS_FILE)
    if course not in subs: subs[course] = []
    
    if is_subbing and chat_id not in subs[course]:
        subs[course].append(chat_id)
    elif not is_subbing and chat_id in subs[course]:
        subs[course].remove(chat_id)
        
    save_json(SUBS_FILE, subs)
    return jsonify({"status": "success"}), 200

@app.route('/api/subscriptions', methods=['GET'])
def get_subs():
    chat_id = request.args.get('chat_id')
    subs = load_json(SUBS_FILE)
    user_subs = [course for course, users in subs.items() if str(chat_id) in users]
    return jsonify(user_subs), 200

@app.route('/api/exams', methods=['GET'])
def get_exams():
    return jsonify(load_json(EXAMS_FILE)), 200

@app.route("/")
def webhook():
    return "ASTU ECE Bot is running on Render!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        bot.remove_webhook()
        bot.set_webhook(url=render_url + '/' + TOKEN)
    app.run(host="0.0.0.0", port=port)
