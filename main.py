import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from flask import Flask, request

# --- CONFIGURATION ---
TOKEN = "8969647277:AAF3jTCal-ZdqYqghm7ln0mrcTZUcTg3o6U" 
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Replace with your numeric ID after sending /myid to the bot
ADMIN_ID = 123456789 

# --- CURRICULUM DATABASE ---
CURRICULUM = {
    "2": {
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
            ("LART2002", "Gen. Psychology and Life Skills"),
            ("Phys2208", "Applied Modern Physics")
        ],
        "2": [
            ("ECEg3202", "Intro to Communication Systems"),
            ("Phys3202", "Solid State Physics"),
            ("LART1003", "History of Ethiopia and the Horn"),
            ("ElectiveI", "Major Elective I"),
            ("ElectiveII", "Major Elective II"),
            ("ElectiveIII", "Major Elective III")
        ]
    }
}

# --- INLINE KEYBOARDS ---
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📚 Find Materials", callback_data="main_find"))
    markup.row(InlineKeyboardButton("📤 Upload Material", callback_data="main_upload"))
    markup.row(InlineKeyboardButton("🧮 Grade Calculator", web_app=WebAppInfo(url="https://your-hosted-calculator.com")))
    return markup

# We pass an 'action' ('f' for find, 'u' for upload) to track what the user is trying to do
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
    
    back_target = "main_find" if action == 'f' else "main_upload"
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
        "What would you like to do?"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['myid'])
def send_id(message):
    bot.reply_to(message, f"Your numeric Telegram ID is:\n\n{message.from_user.id}\n\nPaste this into the ADMIN_ID variable.")

# --- CALLBACK HANDLERS ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    # --- 1. MAIN MENU ACTIONS ---
    if call.data == "main_find":
        bot.edit_message_text("Select your academic year to FIND materials:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=year_keyboard('f'))
    elif call.data == "main_upload":
        bot.edit_message_text("Select your academic year to UPLOAD materials:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=year_keyboard('u'))
    elif call.data == "back_main":
        bot.edit_message_text("Welcome to the ASTU ECE Community Bot! 🚀\n\nAdmin: @pede_7\n\nWhat would you like to do?", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=main_menu_keyboard())

    # --- 2. YEAR SELECTED ---
    elif call.data.startswith("fy_") or call.data.startswith("uy_"):
        action = call.data[0] # 'f' or 'u'
        year = call.data.split('_')[1]
        roman_years = {"2": "II", "3": "III", "4": "IV", "5": "V"} 
        bot.edit_message_text(f"Year {roman_years[year]} Selected.\nChoose your semester:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=semester_keyboard(year, action))

    # --- 3. SEMESTER SELECTED ---
    elif call.data.startswith("fs_") or call.data.startswith("us_"):
        parts = call.data.split('_')
        action = parts[0][0]
        year = parts[1]
        semester = parts[2]
        bot.edit_message_text(f"Select the subject:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=subject_keyboard(year, semester, action))

    # --- 4. SUBJECT SELECTED ---
    elif call.data.startswith("fc_") or call.data.startswith("uc_"):
        parts = call.data.split('_')
        action = parts[0][0]
        course_code = parts[1]
        bot.edit_message_text(f"Course: {course_code}\nSelect the type of material:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=material_type_keyboard(course_code, action))

    # --- 5. MATERIAL TYPE SELECTED ---
    elif call.data.startswith("fm_") or call.data.startswith("um_"):
        parts = call.data.split('_')
        action = parts[0][0]
        course_code = parts[1]
        material_type = parts[2]
        
        if action == 'f':
            # User wants to download
            bot.send_message(call.message.chat.id, f"Fetching {material_type.upper()} materials for {course_code}...\n(Admin @pede_7 will link database files here)")
        elif action == 'u':
            # User wants to upload
            msg = bot.send_message(call.message.chat.id, f"Please send the **{material_type.upper()}** document for **{course_code}** now.", parse_mode="Markdown")
            # Passes the specific course and type into the upload function!
            bot.register_next_step_handler(msg, process_upload, course_code, material_type)

    # --- ADMIN APPROVALS ---
    elif call.data.startswith("approve_"):
        bot.send_message(ADMIN_ID, f"✅ File approved and saved!")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        
    elif call.data == "reject":
        bot.send_message(ADMIN_ID, "❌ Upload rejected.")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

# --- UPLOAD WORKFLOW ---
def process_upload(message, course_code, material_type):
    if message.document:
        # Now the admin gets exact details about the file!
        admin_text = (
            f"📥 **New Upload from @{message.from_user.username}**\n\n"
            f"**Course:** {course_code}\n"
            f"**Type:** {material_type.upper()}"
        )
        bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{message.message_id}"),
            InlineKeyboardButton("❌ Reject", callback_data="reject")
        )
        bot.send_message(ADMIN_ID, "Review this upload:", reply_markup=markup)
        bot.reply_to(message, "Thank you! Your correctly categorized material has been sent to @pede_7 for review.")
    else:
        bot.reply_to(message, "Error: Please upload a valid document file. You will need to start the upload process over from the menu.")

# --- WEBHOOK & FLASK SERVER FOR RENDER ---
@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

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
