import telebot
import os
from flask import Flask
import threading

# Pulls the token securely from Render's environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(8969647277:AAG4RC0IxDRLMwr_VIzU-z_3VxQZlUd9ubo)
app = Flask(__name__)

# This dummy web server keeps Render happy
@app.route('/')
def home():
    return "Bot is running!"

# Your actual bot logic
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, "I received your link: " + message.text)

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    # Start the bot in a background thread
    threading.Thread(target=run_bot).start()
    # Start the web server on the port Render assigns
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
