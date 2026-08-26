import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from flask import Flask, request

# --- CONFIGURATION ---
TOKEN = "8969647277:AAF3jTCal-ZdqYqghm7ln0mrcTZUcTg3o6U" 
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# IMPORTANT: Send /myid to your bot on Telegram, then replace this number
ADMIN_ID = 123456789 

# --- INLINE KEYBOARDS ---
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📚 Find Materials", callback_data="find_materials"))
    markup.row(InlineKeyboardButton("📤 Upload Material", callback_data="upload_material"))
    markup.row(InlineKeyboardButton("🧮 Grade Calculator", web_app=WebAppInfo(url="https://your-hosted-calculator.com")))
    return markup

# New Keyboard: Select Year
def year_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Year II", callback_data="year_2"),
               InlineKeyboardButton("Year III", callback_data="year_3"))
    markup.row(InlineKeyboardButton("Year IV", callback_data="year_4"),
               InlineKeyboardButton("Year V", callback_data="year_5"))
    markup.row(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_main"))
    return markup

# New Keyboard: Select Semester based on the chosen Year
def semester_keyboard(year):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Semester I", callback_data=f"sem_{year}_1"),
               InlineKeyboardButton("Semester II", callback_data=f"sem_{year}_2"))
    markup.row(InlineKeyboardButton("⬅️ Back to Years", callback_data="find_materials"))
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
    bot.reply_to(message, f"Your numeric Telegram ID is:\n\n{message.from_user.id}\n\nPaste this into the ADMIN_ID variable in your code.")

# --- CALLBACK HANDLERS ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    # 1. Main Menu -> Find Materials (Shows Years)
    if call.data == "find_materials":
        bot.edit_message_text("Select your academic year:", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              reply_markup=year_keyboard())
                              
    # 2. Year Selection -> Shows Semesters
    elif call.data.startswith("year_"):
        selected_year = call.data.split('_')[1]
        # Roman numeral mapping for display purposes
        roman_years = {"2": "II", "3": "III", "4": "IV", "5": "V"} 
        
        bot.edit_message_text(f"Year {roman_years[selected_year]} Selected.\nChoose your semester:", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              reply_markup=semester_keyboard(selected_year))

    # 3. Semester Selection -> Fetch Materials
    elif call.data.startswith("sem_"):
        parts = call.data.split('_')
        year = parts[1]
        semester = parts[2]
        roman_years = {"2": "II", "3": "III", "4": "IV", "5": "V"}
        roman_sems = {"1": "I", "2": "II"}
        
        bot.send_message(call.message.chat.id, f"Fetching resources for Year {roman_years[year]} Semester {roman_sems[semester]}...\n(Admins can link specific course files here!)")

    # Upload Workflow
    elif call.data == "upload_material":
        bot.send_message(call.message.chat.id, "Please send the file (PDF, DOCX, ZIP) you want to share with the community.")
        bot.register_next_step_handler(call.message, process_upload)
        
    # Back to Main Menu
    elif call.data == "back_main":
        bot.edit_message_text("Welcome to the ASTU ECE Community Bot! 🚀\n\nAdmin: @pede_7\n\nWhat would you like to do?", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              reply_markup=main_menu_keyboard())

    # Admin Approvals
    elif call.data.startswith("approve_"):
        msg_id = call.data.split('_')[1]
        bot.send_message(ADMIN_ID, f"✅ File approved and added to database!")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        
    elif call.data == "reject":
        bot.send_message(ADMIN_ID, "❌ Upload rejected.")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

# --- UPLOAD WORKFLOW ---
def process_upload(message):
    if message.document:
        bot.send_message(ADMIN_ID, f"New material submission from @{message.from_user.username}:")
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{message.message_id}"),
            InlineKeyboardButton("❌ Reject", callback_data="reject")
        )
        bot.send_message(ADMIN_ID, "Review this upload:", reply_markup=markup)
        
        bot.reply_to(message, "Thank you! Your material has been sent to @pede_7 for review.")
    else:
        bot.reply_to(message, "Please upload a valid document file. Try clicking 'Upload Material' again.")

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
