import os
import json
import telebot
from flask import Flask, request
import firebase_admin
from firebase_admin import credentials, db

# ======================================================
# 🔥 Firebase инициализациясы
# ======================================================

def initialize_firebase():
    try:
        print("🔄 Firebase байланысын тексеру...")

        firebase_json = os.getenv("FIREBASE_SECRET")

        # Егер Render ENV ішінде жоқ болса — GitHub реподан оқимыз
        if not firebase_json and os.path.exists("firebase_secret.json"):
            print("📁 Firebase файлдан оқылуда...")
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
        memory_ref = db.reference("memory")
        return users_ref, memory_ref

    except Exception as e:
        print(f"🚫 Firebase қатесі: {e}")
        return None, None

USERS_REF, MEMORY_REF = initialize_firebase()

# ======================================================
# 🤖 Telegram Bot конфигурациясы
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN:
    print("🚫 BOT_TOKEN табылмады!")
else:
    bot = telebot.TeleBot(BOT_TOKEN)

app = Flask(__name__)

# ======================================================
# 🧠 Командалар тізімі
# ======================================================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "Аты жоқ"

    if USERS_REF:
        USERS_REF.child(user_id).set({
            "username": username,
            "id": user_id
        })
        bot.reply_to(message, f"Сәлем, @{username}! 👋\n\nБұл ManyBot 🇰🇿\nМен сенің жеке Telegram ботыңды жасауға көмектесемін!")
    else:
        bot.reply_to(message, "⚠️ Firebase байланысы орнатылмады!")

@bot.message_handler(commands=["help"])
def help_cmd(message):
    text = (
        "🧭 Командалар тізімі:\n\n"
        "/start - Ботты бастау\n"
        "/help - Көмек алу\n"
        "/makebot - Жаңа бот жасау нұсқаулығы\n"
        "/about - ManyBot туралы ақпарат"
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=["makebot"])
def makebot_cmd(message):
    text = (
        "🤖 Өз ботыңды жасау үшін:\n"
        "1️⃣ @BotFather аш.\n"
        "2️⃣ /newbot деп жаз.\n"
        "3️⃣ Атың мен логинін таңда.\n"
        "4️⃣ Маған токеніңді жібер.\n\n"
        "Мен сенің ботыңды іске қосып берем 🔥"
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=["about"])
def about_cmd(message):
    bot.reply_to(message, "🇰🇿 ManyBot KZ — Telegram бот жасауға арналған қазақша көмекші.\nҚұрастырған: *BotZhasau*", parse_mode="Markdown")

# ======================================================
# 📩 Токен қабылдау (бот жасау)
# ======================================================

@bot.message_handler(func=lambda msg: "token" in msg.text.lower())
def handle_token(message):
    token = message.text.strip()
    user_id = str(message.from_user.id)

    if USERS_REF:
        USERS_REF.child(user_id).update({"bot_token": token})
        bot.reply_to(message, "✅ Токен сақталды! Енді мен сенің ботыңды іске қосамын 🚀")
    else:
        bot.reply_to(message, "⚠️ Firebase байланысы жоқ!")

# ======================================================
# 🌍 Flask маршруты (Webhook)
# ======================================================

@app.route("/", methods=["GET"])
def home():
    return "🤖 ManyBot KZ жұмыс істеп тұр!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    json_update = request.get_json()
    bot.process_new_updates([telebot.types.Update.de_json(json_update)])
    return "OK", 200

# ======================================================
# 🚀 Бағдарламаны іске қосу
# ======================================================

if __name__ == "__main__":
    if WEBHOOK_URL:
        bot.remove_webhook()
        bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
        print(f"✅ Webhook орнатылды: {WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        print("⚠️ WEBHOOK_URL орнатылмаған!")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
