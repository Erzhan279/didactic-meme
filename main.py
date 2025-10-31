# main.py
# ManyBot KZ — Flask + Firebase + Telegram (requests) нұсқасы
# Жүйе: Flask web сервер + Telegram webhook listeners for user bots.
# Автор: (сенің project) — қазақша ManyBot көшірмесі
# Нұсқа: 1.0

import os
import json
import time
import logging
import threading
import traceback
from typing import Optional, Dict, Any, List

from flask import Flask, request, jsonify
import requests

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    if not update:
        return jsonify({"ok": False}), 400

    try:
        asyncio.run(dp.feed_update(bot, types.Update(**update)))
    except Exception as e:
        print("❌ Error handling update:",e)
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True})

# firebase admin
import firebase_admin
from firebase_admin import credentials, db

# crypto for token encryption
from cryptography.fernet import Fernet, InvalidToken

# ----------------- CONFIG / LOGGING -----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("manybot_kz")

BOT_TOKEN = os.getenv("BOT_TOKEN")  # ManyBot main bot токені
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")  # e.g. https://yourapp.onrender.com
MASTER_KEY = os.getenv("MASTER_KEY")  # Fernet key string

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN орнатылмаған. Қызмет жартылай жұмыс істей алады (кей функциялар шектеулі).")
if not WEBHOOK_BASE_URL:
    logger.warning("WEBHOOK_BASE_URL орнатылмаған. Webhookтар автоматты түрде орнатылмауы мүмкін.")
if not MASTER_KEY:
    logger.warning("MASTER_KEY орнатылмаған. Токендер шифрланбайды (қауіпсіздік тәуекелі).")

# Fernet объектісін жасау
fernet = None
if MASTER_KEY:
    try:
        fernet = Fernet(MASTER_KEY.encode())
    except Exception as e:
        logger.exception("MASTER_KEY жарамсыз: Fernet кілті дұрыс емес.")
        fernet = None

# Telegram send function (main bot)
TELEGRAM_API = "https://api.telegram.org/bot" + (BOT_TOKEN or "DUMMY")
def tg_send_message(chat_id: int, text: str, parse_mode: str="HTML", reply_markup: Optional[dict]=None):
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN жоқ — хабар жіберілмейді.")
        return None
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(url, json=payload, timeout=10)
        try:
            return r.json()
        except Exception:
            return {"ok": False, "status_code": r.status_code, "text": r.text}
    except Exception as e:
        logger.exception("Telegram send error: %s", e)
        return None

# ----------------- FIREBASE INITIALIZATION -----------------
# Behavior:
# - Егер FIREBASE_SECRET ENV бар болса, оны оқып JSON ретінде қарайды (private_key ішіндегі \\n -> \n ауыстырылады).
# - Әйтпесе репо ішіндегі firebase_secret.json файлын оқиды.
# - Сәтті қосылса DB references береді.

FIREBASE_DB_URL_DEFAULT = os.getenv("FIREBASE_DB_URL", "")  # егер қойылса override жасайды

