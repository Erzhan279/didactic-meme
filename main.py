# main.py
import os
import logging
import sqlite3
import asyncio
import base64
from datetime import datetime
from typing import Optional, Dict, Any

from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from cryptography.fernet import Fernet, InvalidToken

# Optional Gemini (AI)
try:
    from google import genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

# ----------------- Config -----------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")  # e.g. https://yourapp.onrender.com
PORT = int(os.environ.get("PORT", 10000))
MASTER_KEY = os.environ.get("MASTER_KEY")

if not MAIN_BOT_TOKEN:
    raise SystemExit("MAIN_BOT_TOKEN орнатылмаған.")
if not WEBHOOK_BASE_URL:
    raise SystemExit("WEBHOOK_BASE_URL орнатылмаған.")
if not MASTER_KEY:
    raise SystemExit("MASTER_KEY орнатылмаған. Fernet.generate_key() арқылы жасаңыз.")

# Fernet объектісі
try:
    fernet = Fernet(MASTER_KEY.encode())
except Exception as e:
    raise SystemExit("MASTER_KEY жарамсыз: Fernet кілтін дұрыс енгізіңіз.") from e

# Main bot (Manybot)
bot = Bot(token=MAIN_BOT_TOKEN)
dp = Dispatcher()

scheduler = AsyncIOScheduler()

# DB
DB_PATH = "manybot_kz.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # bots: bots that users added to the platform (encrypted token)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER,
        bot_id INTEGER,
        bot_username TEXT,
        encrypted_token TEXT,
        webhook_path TEXT,
        created_at TEXT
    )
    """)
    # subscribers: per bot subscribers (chat_id means user id who subscribed to that bot)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_db_id INTEGER,
        user_id INTEGER,
        joined_at TEXT
    )
    """)
    # templates
    cur.execute("""
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER,
        title TEXT,
        content TEXT,
        created_at TEXT
    )
    """)
    # bot_info (main Manybot info)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_info (
        id INTEGER PRIMARY KEY,
        description TEXT,
        lang TEXT DEFAULT 'kk'
    )
    """)
    # admins for Manybot
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE
    )
    """)
    conn.commit()
    conn.close()

init_db()

# --------------- Encryption helpers ---------------
def encrypt_token(token: str) -> str:
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(token_enc: str) -> str:
    try:
        return fernet.decrypt(token_enc.encode()).decode()
    except InvalidToken:
        logger.exception("Token decrypt failed (InvalidToken).")
        raise

# --------------- DB helpers ---------------
def save_bot(owner_user_id: int, bot_id: int, bot_username: str, token_plain: str) -> int:
    enc = encrypt_token(token_plain)
    webhook_path = f"/u/{owner_user_id}_{bot_id}"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO bots (owner_user_id, bot_id, bot_username, encrypted_token, webhook_path, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (owner_user_id, bot_id, bot_username, enc, webhook_path, datetime.utcnow().isoformat()))
    bot_db_id = cur.lastrowid
    conn.commit()
    conn.close()
    return bot_db_id

def get_bots_by_owner(owner_user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, bot_id, bot_username, webhook_path FROM bots WHERE owner_user_id=?", (owner_user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_bot(bot_db_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, owner_user_id, bot_id, bot_username, encrypted_token, webhook_path FROM bots WHERE id=?", (bot_db_id,))
    row = cur.fetchone()
    conn.close()
    return row

