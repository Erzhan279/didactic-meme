#!/usr/bin/env python3
# coding: utf-8
"""
ManyBot KZ - Flask + Firebase версиясы
Функциялар:
 - Main bot webhook: /<BOT_TOKEN>  (ManyBot басты басқарушы боты)
 - User bot webhooks: /u/<owner>_<botid>
 - Командалар: /addbot, /token, /bots, /deletebot, /newpost, /subscribers,
               /addtemplate, /templates, /addadmin, /removeadmin, /help
 - Firebase credentials: ENV FIREBASE_SECRET (JSON) немесе firebase_secret.json файл
 - Токендер шифрлау: опционалды MASTER_KEY (Fernet)
 - Локал fallback: local_db/*.json (егер Firebase қолжетімсіз болса)
"""

import os
import json
import time
import logging
import traceback
import uuid
from typing import Optional, Dict, Any, List

from flask import Flask, request, jsonify
import requests

# Optional crypto
try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_AVAILABLE = True
except Exception:
    CRYPTO_AVAILABLE = False

# Firebase
try:
    import firebase_admin
    from firebase_admin import credentials, db
    FIREBASE_PY_AVAILABLE = True
except Exception:
    FIREBASE_PY_AVAILABLE = False

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("manybot_kz")

# ---------------- config from ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")                 # main ManyBot token (BotFather)
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")   # e.g. https://yourapp.onrender.com
PORT = int(os.getenv("PORT", "10000"))
MASTER_KEY = os.getenv("MASTER_KEY")               # optional Fernet key (base64)
FIREBASE_DB_URL_ENV = os.getenv("FIREBASE_DB_URL") # optional override

# Warnings for missing but continue (we'll still run, but limited)
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN орнатылмаған — кейбір функциялар жұмыс істемеуі мүмкін.")
if not WEBHOOK_BASE_URL:
    logger.warning("WEBHOOK_BASE_URL орнатылмаған — вебхук автоматты түрде орнатылмайды.")

# Setup Fernet if provided
fernet = None
if MASTER_KEY:
    if not CRYPTO_AVAILABLE:
        logger.warning("cryptography орнатылмаған — MASTER_KEY еленбейді.")
    else:
        try:
            fernet = Fernet(MASTER_KEY.encode())
            logger.info("🔐 Fernet шифрлау қолжетімді.")
        except Exception:
            logger.exception("MASTER_KEY жарамсыз — Fernet құру сәтсіз.")

# Telegram helpers (requests)
def telegram_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"

def send_message_with_token(token: str, chat_id: int, text: str, parse_mode: str="HTML") -> Dict[str, Any]:
    try:
        r = requests.post(telegram_api_url(token, "sendMessage"),
                          json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
                          timeout=8)
        return r.json()
    except Exception as e:
        logger.exception("send_message_with_token error: %s", e)
        return {"ok": False, "error": str(e)}

def get_me(token: str) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(telegram_api_url(token, "getMe"), timeout=8).json()
        return r.get("result") if r.get("ok") else None
    except Exception as e:
        logger.exception("get_me error: %s", e)
        return None

def set_webhook_for_token(token: str, url: str) -> Dict[str, Any]:
    try:
        r = requests.post(telegram_api_url(token, "setWebhook"), json={"url": url}, timeout=8)
        return r.json()
    except Exception as e:
        logger.exception("set_webhook_for_token error: %s", e)
        return {"ok": False, "error": str(e)}

def delete_webhook_for_token(token: str) -> Dict[str, Any]:
    try:
        r = requests.post(telegram_api_url(token, "deleteWebhook"), timeout=8)
        return r.json()
    except Exception as e:
        logger.exception("delete_webhook_for_token error: %s", e)
        return {"ok": False, "error": str(e)}