def load_firebase_creds_from_env_or_file() -> Optional[dict]:
    # 1) ENV
    s = os.getenv("FIREBASE_SECRET")
    if s:
        try:
            # кейде env ішінде \n сияқты escaped newline болады -> нақты newline-ға ауыстыру
            if "\\n" in s:
                s = s.replace("\\n", "\n")
            parsed = json.loads(s)
            return parsed
        except Exception as e:
            logger.exception("FIREBASE_SECRET ENV парсингі сәтсіз: %s", e)
            # мүмкін multiline емес, тек raw файл жолы болар
    # 2) файл
    if os.path.exists("firebase_secret.json"):
        try:
            with open("firebase_secret.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                # ensure private_key has real newlines
                if "private_key" in data and "\\n" in data["private_key"]:
                    data["private_key"] = data["private_key"].replace("\\n", "\n")
                return data
        except Exception as e:
            logger.exception("firebase_secret.json оқу қатесі: %s", e)
    return None

def initialize_firebase() -> bool:
    try:
        logger.info("🔄 Firebase байланысын тексеру...")
        creds = load_firebase_creds_from_env_or_file()
        if not creds:
            logger.warning("Firebase credentials таппадым — Firebase қолданылмайды.")
            return False
        # DB url
        db_url = FIREBASE_DB_URL_DEFAULT or f"https://{creds.get('project_id')}-default-rtdb.firebaseio.com/"
        # Build credential object (accept dict)
        cred = credentials.Certificate(creds)
        firebase_admin.initialize_app(cred, {"databaseURL": db_url})
        logger.info("✅ Firebase сәтті қосылды (%s)", db_url)
        return True
    except Exception as e:
        logger.exception("🚫 Firebase қатесі:")
        return False

FIREBASE_OK = initialize_firebase()
# Provide top-level references if firebase works
if FIREBASE_OK:
    try:
        ROOT_REF = db.reference("/")
        BOTS_REF = db.reference("bots")
        SUBS_REF = db.reference("subscribers")
        TEMPLATES_REF = db.reference("templates")
        ADMINS_REF = db.reference("admins")
        INFO_REF = db.reference("info")
    except Exception as e:
        logger.exception("Firebase reference жасау қатесі: %s", e)
        FIREBASE_OK = False
        BOTS_REF = SUBS_REF = TEMPLATES_REF = ADMINS_REF = INFO_REF = None
else:
    BOTS_REF = SUBS_REF = TEMPLATES_REF = ADMINS_REF = INFO_REF = None

# ----------------- HELPERS: encrypt/decrypt token -----------------
def encrypt_token(plain: str) -> str:
    if not fernet:
        # егер шифрлау жоқ болса — қайтарып, бірақ логта ескерту беру
        logger.warning("Fernet жоқ: токен шифрланбайды (қауіпсіздік тәуекелі).")
        return plain
    return fernet.encrypt(plain.encode()).decode()

def decrypt_token(enc: str) -> str:
    if not fernet:
        return enc
    try:
        return fernet.decrypt(enc.encode()).decode()
    except InvalidToken:
        logger.exception("Token decrypt failed: InvalidToken")
        raise

# ----------------- FLASK APP -----------------
app = Flask(__name__)

# ----------------- Utilities for DB (Firebase or fallback file) -----------------
# We'll use Firebase if available; otherwise fallback to local JSON files.
LOCAL_DB_DIR = "local_db"
if not os.path.exists(LOCAL_DB_DIR):
    os.makedirs(LOCAL_DB_DIR, exist_ok=True)

def read_local_json(name: str) -> dict:
    path = os.path.join(LOCAL_DB_DIR, name + ".json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_local_json(name: str, data: dict):
    path = os.path.join(LOCAL_DB_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# BOT storage schema
# Firebase: BOTS_REF -> push key -> { owner: user_id, bot_id: int, username: str, token: enc, created_at: ... }
# Local fallback: local_db/bots.json : { "<key>": { ... } }

import uuid
def gen_key() -> str:
    return uuid.uuid4().hex

# Save bot (to Firebase or local)
def save_bot_record(owner: int, bot_id: int, username: str, token_plain: str) -> str:
    record = {
        "owner": int(owner),
        "bot_id": int(bot_id),
        "username": username or "",
        "token": encrypt_token(token_plain),
        "created_at": int(time.time())
    }
    if FIREBASE_OK and BOTS_REF:
        try:
            ref = BOTS_REF.push(record)
            key = ref.key
            logger.info("Bot saved to Firebase with key %s", key)
            return key
        except Exception:
            logger.exception("Firebase push бот сақтау қатесі, локалға жазамын.")
    # fallback local
    d = read_local_json("bots")
    key = gen_key()
    d[key] = record
    write_local_json("bots", d)
    logger.info("Bot saved to local DB with key %s", key)
    return key

def get_all_bots() -> dict:
    if FIREBASE_OK and BOTS_REF:
        try:
            data = BOTS_REF.get() or {}
            return data
        except Exception:
            logger.exception("Firebase get_all_bots қатесі")
    return read_local_json("bots")

def get_bot_by_key(key: str) -> Optional[dict]:
    if FIREBASE_OK and BOTS_REF:
        try:
            return BOTS_REF.child(key).get()
        except Exception:
            logger.exception("Firebase get bot by key error")
    d = read_local_json("bots")
    return d.get(key)

def delete_bot_by_key(key: str):
    if FIREBASE_OK and BOTS_REF:
        try:
            BOTS_REF.child(key).delete()
            # also delete subscribers under /subscribers/<key>
            if SUBS_REF:
                try:
                    SUBS_REF.child(key).delete()
                except Exception:
                    pass
            return
        except Exception:
            logger.exception("Firebase delete bot error, fallback to local")
    d = read_local_json("bots")
    if key in d:
        del d[key]
    # remove subscribers
    subs = read_local_json("subscribers")
    if key in subs:
        del subs[key]
    write_local_json("bots", d)
    write_local_json("subscribers", subs)

def add_subscriber_record(bot_key: str, user_id: int):
    user_id = str(user_id)
    if FIREBASE_OK and SUBS_REF:
        try:
            SUBS_REF.child(bot_key).child(user_id).set(True)
            return
        except Exception:
            logger.exception("Firebase add_subscriber error, fallback local")
    subs = read_local_json("subscribers")
    if bot_key not in subs:
        subs[bot_key] = {}
    subs[bot_key][user_id] = True
    write_local_json("subscribers", subs)

def get_subscribers_for_bot_key(bot_key: str) -> List[int]:
    if FIREBASE_OK and SUBS_REF:
        try:
            res = SUBS_REF.child(bot_key).get() or {}
            return [int(k) for k in res.keys()] if isinstance(res, dict) else []
        except Exception:
            logger.exception("Firebase get_subscribers error")
    subs = read_local_json("subscribers")
    data = subs.get(bot_key, {}) or {}
    return [int(k) for k in data.keys()]

def count_total_subscribers() -> int:
    if FIREBASE_OK and SUBS_REF:
        try:
            all_subs = SUBS_REF.get() or {}
            s = 0
            for k, v in (all_subs.items() if isinstance(all_subs, dict) else []):
                s += len(v or {})
            return s
        except Exception:
            logger.exception("Firebase count_total_subscribers error")
    subs = read_local_json("subscribers")
    total = 0
    for v in subs.values():
        total += len(v)
    return total

# TEMPLATES
def save_template(owner: int, title: str, content: str) -> str:
    rec = {"owner": int(owner), "title": title, "content": content, "created_at": int(time.time())}
    if FIREBASE_OK and TEMPLATES_REF:
        try:
            ref = TEMPLATES_REF.push(rec)
            return ref.key
        except Exception:
            logger.exception("Firebase push template error")
    d = read_local_json("templates")
    key = gen_key()
    d[key] = rec
    write_local_json("templates", d)
    return key

def get_templates_for_owner(owner: int) -> dict:
    if FIREBASE_OK and TEMPLATES_REF:
        try:
            allt = TEMPLATES_REF.get() or {}
            # filter by owner
            return {k: v for k, v in (allt.items() if isinstance(allt, dict) else []) if int(v.get("owner", 0)) == int(owner)}
        except Exception:
            logger.exception("Firebase get templates error")
    d = read_local_json("templates")
    return {k: v for k, v in d.items() if int(v.get("owner", 0)) == int(owner)}

# Admins
def is_admin(user_id: int) -> bool:
    if FIREBASE_OK and ADMINS_REF:
        try:
            data = ADMINS_REF.child(str(user_id)).get()
            return bool(data)
        except Exception:
            logger.exception("ADMINS_REF check error")
    d = read_local_json("admins")
    return str(user_id) in d and d[str(user_id)]

def add_admin_record(user_id: int):
    if FIREBASE_OK and ADMINS_REF:
        try:
            ADMINS_REF.child(str(user_id)).set(True)
            return
        except Exception:
            logger.exception("ADMINS_REF add admin error")
    d = read_local_json("admins")
    d[str(user_id)] = True
    write_local_json("admins", d)

def remove_admin_record(user_id: int):
    if FIREBASE_OK and ADMINS_REF:
        try:
            ADMINS_REF.child(str(user_id)).delete()
            return
        except Exception:
            logger.exception("ADMINS_REF remove admin error")
    d = read_local_json("admins")
    if str(user_id) in d:
        del d[str(user_id)]
    write_local_json("admins", d)

# ----------------- Telegram helper for user bots (requests) -----------------
def send_msg_via_token(token: str, chat_id: int, text: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.json()
    except Exception as e:
        logger.exception("send_msg_via_token error: %s", e)
        return {"ok": False, "error": str(e)}

def set_webhook_for_token(token: str, webhook_url: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    payload = {"url": webhook_url}
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.json()
    except Exception as e:
        logger.exception("set_webhook_for_token error: %s", e)
        return {"ok": False, "error": str(e)}

def delete_webhook_for_token(token: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    try:
        r = requests.post(url, timeout=8, data={})
        return r.json()
    except Exception as e:
        logger.exception("delete_webhook_for_token error: %s", e)
        return {"ok": False, "error": str(e)}

# ----------------- Flask routes: Main bot commands via webhook from Telegram (ManyBot main) -----------------
# We'll expect Telegram to send updates for the main bot to /<BOT_TOKEN> (configure in BotFather or manually setWebhook).
# If you deploy and want setWebhook for main bot automatically, call setWebhook_for_main_bot()

@app.route("/", methods=["GET"])
def index():
    return "🎬 ManyBot KZ — Flask + Firebase ready"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def main_bot_webhook():
    """Handle main bot updates (users interacting with ManyBot)"""
    try:
        update = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "invalid-json"}), 400

    # only handle messages
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify({"ok": True, "info": "no-message"}), 200

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "") or ""
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    # Basic commands parsing (simple)
    try:
        if text.startswith("/start"):
            reply = (
                "Сәлем! 🇰🇿 *ManyBot KZ* — қазақ тіліндегі ManyBot көшірмесі.\n\n"
                "🔹 /addbot — жаңа бот қосу (BotFather-тен токен жібер)\n"
                "🔹 /bots — өз боттарың\n"
                "🔹 /newpost — хабар тарату\n"
                "🔹 /subscribers — жазылушылар саны\n"
                "🔹 /templates — шаблондар\n"
                "🔹 /help — көмек\n"
            )
            tg_send_message(chat_id, reply, parse_mode="HTML")
            return jsonify({"ok": True})
        if text.startswith("/help"):
            help_text = (
                "<b>ManyBot KZ командалары</b>\n"
                "/addbot — жаңа бот қосу. Private чатта /token BOT_TOKEN жібер.\n"
                "/token <TOKEN> — BotFather берген токенді жіберу (тек жеке чатта)\n"
                "/bots — өз боттарыңды көру\n"
                "/deletebot <bot_key> — ботты жою\n"
                "/newpost — хабар тарату (ID + мәтін)\n"
                "/subscribers — барлық жазылушылар саны\n"
                "/templates — шаблон басқару\n"
                "/addtemplate — шаблон қосу\n"
                "/admins — админ басқару (админ ғана)\n"
            )
            tg_send_message(chat_id, help_text)
            return jsonify({"ok": True})
        if text.startswith("/addbot"):
            tg_send_message(chat_id, "Жаңа бот қосу: BotFather-дан алған токенді жеке чатта /token <TOKEN> деп жіберіңіз.")
            return jsonify({"ok": True})
        # token handler (only in private chat)
        if text.startswith("/token "):
            # қауіпсіздікті ескер: тек жеке чатта қабылдау керек
            if chat.get("type") != "private":
                tg_send_message(chat_id, "Токенді тек жеке чатта жіберіңіз (privacy).")
                return jsonify({"ok": True})
            token = text.split(" ", 1)[1].strip()
            if ":" not in token:
                tg_send_message(chat_id, "Токен қате форматта. BotFather-дан тура көшіріп қойыңыз.")
                return jsonify({"ok": True})
            tg_send_message(chat_id, "Токен тексерілуде...")
            # getMe
            try:
                r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8).json()
                if not r.get("ok"):
                    tg_send_message(chat_id, "Токен жарамсыз немесе Telegram қолжетімсіз.")
                    return jsonify({"ok": True})
                me = r.get("result")
                bot_key = save_bot_record(owner=user_id, bot_id=me.get("id"), username=me.get("username"), token_plain=token)
                # set webhook for user bot to our app
                webhook_url = f"{WEBHOOK_BASE_URL}/u/{user_id}_{me.get('id')}"
                set_res = set_webhook_for_token(token, webhook_url)
                logger.info("Set webhook result: %s", set_res)
                tg_send_message(chat_id, f"✅ @{me.get('username')} қосылды!\nWebhook: {webhook_url}\nDB_KEY: {bot_key}")
            except Exception as e:
                logger.exception("Token check exception: %s", e)
                tg_send_message(chat_id, "Токенді тексеру кезінде қате пайда болды.")
            return jsonify({"ok": True})
        if text.startswith("/bots"):
            all_bots = get_all_bots() or {}
            my = []
            for k, v in (all_bots.items() if isinstance(all_bots, dict) else []):
                if int(v.get("owner", 0)) == int(user_id):
                    my.append((k, v))
            if not my:
                tg_send_message(chat_id, "Сізде бот жоқ. /addbot арқылы қосыңыз.")
                return jsonify({"ok": True})
            reply = "Сіздің боттарыңыз:\n\n"
            for k, v in my:
                reply += f"DB_KEY: <code>{k}</code>\n@{v.get('username','')}\nbot_id: {v.get('bot_id')}\n\n"
            tg_send_message(chat_id, reply)
            return jsonify({"ok": True})
        if text.startswith("/deletebot"):
            parts = text.split()
            if len(parts) < 2:
                tg_send_message(chat_id, "Қолдану: /deletebot <DB_KEY>")
                return jsonify({"ok": True})
            key = parts[1].strip()
            rec = get_bot_by_key(key)
            if not rec:
                tg_send_message(chat_id, "Мұндай бот табылмады.")
                return jsonify({"ok": True})
            if int(rec.get("owner")) != int(user_id) and not is_admin(user_id):
                tg_send_message(chat_id, "Сіз бұл ботты өшіре алмайсыз (иесі емессіз).")
                return jsonify({"ok": True})
            # try delete webhook
            try:
                token_enc = rec.get("token")
                token = decrypt_token(token_enc)
                delete_webhook_for_token(token)
            except Exception:
                logger.exception("delete webhook failed, continue")
            delete_bot_by_key(key)
            tg_send_message(chat_id, "✅ Бот өшірілді.")
            return jsonify({"ok": True})
        if text.startswith("/subscribers"):
            total = count_total_subscribers()
            tg_send_message(chat_id, f"Барлығы {total} жазылушы бар.")
            return jsonify({"ok": True})
        if text.startswith("/newpost"):
            tg_send_message(chat_id, "Хабар тарату: Алдымен DB_KEY жазыңыз, одан кейін жаңа жолда хабар мәтінін жазыңыз.\n\nФормат:\n<DB_KEY>\n<мәтін>")
            return jsonify({"ok": True})
        # if message looks like broadcast: first line DB_KEY, rest text
        # quick heuristic: if message contains newline and first token looks like DB key
        if "\n" in text:
            first, rest = text.split("\n", 1)
            first = first.strip()
            if len(first) >= 8:  # crude: DB_KEY length
                bot_rec = get_bot_by_key(first)
                if bot_rec:
                    # check permission
                    if int(bot_rec.get("owner")) != int(user_id) and not is_admin(user_id):
                        tg_send_message(chat_id, "Сіз бұл боттың иесі емессіз, тарату жасауға құқыңыз жоқ.")
                        return jsonify({"ok": True})
                    token_enc = bot_rec.get("token")
                    try:
                        token = decrypt_token(token_enc)
                    except Exception:
                        tg_send_message(chat_id, "Токен дешифрлеуде қате. Админге хабарласыңыз.")
                        return jsonify({"ok": True})
                    subs = get_subscribers_for_bot_key(first)
                    sent = 0
                    for uid in subs:
                        try:
                            send_msg_via_token(token, int(uid), rest)
                            sent += 1
                        except Exception:
                            continue
                    tg_send_message(chat_id, f"✅ {sent} адамға хабар жіберілді.")
                    return jsonify({"ok": True})
        # шаблондар қосу: /addtemplate
        if text.startswith("/addtemplate"):
            # expected format:
            # /addtemplate
            # TITLE
            # CONTENT
            # we will accept multiline following command in same message (client may send)
            if "\n" in text:
                _, rest = text.split("\n", 1)
                if "\n" in rest:
                    title, content = rest.split("\n", 1)
                else:
                    title = rest.strip()
                    content = ""
                save_template(user_id, title.strip() or "Без названия", content.strip())
                tg_send_message(chat_id, "✅ Шаблон сақталды.")
            else:
                tg_send_message(chat_id, "Қолдану: /addtemplate\\n<TITLE>\\n<CONTENT> (немесе бір жолда)")
            return jsonify({"ok": True})
        if text.startswith("/templates"):
            temps = get_templates_for_owner(user_id)
            if not temps:
                tg_send_message(chat_id, "Шаблондар жоқ.")
            else:
                out = ""
                for k, v in temps.items():
                    out += f"ID:{k}\n{v.get('title')}\n{v.get('content')}\n\n"
                tg_send_message(chat_id, out[:4000])
            return jsonify({"ok": True})
        # admin add/remove
        if text.startswith("/addadmin"):
            if not is_admin(user_id):
                tg_send_message(chat_id, "Сіз ManyBot админі емессіз.")
                return jsonify({"ok": True})
            parts = text.split()
            if len(parts) < 2:
                tg_send_message(chat_id, "Қолдану: /addadmin <user_id>")
                return jsonify({"ok": True})
            try:
                new_id = int(parts[1])
                add_admin_record(new_id)
                tg_send_message(chat_id, f"✅ {new_id} админге қосылды.")
            except Exception as e:
                tg_send_message(chat_id, "Қате: user id дұрыс емес.")
            return jsonify({"ok": True})
        if text.startswith("/removeadmin"):
            if not is_admin(user_id):
                tg_send_message(chat_id, "Сіз ManyBot админі емессіз.")
                return jsonify({"ok": True})
            parts = text.split()
            if len(parts) < 2:
                tg_send_message(chat_id, "Қолдану: /removeadmin <user_id>")
                return jsonify({"ok": True})
            try:
                rem = int(parts[1])
                remove_admin_record(rem)
                tg_send_message(chat_id, f"✅ {rem} админдер тізімінен алынды.")
            except Exception:
                tg_send_message(chat_id, "Қате user id.")
            return jsonify({"ok": True})

    except Exception as e:
        logger.exception("Main webhook handling error: %s", e)
        try:
            tg_send_message(chat_id, "Серверде қате пайда болды. Админге хабарлаңыз.")
        except Exception:
            pass
        return jsonify({"ok": False, "error": "exception"}), 500

    # unknown message: be quiet
    return jsonify({"ok": True, "info": "unhandled"})

# ----------------- Webhook endpoint for user bots -----------------
# For each user bot we set webhook to: {WEBHOOK_BASE_URL}/u/{owner}_{botid}
# Telegram will send updates there — we must handle /start to register subscriber

@app.route("/u/<owner_bot>", methods=["POST"])
def user_bot_webhook(owner_bot):
    # owner_bot is like '12345_67890' where first part owner, second botid
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "invalid-json"}), 400

    # find bot record by owner & botid
    try:
        if "_" not in owner_bot:
            logger.warning("Bad owner_bot path: %s", owner_bot)
            return jsonify({"ok": False}), 400
        owner_str, botid_str = owner_bot.split("_", 1)
    except Exception:
        return jsonify({"ok": False}), 400

    # find bot key (firebase/local)
    all_bots = get_all_bots() or {}
    found_key = None
    found_rec = None
    for k, v in (all_bots.items() if isinstance(all_bots, dict) else []):
        if str(v.get("owner")) == owner_str and str(v.get("bot_id")) == botid_str:
            found_key = k
            found_rec = v
            break
    if not found_rec:
        logger.warning("Webhook update for unknown bot path %s", owner_bot)
        return jsonify({"ok": False}), 404

    # process update (we only care about message->/start and maybe text to collect join)
    try:
        update = payload
        message = update.get("message") or update.get("edited_message") or {}
        if not message:
            return jsonify({"ok": True, "info": "no-message"})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "") or ""
        # If user sends /start - register as subscriber
        if isinstance(text, str) and text.strip().lower().startswith("/start"):
            # add subscriber
            add_subscriber_record(found_key, chat_id)
            # greet
            try:
                token_enc = found_rec.get("token")
                try:
                    token = decrypt_token(token_enc)
                except Exception:
                    # if can't decrypt, token might be plain
                    token = token_enc
                send_msg_via_token(token, chat_id, "Сіз осы ботқа жазылдыңыз! Қош келдіңіз ✅")
            except Exception:
                logger.exception("send greeting error")
            return jsonify({"ok": True})
        # Otherwise ignore, or implement auto responses here...
    except Exception:
        logger.exception("user_bot_webhook processing error: %s", traceback.format_exc())
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True})

