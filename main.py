import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from flask import Flask, request

# --- CONFIGURATION ---
# Token hardcoded as requested. 
# Warning: If your GitHub repository is public, anyone can see and use this token!
TOKEN = "8969647277:AAF3jTCal-ZdqYqghm7ln0mrcTZUcTg3o6U" 
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# IMPORTANT: Bots require a numeric ID to forward files, not a username.
# Step 1: Deploy this code.
# Step 2: Send /myid to your bot on Telegram.
# Step 3: Replace 123456789 with the number the bot gives you.
ADMIN_ID = 123456789 

# --- INLINE KEYBOARDS ---
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📚 Find Materials", callback_data="find_materials"))
    markup.row(InlineKeyboardButton("📤 Upload Material", callback_data="upload_material"))
    markup.row(InlineKeyboardButton("🧮 Grade Calculator", web_app=WebAppInfo(url="https://your-hosted-calculator.com")))
    return markup

def materials_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("C++ & Data Structures", callback_data="mat_cpp"))
    markup.row(InlineKeyboardButton("MATLAB & Signals", callback_data="mat_matlab"))
    markup.row(InlineKeyboardButton("Microcontrollers", callback_data="mat_micro"))
    markup.row(InlineKeyboardButton("Embedded Systems", callback_data="mat_embed"))
    markup.row(InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
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
    # A helper command to easily find your numeric ID for the ADMIN_ID variable
    bot.reply_to(message, f"Your numeric Telegram ID is:\n\n{message.from_user.id}\n\nPaste this into the ADMIN_ID variable in your code.")

# --- CALLBACK HANDLERS ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data == "find_materials":
        bot.edit_message_text("Select a subject category:", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              reply_markup=materials_keyboard())
                              
    elif call.data == "upload_material":
        bot.send_message(call.message.chat.id, "Please send the file (PDF, DOCX, ZIP) you want to share with the community.")
        bot.register_next_step_handler(call.message, process_upload)
        
    elif call.data == "back_main":
        bot.edit_message_text("Welcome to the ASTU ECE Community Bot! 🚀\n\nAdmin: @pede_7\n\nWhat would you like to do?", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              reply_markup=main_menu_keyboard())
                              
    elif call.data.startswith("mat_"):
        subject = call.data.split('_')[1]
        bot.send_message(call.message.chat.id, f"Fetching resources for {subject}...\n(Admins can link drive files here!)")

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