# ---------------- Firebase init (env or file) ----------------
def load_firebase_creds() -> Optional[dict]:
    # 1) ENV FIREBASE_SECRET (JSON string). May have escaped \\n in private_key.
    s = os.getenv("FIREBASE_SECRET")
    if s:
        try:
            if "\\n" in s:
                s = s.replace("\\n", "\n")
            parsed = json.loads(s)
            return parsed
        except Exception:
            logger.exception("FIREBASE_SECRET ENV парсинг қатесі.")
    # 2) file firebase_secret.json in repo
    if os.path.exists("firebase_secret.json"):
        try:
            with open("firebase_secret.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            if "private_key" in data and "\\n" in data["private_key"]:
                data["private_key"] = data["private_key"].replace("\\n", "\n")
            return data
        except Exception:
            logger.exception("firebase_secret.json оқу қатесі.")
    return None

FIREBASE_OK = False
BOTS_REF = SUBS_REF = TEMPLATES_REF = ADMINS_REF = INFO_REF = None

if FIREBASE_PY_AVAILABLE:
    creds_dict = load_firebase_creds()
    if creds_dict:
        try:
            db_url = FIREBASE_DB_URL_ENV or f"https://{creds_dict.get('project_id')}-default-rtdb.firebaseio.com/"
            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred, {"databaseURL": db_url})
            BOTS_REF = db.reference("bots")
            SUBS_REF = db.reference("subscribers")
            TEMPLATES_REF = db.reference("templates")
            ADMINS_REF = db.reference("admins")
            INFO_REF = db.reference("info")
            FIREBASE_OK = True
            logger.info("✅ Firebase инициализация сәтті: %s", db_url)
        except Exception:
            logger.exception("Firebase инициализация сәтсіз.")
    else:
        logger.info("Firebase credentials табылмады — локал fallback пайдаланылады.")
else:
    logger.info("firebase_admin пакеті орнатылмаған — локал fallback пайдаланылады.")

# ---------------- Local fallback storage ----------------
LOCAL_DB_DIR = "local_db"
os.makedirs(LOCAL_DB_DIR, exist_ok=True)