# ----------------- Utility: set webhook for main bot -----------------
def set_main_bot_webhook():
    if not BOT_TOKEN or not WEBHOOK_BASE_URL:
        logger.warning("set_main_bot_webhook skipped: BOT_TOKEN or WEBHOOK_BASE_URL missing")
        return None
    webhook_url = f"{WEBHOOK_BASE_URL}/{BOT_TOKEN}"
    try:
        res = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", json={"url": webhook_url}, timeout=8).json()
        logger.info("Main bot setWebhook result: %s", res)
        return res
    except Exception:
        logger.exception("set_main_bot_webhook error")
        return None

# ----------------- Startup actions -----------------
def startup_tasks():
    logger.info("Startup tasks running...")
    # set main bot webhook automatically
    if WEBHOOK_BASE_URL and BOT_TOKEN:
        logger.info("Trying to set webhook for main bot...")
        set_main_bot_webhook()
    else:
        logger.info("WEBHOOK_BASE_URL or BOT_TOKEN missing - skipping main webhook set.")

# Run startup tasks in background thread (so Flask can start quickly)
threading.Thread(target=startup_tasks, daemon=True).start()

# ----------------- Run Flask -----------------
if __name__ == "__main__":
    print("🌐 Webhook listening on port 10000")
    app.run(host="0.0.0.0", port=10000)