def delete_bot(bot_db_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM bots WHERE id=?", (bot_db_id,))
    cur.execute("DELETE FROM subscribers WHERE bot_db_id=?", (bot_db_id,))
    conn.commit()
    conn.close()

def add_subscriber_for_bot(bot_db_id: int, user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO subscribers (bot_db_id, user_id, joined_at) VALUES (?, ?, ?)",
                (bot_db_id, user_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def get_subscribers_for_bot(bot_db_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM subscribers WHERE bot_db_id=?", (bot_db_id,))
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

def count_subscribers_total():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM subscribers")
    val = cur.fetchone()[0]
    conn.close()
    return val

def set_bot_description(text: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO bot_info (id, description) VALUES (1, ?)", (text,))
    conn.commit()
    conn.close()

def get_bot_description() -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT description FROM bot_info WHERE id=1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "Сипаттама орнатылмаған."

def is_admin(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def add_admin(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def remove_admin(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# --------------- Gemini client (optional) ---------------
def init_genai_client():
    if GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY"):
        return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return None

genai_client = init_genai_client()

async def ask_gemini(prompt: str) -> str:
    if not genai_client:
        return "Gemini қолжетімсіз."
    try:
        response = genai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return getattr(response, "text", str(response))
    except Exception as e:
        logger.exception("Gemini error: %s", e)
        return "Gemini жауап бере алмады."

# --------------- FSM states ---------------
class Form(StatesGroup):
    AWAIT_TOKEN = State()
    CHOOSE_BOT_FOR_POST = State()
    BROADCAST_TEXT = State()
    DESCRIPTION = State()

# --------------- Keyboards ---------------
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📄 Шаблондар"), KeyboardButton(text="➕ Шаблон қосу")],
        [KeyboardButton(text="🧠 Сұрақ қою (Gemini)"), KeyboardButton(text="✉️ Жазылушыларға хабар (newpost)")],
    ],
    resize_keyboard=True
)

# --------------- Bot handlers (Manybot) ---------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    # Just greet and store user in subscribers table? We will only store subscribers to individual bots
    await message.answer("Сәлем! 🇰🇿 Manybot-қа қош келдіңіз.\n/help деп жазып функцияларды көре аласыз.", reply_markup=main_kb)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Командалар:\n"
        "/addbot — жаңа бот қосу\n"
        "/token <TOKEN> — BotFather-тен келген токенді жіберу (тек жеке чатта)\n"
        "/newpost — боттарды таңдап, жазылушыларға хабар тарату\n"
        "/setdescription — Manybot сипаттамасын орнату\n"
        "/subscribers — жалпы жазылушылар саны\n"
        "/bots — өзіңіз қосқан боттарды көру\n"
        "/deletebot <bot_db_id> — ботты өшіру\n"
        "/admins — админдерді басқару\n"
        "/help — осы көмек\n"
        "/cancel — ағымдағы әрекетті тоқтату"
    )

@dp.message(Command("addbot"))
async def cmd_addbot(message: types.Message):
    await message.answer(
        "Жаңа бот қосу үшін BotFather арқылы бот жасаңыз да, алған токенді жеке чатта мына команда арқылы жіберіңіз:\n\n"
        "`/token <BOT_TOKEN>`\n\n"
        "Ескерту: токенді қауіпсіз сақтаймыз (шифрланған).", parse_mode="Markdown"
    )
    await Form.AWAIT_TOKEN.set()

@dp.message(F.text.startswith("/token "))
async def cmd_token_raw(message: types.Message, state: FSMContext):
    # Accept only in private chat
    if message.chat.type != "private":
        await message.answer("Токенді тек жеке чатта жіберіңіз.")
        return
    token = message.text.split(" ", 1)[1].strip()
    if ":" not in token:
        await message.answer("Токен қате форматта. Қайта тексеріп жіберіңіз.")
        return

    await message.answer("Токенді тексеріп жатырмын...")

    # try to init temporary bot with that token
    try:
        temp_bot = Bot(token=token)
        me = await temp_bot.get_me()
        # close session of temp_bot to free resources
        await temp_bot.session.close()
    except Exception as e:
        logger.exception("Token validation error: %s", e)
        await message.answer("Токен жарамсыз немесе Telegram қол жетімсіз.")
        return

    # Save to DB encrypted
    bot_db_id = save_bot(message.from_user.id, me.id, me.username or "", token)
    # set webhook for that user bot to our server: WEBHOOK_BASE_URL + webhook_path
    webhook_path = f"{WEBHOOK_BASE_URL}/u/{message.from_user.id}_{me.id}"
    # set webhook using token
    try:
        user_bot = Bot(token=token)
        await user_bot.set_webhook(webhook_path)
        await user_bot.session.close()
    except Exception as e:
        logger.exception("Failed to set webhook for user bot: %s", e)
        # even if webhook setting failed, keep bot saved so user can use send methods
        await message.answer("Бот қосылды, бірақ webhook орнату мүмкін болмады (кейін қолмен тексеріңіз).")
        return

    await message.answer(f"✅ Бот қосылды: @{me.username} (id={bot_db_id})\nWebhook орнатылды.")

@dp.message(Command("bots"))
async def cmd_list_bots(message: types.Message):
    rows = get_bots_by_owner(message.from_user.id)
    if not rows:
        await message.answer("Сізде қосылған боттар жоқ. /addbot арқылы қосыңыз.")
        return
    text = "Сіздің боттарыңыз:\n\n"
    for r in rows:
        text += f"DB_ID:{r[0]} — @{r[2]}  (webhook: {r[3]})\n"
    await message.answer(text)

@dp.message(Command("deletebot"))
async def cmd_deletebot(message: types.Message):
    # /deletebot <bot_db_id>
    args = message.get_args()
    if not args:
        await message.answer("Қолдану: /deletebot <bot_db_id>\nАлдымен /bots арқылы ID табыңыз.")
        return
    try:
        bot_db_id = int(args.strip())
    except ValueError:
        await message.answer("ID сан болуы тиіс.")
        return
    row = get_bot(bot_db_id)
    if not row:
        await message.answer("Мұндай бот табылған жоқ.")
        return
    if row[1] != message.from_user.id and not is_admin(message.from_user.id):
        await message.answer("Сіз бұл боттың иесі емессіз.")
        return
    # try to remove webhook from that bot (best-effort)
    enc = row[4]
    try:
        token = decrypt_token(enc)
        tbot = Bot(token=token)
        await tbot.delete_webhook(drop_pending_updates=True)
        await tbot.session.close()
    except Exception:
        logger.exception("Failed to delete webhook for user bot (best-effort).")

    delete_bot(bot_db_id)
    await message.answer("✅ Бот және онымен байланысты жазбалар жойылды.")

@dp.message(Command("setdescription"))
async def cmd_setdescription(message: types.Message):
    await message.answer("Бет сипаттамасын енгізіңіз:")
    await Form.DESCRIPTION.set()

@dp.message(Form.DESCRIPTION)
async def save_description_handler(message: types.Message, state: FSMContext):
    set_bot_description(message.text)
    await message.answer("✅ Сипаттама орнатылды.")
    await state.clear()

@dp.message(Command("subscribers"))
async def cmd_subscribers(message: types.Message):
    cnt = count_subscribers_total()
    await message.answer(f"Барлығы {cnt} жазылушы бар (барлық боттар бойынша).")

# Broadcast: choose bot then message
@dp.message(Command("newpost"))
async def cmd_newpost_start(message: types.Message):
    rows = get_bots_by_owner(message.from_user.id)
    if not rows:
        await message.answer("Сізде боттар жоқ. /addbot арқылы қосыңыз.")
        return
    if len(rows) == 1:
        # single bot: go ahead
        await message.answer("Мәтінді жазыңыз, ол боттың барлық жазылушыларына таратылады.")
        # save chosen bot id in FSM
        await dp.current_state(user=message.from_user.id).update_data(chosen_bot=rows[0][0])
        await Form.BROADCAST_TEXT.set()
        return
    # multiple — present options
    text = "Қай боттан таратасыз? DB_ID санын жазыңыз:\n\n"
    for r in rows:
        text += f"DB_ID:{r[0]} — @{r[2]}\n"
    await message.answer(text)
    await dp.current_state(user=message.from_user.id).set_state(Form.BROADCAST_TEXT)  # reuse state; expect '<id>\n<message>' pattern or two-step

@dp.message(Form.BROADCAST_TEXT)
async def cmd_newpost_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if "chosen_bot" not in data:
        # expecting first message to be bot id or "id: message"
        text = message.text.strip()
        parts = text.split("\n", 1)
        first = parts[0].strip()
        # if first is number -> select bot and ask for message
        if first.isdigit() and len(parts) == 1:
            bot_db_id = int(first)
            row = get_bot(bot_db_id)
            if not row or row[1] != message.from_user.id and not is_admin(message.from_user.id):
                await message.answer("Қате ID немесе сізге тиесілі емес бот.")
                await state.clear()
                return
            await state.update_data(chosen_bot=bot_db_id)
            await message.answer("Енді тарату мәтінін жіберіңіз:")
            return
        # if user sent 'id\nmessage' in one go
        if first.isdigit() and len(parts) == 2:
            bot_db_id = int(first)
            # proceed
            msg_text = parts[1].strip()
            row = get_bot(bot_db_id)
            if not row or row[1] != message.from_user.id and not is_admin(message.from_user.id):
                await message.answer("Қате ID немесе сізге тиесілі емес бот.")
                await state.clear()
                return
            # send broadcasts
            await _broadcast_to_bot(bot_db_id, msg_text, message)
            await state.clear()
            return
        # else: ambiguous
        await message.answer("Ботты таңдамайсыз ба? Алдымен /bots арқылы ID қарап, бірінші жолға ID жазыңыз, екінші жолда мәтін.")
        await state.clear()
        return
    else:
        bot_db_id = data["chosen_bot"]
        text_to_send = message.text
        await _broadcast_to_bot(bot_db_id, text_to_send, message)
        await state.clear()

async def _broadcast_to_bot(bot_db_id: int, text: str, reply_message: types.Message):
    subs = get_subscribers_for_bot(bot_db_id)
    if not subs:
        await reply_message.answer("Бұл ботта жазылушылар жоқ.")
        return
    row = get_bot(bot_db_id)
    if not row:
        await reply_message.answer("Бот табылмады.")
        return
    enc = row[4]
    try:
        token = decrypt_token(enc)
    except Exception:
        await reply_message.answer("Токенді дешифрлеу қатесі.")
        return
    user_bot = Bot(token=token)
    sent = 0
    for chat_id in subs:
        try:
            await user_bot.send_message(chat_id, text)
            sent += 1
        except Exception:
            continue
    await user_bot.session.close()
    await reply_message.answer(f"✅ {sent} адамға хабар жіберілді.")

# Add admin management
@dp.message(Command("admins"))
async def cmd_admins(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Сіз Manybot админі емессіз.")
        return
    await message.answer(
        "Admins басқару:\n/addadmin <user_id>\n/removeadmin <user_id>\n/listadmins"
    )

@dp.message(Command("addadmin"))
async def cmd_addadmin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Сіз Manybot админі емессіз.")
        return
    args = message.get_args()
    if not args or not args.isdigit():
        await message.answer("Пайдалану: /addadmin <user_id>")
        return
    add_admin(int(args))
    await message.answer("Админ қосылды.")

@dp.message(Command("removeadmin"))
async def cmd_removeadmin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Сіз Manybot админі емессіз.")
        return
    args = message.get_args()
    if not args or not args.isdigit():
        await message.answer("Пайдалану: /removeadmin <user_id>")
        return
    remove_admin(int(args))
    await message.answer("Админ жойылды (бар болса).")

@dp.message(Command("listadmins"))
async def cmd_listadmins(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Сіз Manybot админі емессіз.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await message.answer("Админдер тізімі бос.")
        return
    await message.answer("Админдер:\n" + "\n".join(str(r[0]) for r in rows))

@dp.message(Command("templates"))
async def cmd_templates(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, content FROM templates WHERE owner_user_id=? ORDER BY id DESC", (message.from_user.id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await message.answer("Шаблондар жоқ.")
        return
    text = "\n\n".join([f"ID:{r[0]} — {r[1]}\n{r[2]}" for r in rows])
    await message.answer(text)

@dp.message(Command("addtemplate"))
async def cmd_addtemplate_start(message: types.Message, state: FSMContext):
    await message.answer("Шаблон атауын енгізіңіз:")
    await state.set_state(Form.AWAIT_TOKEN)  # reuse a simple state for temp
    await dp.current_state(user=message.from_user.id).update_data(template_step="title")

@dp.message(lambda message: True)
async def catch_all(message: types.Message):
    # A simple fallback to help users - keep succinct
    # If not in any FSM, ignore or guide
    state = dp.current_state(user=message.from_user.id)
    st = await state.get_state()
    if st is None:
        # not in FSM
        # minimal autoprompt
        txt = message.text.strip().lower()
        if txt.startswith("/"):
            # unknown command
            await message.answer("Түсінілмейтін команда. /help деп көріңіз.")
            return
        # otherwise ignore silently or reply:
        return

# --------------- Web server / webhook for user bots ---------------
# This endpoint receives updates from user bots (we set webhook to WEBHOOK_BASE_URL + webhook_path)
async def user_bot_webhook(request):
    # path like /u/{owner}_{botid}
    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400)
    # get webhook path to map to bot row
    path = request.path
    # find bot by webhook_path
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, encrypted_token FROM bots WHERE webhook_path=?", (path,))
    row = cur.fetchone()
    conn.close()
    if not row:
        logger.warning("Webhook for unknown path: %s", path)
        return web.Response(status=404)
    bot_db_id, enc = row
    try:
        token = decrypt_token(enc)
    except Exception:
        logger.exception("Decrypt token failed for webhook.")
        return web.Response(status=500)
    # create Bot instance and handle update
    user_bot = Bot(token=token)
    try:
        # feed update to aiogram Dispatcher? Simpler: handle basic subscribe command for user bot
        # we parse simple messages: if user sends '/start' to user bot -> add subscriber
        update = payload
        # if message present and text == /start => add subscriber
        if "message" in update:
            msg = update["message"]
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if isinstance(text, str) and text.strip().lower().startswith("/start"):
                add_subscriber_for_bot(bot_db_id, chat_id)
                # send welcome via user_bot
                await user_bot.send_message(chat_id, "Сіз сол ботқа жазылдыңыз. Қош келдіңіз!")
    except Exception:
        logger.exception("Error handling webhook update.")
    finally:
        await user_bot.session.close()
    return web.Response(status=200)

# Health and root
async def handle_root(request):
    return web.Response(text="✅ Manybot running")

# --------------- App startup/shutdown ---------------
async def on_startup(app):
    logger.info("App startup: main webhook not needed for Manybot (we use web endpoints).")

async def on_shutdown(app):
    logger.info("Shutting down: closing main bot session.")
    await bot.session.close()

# --------------- App runner ---------------
def create_app():
    app = web.Application()
    app.router.add_get("/", handle_root)
    # endpoint for user bot webhooks: /u/{owner}_{botid}
    app.router.add_post(r"/u/{owner_bot}", user_bot_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

# --------------- Run ---------------
if __name__ == "__main__":
    app = create_app()
    # Note: we still need to set webhook for MAIN_BOT if we want; currently Manybot uses polling commands with aiogram Dispatcher
    # But since we're running an aiohttp app, we should run both the aiohttp server and aiogram polling concurrently.
    async def _main():
        # start web server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info(f"Web server started on port {PORT}")
        # start aiogram polling for Manybot admin interactions
        # run polling in background
        await dp.start_polling(bot)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Exiting...")