def read_local(name: str) -> dict:
    p = os.path.join(LOCAL_DB_DIR, name + ".json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_local(name: str, data: dict):
    p = os.path.join(LOCAL_DB_DIR, name + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def gen_key() -> str:
    return uuid.uuid4().hex

# ---------------- Storage helpers (Firebase or local) ----------------
def save_bot_record(owner: int, bot_id: int, username: str, token_plain: str) -> str:
    rec = {
        "owner": int(owner),
        "bot_id": int(bot_id),
        "username": username or "",
        "token": (encrypt_token(token_plain) if fernet else token_plain),
        "created_at": int(time.time())
    }
    if FIREBASE_OK and BOTS_REF:
        try:
            ref = BOTS_REF.push(rec)
            return ref.key
        except Exception:
            logger.exception("Firebase push failed, falling back to local.")
    d = read_local("bots")
    k = gen_key()
    d[k] = rec
    write_local("bots", d)
    return k

def get_all_bots() -> dict:
    if FIREBASE_OK and BOTS_REF:
        try:
            return BOTS_REF.get() or {}
        except Exception:
            logger.exception("Firebase get_all_bots failed")
    return read_local("bots")

def get_bot_by_key(key: str) -> Optional[dict]:
    if FIREBASE_OK and BOTS_REF:
        try:
            return BOTS_REF.child(key).get()
        except Exception:
            logger.exception("Firebase get bot failed")
    return read_local("bots").get(key)

def delete_bot_by_key(key: str):
    if FIREBASE_OK and BOTS_REF:
        try:
            BOTS_REF.child(key).delete()
            if SUBS_REF:
                try:
                    SUBS_REF.child(key).delete()
                except Exception:
                    pass
            return
        except Exception:
            logger.exception("Firebase delete bot error, falling back to local.")
    d = read_local("bots")
    if key in d:
        del d[key]
        write_local("bots", d)
    subs = read_local("subscribers")
    if key in subs:
        del subs[key]
        write_local("subscribers", subs)

def add_subscriber(bot_key: str, user_id: int):
    if FIREBASE_OK and SUBS_REF:
        try:
            SUBS_REF.child(bot_key).child(str(user_id)).set(True)
            return
        except Exception:
            logger.exception("Firebase add_subscriber failed, fallback to local.")
    subs = read_local("subscribers")
    if bot_key not in subs:
        subs[bot_key] = {}
    subs[bot_key][str(user_id)] = True
    write_local("subscribers", subs)

def get_subscribers(bot_key: str) -> List[int]:
    if FIREBASE_OK and SUBS_REF:
        try:
            d = SUBS_REF.child(bot_key).get() or {}
            return [int(k) for k in d.keys()] if isinstance(d, dict) else []
        except Exception:
            logger.exception("Firebase get_subscribers failed")
    subs = read_local("subscribers")
    d = subs.get(bot_key, {}) or {}
    return [int(k) for k in d.keys()]

def count_total_subscribers() -> int:
    if FIREBASE_OK and SUBS_REF:
        try:
            allsubs = SUBS_REF.get() or {}
            total = 0
            if isinstance(allsubs, dict):
                for k, v in allsubs.items():
                    if isinstance(v, dict):
                        total += len(v)
            return total
        except Exception:
            logger.exception("Firebase count_total_subscribers failed")
    subs = read_local("subscribers")
    total = 0
    for v in subs.values():
        total += len(v)
    return total

def save_template(owner: int, title: str, content: str) -> str:
    rec = {"owner": int(owner), "title": title, "content": content, "created_at": int(time.time())}
    if FIREBASE_OK and TEMPLATES_REF:
        try:
            ref = TEMPLATES_REF.push(rec)
            return ref.key
        except Exception:
            logger.exception("Firebase push template failed")
    d = read_local("templates")
    k = gen_key()
    d[k] = rec
    write_local("templates", d)
    return k

def get_templates(owner: int) -> dict:
    if FIREBASE_OK and TEMPLATES_REF:
        try:
            alld = TEMPLATES_REF.get() or {}
            return {k: v for k, v in (alld.items() if isinstance(alld, dict) else []) if int(v.get("owner", 0)) == int(owner)}
        except Exception:
            logger.exception("Firebase get_templates failed")
    d = read_local("templates")
    return {k: v for k, v in d.items() if int(v.get("owner", 0)) == int(owner)}

def is_admin(user_id: int) -> bool:
    if FIREBASE_OK and ADMINS_REF:
        try:
            v = ADMINS_REF.child(str(user_id)).get()
            return bool(v)
        except Exception:
            logger.exception("Firebase is_admin check failed")
    d = read_local("admins")
    return str(user_id) in d and d[str(user_id)]

def add_admin(user_id: int):
    if FIREBASE_OK and ADMINS_REF:
        try:
            ADMINS_REF.child(str(user_id)).set(True)
            return
        except Exception:
            logger.exception("Firebase add_admin failed")
    d = read_local("admins"); d[str(user_id)] = True; write_local("admins", d)

def remove_admin(user_id: int):
    if FIREBASE_OK and ADMINS_REF:
        try:
            ADMINS_REF.child(str(user_id)).delete()
            return
        except Exception:
            logger.exception("Firebase remove_admin failed")
    d = read_local("admins")
    if str(user_id) in d:
        del d[str(user_id)]
        write_local("admins", d)

# ----------------- encryption helpers ----------------
def encrypt_token(plain: str) -> str:
    if not fernet:
        return plain
    try:
        return fernet.encrypt(plain.encode()).decode()
    except Exception:
        logger.exception("encrypt_token failed")
        return plain

def decrypt_token(enc: str) -> str:
    if not fernet:
        return enc
    try:
        return fernet.decrypt(enc.encode()).decode()
    except InvalidToken:
        logger.exception("InvalidToken when decrypting")
        raise
    except Exception:
        logger.exception("decrypt_token failed")
        raise

# ----------------- Flask app & routes -----------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    return "✅ ManyBot KZ running"

# Main bot webhook - ManyBot main receives updates here
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def main_bot_webhook():
    # This endpoint is where Telegram posts updates for the MAIN ManyBot (BOT_TOKEN)
    update = request.get_json(silent=True)
    if not update:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    try:
    message = update.get("message") or update.get("edited_message") or {}
    logger.info(f"📨 Incoming message: {json.dumps(message, ensure_ascii=False)[:300]}")
except Exception as e:
    logger.exception(f"⚠️ Қате пайда болды: {e}")
        if not message:
            return jsonify({"ok": True, "info": "no-message"})

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        from_user = message.get("from", {}) or {}
        user_id = from_user.get("id")

        # Commands handling
        # /start
        if text.startswith("/start"):
            reply = (
                "Сәлем! ManyBot KZ 🇰🇿\n\n"
                "Командалар:\n"
                "/addbot — жаңа бот қосу\n"
                "/token <TOKEN> — BotFather-дан алынған токенді жіберу (тек жеке чат)\n"
                "/bots — өз боттарың\n"
                "/newpost — хабар тарату (ID + мәтін)\n"
                "/subscribers — жалпы жазылушылар саны\n"
                "/templates — шаблондар\n"
                "/addtemplate — шаблон қосу\n"
                "/help — көмек\n"
            )
            if BOT_TOKEN:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": reply, "parse_mode": "HTML"}, timeout=8)
            return jsonify({"ok": True})

        if text.startswith("/help"):
            help_text = (
                "<b>ManyBot KZ командалары</b>\n"
                "/addbot — жаңа бот қосу (private чатта /token жібер)\n"
                "/token <TOKEN> — токен жіберу\n"
                "/bots — өз боттарыңызды көру\n"
                "/deletebot <DB_KEY> — ботты өшіру\n"
                "/newpost — хабар тарату (бір хабарда: DB_KEY\\nМӘТІН)\n"
                "/subscribers — жазылушылар саны\n"
                "/addtemplate — шаблон қосу (бір хабарда: /addtemplate\\nTITLE\\nCONTENT)\n"
                "/templates — менің шаблондар\n"
            )
            if BOT_TOKEN:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": help_text, "parse_mode": "HTML"}, timeout=8)
            return jsonify({"ok": True})

        if text.startswith("/addbot"):
            msg = "Bot қосу: BotFather арқылы бот жасаңыз да, жеке чатта төмендегі командамен токенді жіберіңіз:\n\n/token <BOT_TOKEN>"
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": msg}, timeout=8)
            return jsonify({"ok": True})

        # token handler (only in private)
        if text.startswith("/token "):
            if chat.get("type") != "private":
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Токенді тек жеке чатта жіберіңіз."}, timeout=8)
                return jsonify({"ok": True})
            token = text.split(" ", 1)[1].strip()
            if ":" not in token:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Токен форматы қате."}, timeout=8)
                return jsonify({"ok": True})
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": "Токен тексерілуде..."}, timeout=8)
            me = get_me(token)
            if not me:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Токен жарамсыз немесе Telegram қол жетімсіз."}, timeout=8)
                return jsonify({"ok": True})
            # save bot
            key = save_bot_record(owner=user_id, bot_id=me.get("id"), username=me.get("username"), token_plain=token)
            # set webhook for user bot to our /u/<owner>_<botid>
            webhook_url = None
            if WEBHOOK_BASE_URL:
                webhook_url = f"{WEBHOOK_BASE_URL}/u/{user_id}_{me.get('id')}"
                set_res = set_webhook_for_token(token, webhook_url)
                logger.info("Set webhook for user bot result: %s", set_res)
            # reply
            reply = f"✅ @{me.get('username')} қосылды!\nDB_KEY: {key}"
            if webhook_url:
                reply += f"\nWebhook: {webhook_url}"
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": reply}, timeout=8)
            return jsonify({"ok": True})

        # /bots - show user's bots
        if text.startswith("/bots"):
            all_bots = get_all_bots() or {}
            my = []
            for k, v in (all_bots.items() if isinstance(all_bots, dict) else []):
                if int(v.get("owner", 0)) == int(user_id):
                    my.append((k, v))
            if not my:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Сіздің қосқан ботыңыз жоқ."}, timeout=8)
                return jsonify({"ok": True})
            out = "Сіздің боттарыңыз:\n\n"
            for k, v in my:
                out += f"DB_KEY: <code>{k}</code>\n@{v.get('username','')}\n\n"
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": out, "parse_mode": "HTML"}, timeout=8)
            return jsonify({"ok": True})

        # /deletebot <key>
        if text.startswith("/deletebot"):
            parts = text.split()
            if len(parts) < 2:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Қолдану: /deletebot <DB_KEY>"}, timeout=8)
                return jsonify({"ok": True})
            key = parts[1].strip()
            rec = get_bot_by_key(key)
            if not rec:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Бот табылмады."}, timeout=8)
                return jsonify({"ok": True})
            if int(rec.get("owner")) != int(user_id) and not is_admin(user_id):
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Бұл бот сізге тиесілі емес."}, timeout=8)
                return jsonify({"ok": True})
            # try delete webhook
            try:
                token_enc = rec.get("token")
                token = decrypt_token(token_enc) if fernet else token_enc
                delete_webhook_for_token(token)
            except Exception:
                logger.exception("delete webhook attempt failed")
            delete_bot_by_key(key)
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": "✅ Бот жойылды."}, timeout=8)
            return jsonify({"ok": True})

        # /newpost - explanation
        if text.startswith("/newpost"):
            msg = "Хабар тарату үшін бір хабарда келесі форматты жіберіңіз:\n<DB_KEY>\n<мәтін>"
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": msg}, timeout=8)
            return jsonify({"ok": True})

        # Broadcast heuristic: message contains newline and first line looks like DB_KEY
        if "\n" in text:
            first, rest = text.split("\n", 1)
            first = first.strip()
            if len(first) >= 8:  # crude check for DB_KEY
                rec = get_bot_by_key(first)
                if rec:
                    if int(rec.get("owner")) != int(user_id) and not is_admin(user_id):
                        requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                                      json={"chat_id": chat_id, "text": "Сіз бұл боттың иесі емессіз."}, timeout=8)
                        return jsonify({"ok": True})
                    tok_enc = rec.get("token")
                    try:
                        tok = decrypt_token(tok_enc) if fernet else tok_enc
                    except Exception:
                        requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                                      json={"chat_id": chat_id, "text": "Токенді дешифрлеу сәтсіз."}, timeout=8)
                        return jsonify({"ok": True})
                    subs = get_subscribers(first)
                    sent = 0
                    for uid in subs:
                        try:
                            send_message_with_token(tok, int(uid), rest)
                            sent += 1
                        except Exception:
                            continue
                    requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                                  json={"chat_id": chat_id, "text": f"✅ {sent} адамға жіберілді."}, timeout=8)
                    return jsonify({"ok": True})

        # templates
        if text.startswith("/addtemplate"):
            # accept multiline message after command
            if "\n" in text:
                _, rest = text.split("\n", 1)
                if "\n" in rest:
                    title, content = rest.split("\n", 1)
                else:
                    title = rest.strip(); content = ""
                save_template(user_id, title.strip() or "Без названия", content.strip())
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "✅ Шаблон сақталды."}, timeout=8)
            else:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Қолдану: /addtemplate\\n<TITLE>\\n<CONTENT>"}, timeout=8)
            return jsonify({"ok": True})

        if text.startswith("/templates"):
            temps = get_templates(user_id)
            if not temps:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Шаблондар жоқ."}, timeout=8)
            else:
                out = ""
                for k, v in temps.items():
                    out += f"ID:{k}\n{v.get('title')}\n{v.get('content')}\n\n"
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": out[:4000]}, timeout=8)
            return jsonify({"ok": True})

        # admin add/remove
        if text.startswith("/addadmin"):
            if not is_admin(user_id):
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Сіз админ емессіз."}, timeout=8)
                return jsonify({"ok": True})
            parts = text.split()
            if len(parts) < 2:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Қолдану: /addadmin <user_id>"}, timeout=8)
                return jsonify({"ok": True})
            try:
                new_id = int(parts[1])
                add_admin(new_id)
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": f"✅ {new_id} админ етілді."}, timeout=8)
            except:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Қате user_id."}, timeout=8)
            return jsonify({"ok": True})

        if text.startswith("/removeadmin"):
            if not is_admin(user_id):
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Сіз админ емессіз."}, timeout=8)
                return jsonify({"ok": True})
            parts = text.split()
            if len(parts) < 2:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Қолдану: /removeadmin <user_id>"}, timeout=8)
                return jsonify({"ok": True})
            try:
                rem = int(parts[1])
                remove_admin(rem)
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": f"✅ {rem} админдер тізімінен алынды."}, timeout=8)
            except:
                requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                              json={"chat_id": chat_id, "text": "Қате user_id."}, timeout=8)
            return jsonify({"ok": True})

        # subscribers count
        if text.startswith("/subscribers"):
            total = count_total_subscribers()
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": f"Барлығы: {total}"}, timeout=8)
            return jsonify({"ok": True})

    except Exception as e:
        logger.exception("Main webhook handler exception: %s", e)
        try:
            requests.post(telegram_api_url(BOT_TOKEN, "sendMessage"),
                          json={"chat_id": chat_id, "text": "Серверде қате пайда болды. Админге хабарлаңыз."}, timeout=8)
        except Exception:
            pass
        return jsonify({"ok": False, "error": "exception"}), 500

    return jsonify({"ok": True, "info": "unhandled"})

