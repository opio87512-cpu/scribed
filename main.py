import os
import json
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- CONFIGURATION ---
TOKEN = "8630946224:AAHjvpI_7uzQAhFjJnX5YWBUIVMA7oKrcEg" 
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
CORS(app)

ADMIN_ID = 8429521561 

# --- LOCAL STORAGE & HELPERS ---
DATA_FILE = "materials.json"
VIDEOS_FILE = "videos.json"

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def save_material(course_code, mat_type, file_id, file_name):
    data = load_json(DATA_FILE)
    key = f"{course_code}_{mat_type}"
    if key not in data:
        data[key] = []
    data[key].append({"file_id": file_id, "name": file_name})
    save_json(DATA_FILE, data)

def delete_material_by_index(course_code, mat_type, index):
    data = load_json(DATA_FILE)
    key = f"{course_code}_{mat_type}"
    if key in data and 0 <= index < len(data[key]):
        removed = data[key].pop(index)
        if not data[key]:
            del data[key]
        save_json(DATA_FILE, data)
        return removed.get("name", "File")
    return None

# Temporary storage for video submissions awaiting admin review
PENDING_VIDEOS = {}

def add_approved_video(course_code, title, url):
    data = load_json(VIDEOS_FILE)
    if course_code not in data:
        data[course_code] = []
    data[course_code].append({"title": title, "url": url})
    save_json(VIDEOS_FILE, data)

# --- CURRICULUM DATABASE ---
CURRICULUM = {
    "2": {
        "1": [
            ("Math2101", "Applied Mathematics III"),
            ("ECEg2201", "Electronics Circuit I"),
            ("EPCE2101", "Fundamentals of Electrical Eng."),
            ("CSEg2101", "Data Structures & Algorithms"),
            ("LART1004", "Geography of Ethiopia & the Horn")
        ],
        "2": [
            ("ECEg2202", "Electronic Circuit II"),
            ("ECEg2204", "Signals and System Analysis"),
            ("EPCE2202", "Electromagnetic Field"),
            ("ECEg2208", "Eng. Application Software"),
            ("Math2103", "Computational methods"),
            ("Math2201", "Linear Algebra")
        ]
    },
    "3": {
        "1": [
            ("ECEg3201", "Digital Logic Design"),
            ("EPCE3201", "Network Analysis & Synthesis"),
            ("ECEg3103", "Probability & Random Proc."),
            ("ECEg3205", "Digital Signal Processing"),
            ("LART2002", "Gen. Psychology & Life Skills"),
            ("Phys2208", "Applied Modern Physics")
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
            ("EPCE3302", "Intro to Electrical Machines")
        ]
    },
    "4": {
        "1": [
            ("ECEg4201", "Comp. Architecture & Org."),
            ("ECEg4203", "Digital Communication"),
            ("ECEg4205", "EM Waves & Guide Structure"),
            ("SOSC5003", "Entrepreneurship & Bus. Dev."),
            ("ECEg4206", "Eng. Research & Dev Methodology"),
            ("EPCE3206", "Intro to Power Systems"),
            ("EPCE3207", "Electrical Measurement & Inst.")
        ],
        "2": [
            ("ECEg4202", "Microprocessor & Interfacing"),
            ("ECEg4204", "Antenna & Radio Wave Prop."),
            ("ECEg4208", "Data Comm. & Computer Networks"),
            ("SOSC2002", "Introduction to Economics"),
            ("IETP4203", "Integrated Engineering Project"),
            ("ECEg4310", "Microwave Devices & Systems"),
            ("ECEg4312", "Integrated Circuit Technology")
        ]
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
            ("EPCE3202", "Power Electronics")
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
            ("ECEg5316", "Semiconductor Devices")
        ]
    }
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
    markup.row(InlineKeyboardButton("Year II", callback_data=f"{action}y_2"),
               InlineKeyboardButton("Year III", callback_data=f"{action}y_3"))
    markup.row(InlineKeyboardButton("Year IV", callback_data=f"{action}y_4"),
               InlineKeyboardButton("Year V", callback_data=f"{action}y_5"))
    markup.row(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_main"))
    return markup

def semester_keyboard(year, action):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Semester I", callback_data=f"{action}s_{year}_1"),
               InlineKeyboardButton("Semester II", callback_data=f"{action}s_{year}_2"))
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
    markup.row(InlineKeyboardButton("📝 Note", callback_data=f"{action}m_{course_code}_note"),
               InlineKeyboardButton("📄 Assignment", callback_data=f"{action}m_{course_code}_assignment"))
    markup.row(InlineKeyboardButton("📝 Mid Exam", callback_data=f"{action}m_{course_code}_mid"),
               InlineKeyboardButton("📝 Final Exam", callback_data=f"{action}m_{course_code}_final"))
    markup.row(InlineKeyboardButton("⏳ Test", callback_data=f"{action}m_{course_code}_test"))
    markup.row(InlineKeyboardButton("⬅️ Main Menu", callback_data="back_main"))
    return markup

