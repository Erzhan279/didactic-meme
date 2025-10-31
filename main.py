# main.py
# ManyBot KZ — Flask + Firebase + Telegram (requests)
# Жүйе: Flask web сервер + Telegram webhook listeners for user bots
# Автор: Erzhan (қазақша ManyBot көшірмесі)
# Нұсқа: 1.0

import os
import json
import time
import logging
import threading
import traceback
import asyncio
import uuid
from typing import Optional, Dict, Any, List

import requests
from flask import Flask, request, jsonify
from cryptography.fernet import Fernet, InvalidToken
import firebase_admin
from firebase_admin import credentials, db

# ----------------- CONFIG / LOGGING -----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("manybot_kz")

BOT_TOKEN = os.getenv("BOT_TOKEN") or "8005464032:AAGTBZ99oB9pcF0VeEjDGn20LgRWzHN25T4"
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL") or "https://didactic-meme.onrender.com"
MASTER_KEY = os.getenv("MASTER_KEY") or "testkey12345678901234567890123456"
PORT = int(os.getenv("PORT", "10000"))

# ----------------- Flask init -----------------
app = Flask(__name__)

# ----------------- Fernet encryption -----------------
fernet = None
try:
    fernet = Fernet(MASTER_KEY.encode())
except Exception as e:
    logger.warning("⚠️ Fernet кілті жарамсыз, токендер шифрланбайды.")
    fernet = None

def encrypt_token(plain: str) -> str:
    if not fernet:
        return plain
    return fernet.encrypt(plain.encode()).decode()

def decrypt_token(enc: str) -> str:
    if not fernet:
        return enc
    try:
        return fernet.decrypt(enc.encode()).decode()
    except InvalidToken:
        return enc

# ----------------- Telegram helpers -----------------
def tg_send_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        return r.json()
    except Exception as e:
        logger.exception("Telegram send error")
        return None

def set_webhook_for_token(token: str, webhook_url: str) -> Dict[str, Any]:
    try:
        return requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": webhook_url},
            timeout=8,
        ).json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def delete_webhook_for_token(token: str):
    try:
        requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=8)
    except Exception:
        pass

# ----------------- Firebase setup -----------------
def load_firebase_creds() -> Optional[dict]:
    s = os.getenv("FIREBASE_SECRET")
    if s:
        if "\\n" in s:
            s = s.replace("\\n", "\n")
        try:
            return json.loads(s)
        except Exception:
            pass
    if os.path.exists("firebase_secret.json"):
        with open("firebase_secret.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            if "\\n" in data.get("private_key", ""):
                data["private_key"] = data["private_key"].replace("\\n", "\n")
            return data
    return None

def initialize_firebase():
    creds = load_firebase_creds()
    if not creds:
        logger.warning("⚠️ Firebase credentials табылмады.")
        return False
    try:
        firebase_admin.initialize_app(
            credentials.Certificate(creds),
            {"databaseURL": f"https://{creds.get('project_id')}-default-rtdb.firebaseio.com/"},
        )
        logger.info("✅ Firebase сәтті қосылды.")
        return True
    except Exception as e:
        logger.exception("Firebase init error")
        return False

FIREBASE_OK = initialize_firebase()
if FIREBASE_OK:
    ROOT_REF = db.reference("/")
    BOTS_REF = db.reference("bots")
    SUBS_REF = db.reference("subscribers")
else:
    BOTS_REF = SUBS_REF = None

# ----------------- Local fallback DB -----------------
LOCAL_DB = "local_db"
os.makedirs(LOCAL_DB, exist_ok=True)

def local_read(name):
    path = f"{LOCAL_DB}/{name}.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def local_write(name, data):
    path = f"{LOCAL_DB}/{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ----------------- Bot storage -----------------
def save_bot(owner, bot_id, username, token_plain):
    rec = {
        "owner": owner,
        "bot_id": bot_id,
        "username": username,
        "token": encrypt_token(token_plain),
        "created_at": int(time.time()),
    }
    if FIREBASE_OK:
        try:
            ref = BOTS_REF.push(rec)
            return ref.key
        except Exception:
            pass
    data = local_read("bots")
    key = uuid.uuid4().hex
    data[key] = rec
    local_write("bots", data)
    return key

def get_all_bots():
    if FIREBASE_OK:
        try:
            return BOTS_REF.get() or {}
        except Exception:
            pass
    return local_read("bots")

# ----------------- Flask routes -----------------
@app.route("/", methods=["GET"])
def root():
    return "✅ ManyBot KZ is running!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def main_webhook():
    update = request.get_json(force=True)
    if not update:
        return jsonify({"ok": False}), 400

    msg = update.get("message", {})
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = msg.get("text", "")

    if not text:
        return jsonify({"ok": True})

    if text.startswith("/start"):
        tg_send_message(chat_id,
                        "Сәлем! 🇰🇿 Бұл ManyBot KZ.\nБот жасау үшін /addbot деп жазыңыз.")
        return jsonify({"ok": True})

    if text.startswith("/addbot"):
        tg_send_message(chat_id, "BotFather-дан токен алыңыз және /token <TOKEN> деп жазыңыз.")
        return jsonify({"ok": True})

    if text.startswith("/token "):
        token = text.split(" ", 1)[1].strip()
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe").json()
        if not r.get("ok"):
            tg_send_message(chat_id, "❌ Токен жарамсыз.")
            return jsonify({"ok": True})
        me = r["result"]
        key = save_bot(chat_id, me["id"], me["username"], token)
        webhook_url = f"{WEBHOOK_BASE_URL}/u/{chat_id}_{me['id']}"
        set_webhook_for_token(token, webhook_url)
        tg_send_message(chat_id, f"✅ @{me['username']} қосылды!\nWebhook: {webhook_url}\nKey: <code>{key}</code>")
        return jsonify({"ok": True})

    if text.startswith("/bots"):
        all_bots = get_all_bots()
        mine = [v for v in all_bots.values() if v.get("owner") == chat_id]
        if not mine:
            tg_send_message(chat_id, "Сізде бот жоқ.")
        else:
            msgtxt = "\n".join([f"@{b['username']} | ID: {b['bot_id']}" for b in mine])
            tg_send_message(chat_id, msgtxt)
        return jsonify({"ok": True})

    tg_send_message(chat_id, "Белгісіз команда.")
    return jsonify({"ok": True})

@app.route("/u/<path:owner_bot>", methods=["POST"])
def user_webhook(owner_bot):
    payload = request.get_json(force=True)
    owner, botid = owner_bot.split("_")
    msg = payload.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "")

    bots = get_all_bots()
    for k, v in bots.items():
        if str(v.get("owner")) == owner and str(v.get("bot_id")) == botid:
            token = decrypt_token(v["token"])
            if text.lower().startswith("/start"):
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat_id, "text": "Қош келдіңіз! ✅"})
            break
    return jsonify({"ok": True})

# ----------------- Auto webhook -----------------
def set_main_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    payload = {"url": f"{WEBHOOK_BASE_URL}/{BOT_TOKEN}"}
    try:
        res = requests.post(url, json=payload, timeout=10).json()
        logger.info("Main bot webhook result: %s", res)
    except Exception:
        logger.exception("Webhook set error")

threading.Thread(target=set_main_webhook, daemon=True).start()

# ----------------- Run Flask -----------------
if __name__ == "__main__":
    logger.info("🌐 Webhook listening on port %s", PORT)
    app.run(host="0.0.0.0", port=PORT)
