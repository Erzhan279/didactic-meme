import os
import json
import telebot
from flask import Flask, request
import firebase_admin
from firebase_admin import credentials, db

# === 🔐 Telegram токеніңді осында жаз немесе Render Environment-тен ал
BOT_TOKEN = os.getenv("BOT_TOKEN", "8005464032:AAGTBZ99oB9pcF0VeEjDGn20LgRWzHN25T4")
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# === Firebase орнату ===
def initialize_firebase():
    try:
        print("🔄 Firebase қосылып жатыр...")

        firebase_json = os.getenv("FIREBASE_SECRET")

        if not firebase_json and os.path.exists("firebase_secret.json"):
            with open("firebase_secret.json", "r") as f:
                firebase_json = f.read()

        if not firebase_json:
            print("🚫 Firebase secret табылмады!")
            return None, None

        creds_dict = json.loads(firebase_json)
        cred = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(cred, {
            "databaseURL": "https://manybot-kz-default-rtdb.firebaseio.com/"
        })
        print("✅ Firebase сәтті қосылды!")
        users_ref = db.reference("users")
        bots_ref = db.reference("user_bots")
        return users_ref, bots_ref
    except Exception as e:
        print(f"🚫 Firebase қатесі: {e}")
        return None, None


USERS_REF, BOTS_REF = initialize_firebase()


# === Telegram командалары ===

@bot.message_handler(commands=['start'])
def start(message):
    text = (
        "👋 Сәлем, *{0}*!\n\n"
        "Мен — ManyBot KZ 🤖\n"
        "Мен арқылы өзіңнің Telegram ботыңды оңай құра аласың!\n\n"
        "Бастау үшін мәзірден таңда:\n"
        "➡️ /newbot — жаңа бот қосу\n"
        "➡️ /mybots — менің боттарым\n"
        "➡️ /help — көмек нұсқаулығы"
    ).format(message.from_user.first_name)
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

    if USERS_REF:
        USERS_REF.child(str(message.chat.id)).set({
            "username": message.from_user.username,
            "first_name": message.from_user.first_name
        })


@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.send_message(
        message.chat.id,
        "ℹ️ *Көмек*\n\n"
        "1️⃣ /newbot — жаңа бот қосу.\n"
        "2️⃣ /mybots — тіркелген боттарыңды көру.\n"
        "3️⃣ /broadcast — барлық қолданушыға хабарлама жіберу (админге).\n\n"
        "Бот токеніңді BotFather-ден алып, осы ботқа жібер.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['newbot'])
def newbot(message):
    msg = bot.send_message(message.chat.id, "🤖 Боттың *токенін* жіберіңіз:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_new_bot)


def save_new_bot(message):
    token = message.text.strip()
    if not token.startswith(""):
        bot.reply_to(message, "⚠️ Қате токен. Қайта тексеріп көрші.")
        return

    if BOTS_REF:
        BOTS_REF.child(str(message.chat.id)).push({"token": token})
        bot.reply_to(message, "✅ Жаңа бот сәтті қосылды!\nЕнді /mybots командасын қолдан.")


@bot.message_handler(commands=['mybots'])
def my_bots(message):
    if not BOTS_REF:
        bot.reply_to(message, "🚫 Firebase байланысы жоқ.")
        return

    user_bots = BOTS_REF.child(str(message.chat.id)).get()
    if not user_bots:
        bot.reply_to(message, "Сенде әлі боттар жоқ. /newbot командасын қолдан.")
        return

    text = "🤖 *Сенің боттарың:*\n\n"
    for _, bot_data in user_bots.items():
        text += f"🔹 `{bot_data['token']}`\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


# === Фондағы хабарламалар ===

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.send_message(message.chat.id, f"💬 {message.text}")


# === Flask маршруттары ===

@app.route("/", methods=["GET"])
def index():
    return "✅ ManyBot KZ жұмыс істеп тұр!"


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        json_update = request.get_json(force=True)
        update = telebot.types.Update.de_json(json_update)
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        print(f"⚠️ Webhook қатесі: {e}")
        return "Error", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))