from flask import Flask, request
import requests, threading, os, json, time
from firebase_utils import initialize_firebase

app = Flask(__name__)

# 🔐 Негізгі BotZhasau токен (өз Manybot-ың)
BOT_TOKEN = "YOUR_MAIN_BOT_TOKEN"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# 🔥 Firebase қосу
BOTS_REF, USERS_REF = initialize_firebase()

# === 📤 Telegram хабар жіберу ===
def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = {"keyboard": buttons, "resize_keyboard": True}
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

# === 🌐 BotZhasau webhook ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def botzhasau_webhook():
    data = request.get_json()
    if not data or "message" not in data:
        return "no message"

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    user_ref = USERS_REF.child(str(chat_id))
    user_data = user_ref.get() or {}

    # 🔹 1. Алғашқы старт
    if text.lower() == "/start":
        send_message(chat_id,
            "🤖 <b>BotZhasau</b> жүйесіне қош келдің!\n\n"
            "Бот жасау үшін маған өзіңнің Telegram BotFather токеніңді жібер ⤵️"
        )
        return "ok"

    # 🔹 2. Егер токен форматта болса — тіркеу
    if ":" in text and len(text) > 30:
        user_ref.set({"token": text})
        send_message(chat_id, "✅ Бот токен сақталды! Енді /add командасымен жауап орнат.")
        return "ok"

    # 🔹 3. Команда қосу
    if text.lower().startswith("/add"):
        parts = text.split(" ", 2)
        if len(parts) < 3:
            send_message(chat_id, "Формат: /add команда жауап")
        else:
            cmd, reply = parts[1], parts[2]
            user_commands = user_data.get("commands", {})
            user_commands[cmd] = reply
            user_ref.update({"commands": user_commands})
            send_message(chat_id, f"✅ Команда '{cmd}' сақталды!")
        return "ok"

    # 🔹 4. Бот тізімі
    if text.lower() == "/mybot":
        token = user_data.get("token")
        if token:
            send_message(chat_id, f"🤖 Сенің ботың:\n<code>{token}</code>")
        else:
            send_message(chat_id, "Бот тіркелмеген 😅")
        return "ok"

    send_message(chat_id, "ℹ️ Нұсқаулық:\n/start — бастау\n/add — команда қосу\n/mybot — токенді көру")
    return "ok"

# === 🌐 Пайдаланушы боттарының webhook-тары ===
@app.route("/<token>", methods=["POST"])
def user_bot_webhook(token):
    data = request.get_json()
    if not data or "message" not in data:
        return "no message"

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    # 🔍 Firebase-тан бот иесін табу
    all_users = USERS_REF.get() or {}
    owner_id = None
    commands = {}
    for uid, info in all_users.items():
        if info.get("token") == token:
            owner_id = uid
            commands = info.get("commands", {})
            break

    if not owner_id:
        return "no owner"

    # 🔹 Команда табу
    for cmd, reply in commands.items():
        if text.lower().startswith(cmd.lower()):
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": reply}
            )
            return "ok"

    # 🔹 Егер команда табылмаса
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "Менің ием бұл командаға жауап орнатпаған 😅"}
    )
    return "ok"

@app.route("/")
def home():
    return "🤖 BotZhasau Flask сервері жұмыс істеп тұр ✅"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
