import os
import json
import base64
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- CONFIGURATION ---
TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
CORS(app)

ADMIN_ID = 8429521561

# --- GITHUB-BACKED STORAGE ---
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = "opio87512-cpu/scribed"
GITHUB_BRANCH = "main"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/contents"

DATA_FILE = "materials.json"
VIDEOS_FILE = "videos.json"

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

def save_material(course_code, mat_type, file_id, file_name, content_type="document"):
    data = load_json(DATA_FILE)
    key = f"{course_code}_{mat_type}"
    if key not in data:
        data[key] = []
    data[key].append({"file_id": file_id, "name": file_name, "content_type": content_type})
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

# Replaced timers with a direct state dictionary that holds the files
UPLOAD_STATES = {}

def add_approved_video(course_code, title, url):
    data = load_json(VIDEOS_FILE)
    if course_code not in data:
        data[course_code] = []
    data[course_code].append({"title": title, "url": url})
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
    markup.row(InlineKeyboardButton("📚 Find Materials (Chat)", callback_data="main_find"))
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
        "Admin: @pede_7\n\n"
        "Tap 'Open Portal' for the best experience, or use the chat menus below."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['addfile'])
def admin_add_file_start(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.send_message(message.chat.id, "Select the Year to ADD official material:", reply_markup=year_keyboard('a'))

@bot.message_handler(commands=['deletefile'])
def admin_delete_file_start(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.send_message(message.chat.id, "Select the Year to DELETE material from:", reply_markup=year_keyboard('d'))

@bot.message_handler(commands=['deletevideo'])
def admin_delete_video_start(message):
    if message.from_user.id != ADMIN_ID:
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
        bot.edit_message_text("Welcome to the ASTU ECE Community Bot! 🚀\n\nAdmin: @pede_7\n\nTap 'Open Portal' for the best experience, or use the chat menus below.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=main_menu_keyboard())

    elif call.data.startswith("fy_") or call.data.startswith("uy_") or call.data.startswith("ay_") or call.data.startswith("dy_"):
        action = call.data[0]
        year = call.data.split('_')[1]
        roman_years = {"2": "II", "3": "III", "4": "IV", "5": "V"}
        bot.edit_message_text(f"Year {roman_years[year]} Selected.\nChoose your semester:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=semester_keyboard(year, action))

    elif call.data.startswith("fs_") or call.data.startswith("us_") or call.data.startswith("as_") or call.data.startswith("ds_"):
        parts = call.data.split('_')
        action = parts[0][0]
        year, semester = parts[1], parts[2]
        bot.edit_message_text("Select the subject:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=subject_keyboard(year, semester, action))

    elif call.data.startswith("fc_") or call.data.startswith("uc_") or call.data.startswith("ac_") or call.data.startswith("dc_"):
        parts = call.data.split('_')
        action = parts[0][0]
        course_code = parts[1]
        bot.edit_message_text(f"Course: {course_code}\nSelect the type of material:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=material_type_keyboard(course_code, action))

    elif call.data.startswith("fm_") or call.data.startswith("um_") or call.data.startswith("am_") or call.data.startswith("dm_"):
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
            # Initialize upload state
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
                markup = InlineKeyboardMarkup()
                for idx, item in enumerate(materials):
                    markup.row(InlineKeyboardButton(f"❌ Delete: {item['name']}", callback_data=f"delitem_{course_code}_{material_type}_{idx}"))
                markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
                bot.edit_message_text(f"Select the file you want to delete for {course_code} ({material_type.upper()}):", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    # --- NEW: Finish Upload Button handler ---
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
            
        # Update UI to show processing
        bot.edit_message_text(
            f"🔄 Processing your {len(files)} file(s)...", 
            chat_id=chat_id, 
            message_id=call.message.message_id
        )
        
        # Process them
        process_files(chat_id, files, state, call.from_user)
        
        # Clear the state
        UPLOAD_STATES.pop(chat_id, None)

    elif call.data.startswith("delitem_"):
        parts = call.data.split('_')
        course_code, material_type, idx = parts[1], parts[2], int(parts[3])
        deleted_name = delete_material_by_index(course_code, material_type, idx)
        if deleted_name:
            bot.edit_message_text(f"✅ Successfully deleted {deleted_name} from {course_code} ({material_type.upper()})!", chat_id=call.message.chat.id, message_id=call.message.message_id)
        else:
            bot.edit_message_text("⚠️ Error: File could not be found, already deleted, or the save to GitHub failed.", chat_id=call.message.chat.id, message_id=call.message.message_id)

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
                bot.edit_message_text("⚠️ Error: Could not save the deletion to GitHub. Please try again.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        else:
            bot.edit_message_text("⚠️ Error: Video could not be found or already deleted.", chat_id=call.message.chat.id, message_id=call.message.message_id)

    elif call.data.startswith("approve_vid_"):
        req_id = call.data.split('_')[2]
        if req_id in PENDING_VIDEOS:
            v_data = PENDING_VIDEOS[req_id]
            ok = add_approved_video(v_data['course'], v_data['title'], v_data['url'])
            if ok:
                bot.send_message(ADMIN_ID, f"✅ Video approved and added to {v_data['course']}!")
            else:
                bot.send_message(ADMIN_ID, f"⚠️ Video approved, but saving to GitHub failed. Try approving again or check logs.")
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            del PENDING_VIDEOS[req_id]
        else:
            bot.answer_callback_query(call.id, "Request expired or already handled.")
            
    elif call.data == "reject_vid":
        bot.send_message(ADMIN_ID, "❌ Video submission rejected.")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

    elif call.data.startswith("approve_upload_"):
        req_id = call.data.split('_', 2)[2]
        if req_id in PENDING_UPLOADS:
            up = PENDING_UPLOADS[req_id]
            success_count = 0
            
            # Loop through all files in the batch
            for f in up["files"]:
                ok = save_material(up["course_code"], up["material_type"], f["file_id"], f["file_name"], f["content_type"])
                if ok: 
                    success_count += 1
                    
            if success_count > 0:
                bot.send_message(ADMIN_ID, f"✅ {success_count}/{len(up['files'])} files approved and saved under {up['course_code']} ({up['material_type'].upper()})!")
                try:
                    bot.send_message(up["chat_id"], f"✅ Your batch of {success_count} file(s) was approved and added to {up['course_code']} materials!")
                except Exception:
                    pass
            else:
                bot.send_message(ADMIN_ID, "⚠️ Approved, but saving to GitHub failed. Try again or check server logs.")
                
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            del PENDING_UPLOADS[req_id]
        else:
            bot.answer_callback_query(call.id, "Request expired or already handled.")
            
    elif call.data.startswith("reject_upload_"):
        req_id = call.data.split('_', 2)[2]
        up = PENDING_UPLOADS.pop(req_id, None)
        bot.send_message(ADMIN_ID, "❌ Upload batch rejected.")
        if up:
            try:
                bot.send_message(up["chat_id"], f"❌ Your submitted batch of {len(up['files'])} file(s) was not approved.")
            except Exception:
                pass
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)


# --- EXPLICIT BUTTON BATCH MEDIA HANDLER ---
@bot.message_handler(content_types=['document', 'photo'])
def handle_media(message):
    chat_id = message.chat.id
    if chat_id not in UPLOAD_STATES:
        return  # Ignore files if user didn't initiate an upload

    state = UPLOAD_STATES[chat_id]

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

    # Add file to the list
    state["files"].append({
        "file_id": file_id,
        "file_name": file_name,
        "content_type": content_type,
        "message_id": message.message_id
    })
    
    # Update the status message text so they know it was caught
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
        # Ignore telegram rate limits if they upload 20 files instantly
        pass


def process_files(chat_id, files, state, user):
    course_code = state["course_code"]
    material_type = state["material_type"]
    action = state["action"]

    if action == "admin":
        success_count = 0
        for f in files:
            ok = save_material(course_code, material_type, f["file_id"], f["file_name"], f["content_type"])
            if ok: success_count += 1
        
        bot.send_message(chat_id, f"✅ Successfully saved {success_count}/{len(files)} files under {course_code} ({material_type.upper()})!")
        
    elif action == "user":
        req_id = os.urandom(4).hex()
        PENDING_UPLOADS[req_id] = {
            "course_code": course_code,
            "material_type": material_type,
            "files": files,
            "chat_id": chat_id,
            "username": user.username or "Student",
        }
        
        admin_text = (
            f"📥 New Chat Upload (Batch of {len(files)} files)\n"
            f"From: @{user.username or 'Student'}\n\n"
            f"Course: {course_code}\n"
            f"Type: {material_type.upper()}"
        )
        bot.send_message(ADMIN_ID, admin_text)
        
        # Forward every file in the batch to the admin
        for f in files:
            bot.forward_message(ADMIN_ID, chat_id, f["message_id"])
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve All", callback_data=f"approve_upload_{req_id}"),
            InlineKeyboardButton("❌ Reject All", callback_data=f"reject_upload_{req_id}"),
        )
        bot.send_message(ADMIN_ID, f"Review this batch upload:", reply_markup=markup)
        
        bot.send_message(chat_id, f"✅ Thank you! Your batch of {len(files)} file(s) has been sent to @pede_7 for review.")


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

    admin_text = f"🌐 WEB APP File Upload from {username}\n\nCourse: {course}\nType: {mat_type.upper()}"
    try:
        bot.send_document(ADMIN_ID, file.read(), caption=admin_text, visible_file_name=file.filename)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

        admin_text = (
            f"📺 New Video Submission from {data.get('username', 'Student')}\n\n"
            f"Course: {data['course']}\n"
            f"Title: {data['title']}\n"
            f"URL: {data['url']}"
        )
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve Video", callback_data=f"approve_vid_{req_id}"),
            InlineKeyboardButton("❌ Reject", callback_data="reject_vid"),
        )

        bot.send_message(int(ADMIN_ID), admin_text, reply_markup=markup)
        return jsonify({"status": "pending_approval"}), 200
    except Exception as e:
        print(f"CRITICAL ERROR in handle_video_upload: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/materials', methods=['GET'])
def get_materials():
    return jsonify(load_json(DATA_FILE)), 200

@app.route('/api/videos', methods=['GET'])
def get_videos():
    return jsonify(load_json(VIDEOS_FILE)), 200

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