# ----------------- User bot webhook endpoint -----------------
# For each user bot, webhook should be set to: {WEBHOOK_BASE_URL}/u/{owner}_{botid}
@app.route("/u/<owner_bot>", methods=["POST"])
def user_bot_webhook(owner_bot: str):
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid json"}), 400
    # owner_bot like "12345_987654321"
    if "_" not in owner_bot:
        return jsonify({"ok": False, "error": "bad path"}), 400
    owner_str, botid_str = owner_bot.split("_", 1)
    # find bot record
    all_bots = get_all_bots() or {}
    found_key = None; found_rec = None
    for k, v in (all_bots.items() if isinstance(all_bots, dict) else []):
        if str(v.get("owner")) == owner_str and str(v.get("bot_id")) == botid_str:
            found_key = k; found_rec = v; break
    if not found_rec:
        logger.warning("Webhook for unknown user-bot: %s", owner_bot)
        return jsonify({"ok": False, "error": "unknown bot"}), 404

    try:
        message = payload.get("message") or payload.get("edited_message") or {}
        if not message:
            return jsonify({"ok": True, "info": "no-message"})
        text = (message.get("text") or "").strip()
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        # register subscriber on /start
        if isinstance(text, str) and text.lower().startswith("/start"):
            add_subscriber(found_key, chat_id)
            # greet via user bot token
            token_enc = found_rec.get("token")
            try:
                token = decrypt_token(token_enc) if fernet else token_enc
            except Exception:
                token = token_enc
            try:
                send_message_with_token(token, chat_id, "Сіз осы ботқа жазылдыңыз! Қош келдіңіз ✅")
            except Exception:
                logger.exception("Greeting send failed")
            return jsonify({"ok": True})
        # Optionally: you can add auto-replies or collect commands from user-bot messages here
    except Exception:
        logger.exception("user_bot_webhook processing error: %s", traceback.format_exc())
        return jsonify({"ok": False, "error": "exception"}), 500

    return jsonify({"ok": True})

# ----------------- utility: set main webhook -----------------
def set_main_webhook():
    if not BOT_TOKEN or not WEBHOOK_BASE_URL:
        logger.info("set_main_webhook skipped (BOT_TOKEN or WEBHOOK_BASE_URL missing)")
        return
    url = f"{WEBHOOK_BASE_URL}/{BOT_TOKEN}"
    try:
        r = requests.post(telegram_api_url(BOT_TOKEN, "setWebhook"), json={"url": url}, timeout=8).json()
        logger.info("Set main webhook result: %s", r)
    except Exception:
        logger.exception("set_main_webhook failed")

# Run startup: try set webhook
try:
    if WEBHOOK_BASE_URL and BOT_TOKEN:
        logger.info("Attempting to set main webhook...")
        set_main_webhook()
except Exception:
    logger.exception("startup set_main_webhook error")
    info = requests.get(telegram_api_url(BOT_TOKEN, "getWebhookInfo")).json()
    logger.info(f"Webhook info: {json.dumps(info, ensure_ascii=False, indent=2)}")

# ------------- Run Flask -------------
if __name__ == "__main__":
    logger.info("ManyBot KZ starting (Flask). Port: %s", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=True)