# --- COMMAND HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
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
        bot.edit_message_text(f"Select the subject:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=subject_keyboard(year, semester, action))
    
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
                    bot.send_document(call.message.chat.id, item["file_id"], caption=item.get("name", ""))
        elif action == 'u':
            msg = bot.send_message(call.message.chat.id, f"Please send the **{material_type.upper()}** document or photo for **{course_code}** now.", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_upload, course_code, material_type)
        elif action == 'a':
            msg = bot.send_message(call.message.chat.id, f"📥 Send official document for **{course_code}** ({material_type.upper()}):", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_admin_save, course_code, material_type)
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

    elif call.data.startswith("delitem_"):
        parts = call.data.split('_')
        course_code, material_type, idx = parts[1], parts[2], int(parts[3])
        deleted_name = delete_material_by_index(course_code, material_type, idx)
        if deleted_name:
            bot.edit_message_text(f"✅ Successfully deleted **{deleted_name}** from `{course_code}` ({material_type.upper()})!", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text("⚠️ Error: File could not be found or already deleted.", chat_id=call.message.chat.id, message_id=call.message.message_id)

    elif call.data.startswith("approve_vid_"):
        req_id = call.data.split('_')[2]
        if req_id in PENDING_VIDEOS:
            v_data = PENDING_VIDEOS[req_id]
            add_approved_video(v_data['course'], v_data['title'], v_data['url'])
            bot.send_message(ADMIN_ID, f"✅ Video approved and added to `{v_data['course']}`!", parse_mode="Markdown")
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            del PENDING_VIDEOS[req_id]
        else:
            bot.answer_callback_query(call.id, "Request expired or already handled.")
    elif call.data == "reject_vid":
        bot.send_message(ADMIN_ID, "❌ Video submission rejected.")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

    elif call.data.startswith("approve_"):
        bot.send_message(ADMIN_ID, "✅ File approved and saved!")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    elif call.data == "reject":
        bot.send_message(ADMIN_ID, "❌ Upload rejected.")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

# --- UPLOAD WORKFLOWS ---
def process_upload(message, course_code, material_type):
    if message.document or message.photo:
        admin_text = (
            f"📥 **New Chat Upload from @{message.from_user.username}**\n\n"
            f"**Course:** {course_code}\n"
            f"**Type:** {material_type.upper()}"
        )
        bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_review"),
            InlineKeyboardButton("❌ Reject", callback_data="reject")
        )
        bot.send_message(ADMIN_ID, "Review this upload:", reply_markup=markup)
        bot.reply_to(message, "Thank you! Your correctly categorized material has been sent to @pede_7 for review.")
    else:
        bot.reply_to(message, "Error: Please upload a valid document or photo. You will need to start the upload process over from the menu.")

def process_admin_save(message, course_code, material_type):
    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "document.pdf"
        save_material(course_code, material_type, file_id, file_name)
        bot.reply_to(message, f"✅ Successfully saved **{file_name}** under `{course_code}` ({material_type.upper()})!", parse_mode="Markdown")
    else:
        bot.reply_to(message, "⚠️ Please send a valid document file.")

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
    
    admin_text = f"🌐 **WEB APP File Upload from {username}**\n\n**Course:** {course}\n**Type:** {mat_type.upper()}"
    try:
        bot.send_document(ADMIN_ID, file.read(), caption=admin_text, visible_file_name=file.filename, parse_mode="Markdown")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/upload_video', methods=['POST'])
def handle_video_upload():
    data = request.json
    if not data or 'course' not in data or 'url' not in data or 'title' not in data:
        return jsonify({"error": "Invalid data"}), 400
    
    req_id = str(os.urandom(4).hex())
    PENDING_VIDEOS[req_id] = {
        "course": data['course'],
        "title": data['title'],
        "url": data['url'],
        "username": data.get('username', 'Student')
    }
    
    admin_text = (
        f"📺 **New Video Submission from {data.get('username', 'Student')}**\n\n"
        f"**Course:** {data['course']}\n"
        f"**Title:** {data['title']}\n"
        f"**URL:** {data['url']}"
    )
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Approve Video", callback_data=f"approve_vid_{req_id}"),
        InlineKeyboardButton("❌ Reject", callback_data="reject_vid")
    )
    
    try:
        bot.send_message(ADMIN_ID, admin_text, reply_markup=markup, parse_mode="Markdown")
        return jsonify({"status": "pending_approval"}), 200
    except Exception as e:
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
