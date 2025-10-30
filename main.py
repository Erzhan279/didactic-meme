import os
import logging
import sqlite3
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

try:
    from google import genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not MAIN_BOT_TOKEN:
    logger.error("MAIN_BOT_TOKEN орнатылмаған. Render/Env айнымалысын орнатыңыз.")
    raise SystemExit("MAIN_BOT_TOKEN жоқ")

bot = Bot(token=MAIN_BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "manybot_kz.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        content TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        content TEXT,
        cron TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE
    )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Helper functions ---
def add_template_db(user_id: int, title: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO templates (user_id, title, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, title, content, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

def list_templates_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, content FROM templates WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def add_schedule_db(user_id:int, title:str, content:str, cron_expr:str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO schedules (user_id, title, content, cron, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, title, content, cron_expr, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

def list_schedules_db(user_id:int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, content, cron FROM schedules WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def is_admin(user_id:int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def ensure_admin(user_id:int):
    ADMIN_ID = os.getenv("ADMIN_ID")
    if ADMIN_ID and int(ADMIN_ID) == user_id:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()

# --- Scheduler ---
scheduler = AsyncIOScheduler()

async def send_scheduled_message(chat_id: int, text: str):
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logger.error("Жіберу қатесі: %s", e)

def schedule_job(job_id: str, cron_expr: str, chat_id: int, text: str):
    parts = cron_expr.split(":")
    if parts[0] == "daily":
        hour, minute = map(int, parts[1].split(":"))
        scheduler.add_job(lambda: asyncio.create_task(send_scheduled_message(chat_id, text)),
                          'cron', hour=hour, minute=minute, id=job_id, replace_existing=True)
    elif parts[0] == "weekly":
        _, weekday, time_part = parts
        hour, minute = map(int, time_part.split(":"))
        scheduler.add_job(lambda: asyncio.create_task(send_scheduled_message(chat_id, text)),
                          'cron', day_of_week=weekday.lower(), hour=hour, minute=minute,
                          id=job_id, replace_existing=True)
    else:
        logger.warning("Белгісіз cron түрі: %s", cron_expr)

# --- Gemini helper ---
def init_genai_client():
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY орнатылмаған. /ask командасы жұмыс істемейді.")
        return None
    if not GEMINI_AVAILABLE:
        logger.warning("google.genai кітапханасы табылмады. Егер жоқ болса REST арқылы шақыр.")
        return None
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client

genai_client = init_genai_client()

async def ask_gemini(prompt: str) -> str:
    if not genai_client:
        return "Кешіріңіз, Gemini клиенті конфигурацияланбаған."
    try:
        response = genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = getattr(response, "text", None) or str(response)
        return text
    except Exception as e:
        logger.exception("Gemini қате: %s", e)
        return "Gemini-ден жауап алу кезінде қате пайда болды."

# --- Keyboard ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📄 Шаблондар"), KeyboardButton("➕ Шаблон қосу")],
        [KeyboardButton("📆 Апта жоспары"), KeyboardButton("✉️ Хабар тарату (admin)")],
        [KeyboardButton("🧠 Сұрақ қою (Gemini)"), KeyboardButton("🛠️ Баптаулар")],
    ],
    resize_keyboard=True
)

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    ensure_admin(message.from_user.id)
    await message.answer(
        "Сәлем! 🇰🇿\nБұл — қазақша Manybot прототипі.\n\nМәзірден таңдаңыз немесе /help деп жазыңыз.",
        reply_markup=main_kb
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Қол жетімді командалар:\n"
        "/templates — шаблондарды қарау\n"
        "/addtemplate — шаблон қосу\n"
        "/schedules — жоспарларды көру\n"
        "/addschedule — апта жоспарын қосу\n"
        "/ask — GPT (Gemini) көмегі\n"
    )

@dp.message(F.text == "📄 Шаблондар")
async def show_templates(message: types.Message):
    rows = list_templates_db(message.from_user.id)
    if not rows:
        await message.answer("Сізде шаблондар жоқ. '➕ Шаблон қосу' арқылы қосыңыз.")
        return
    text = "Сіздің шаблондарыңыз:\n\n"
    for r in rows:
        text += f"ID:{r[0]} — {r[1]}\n{r[2]}\n\n"
    await message.answer(text)

@dp.message(F.text == "➕ Шаблон қосу")
async def start_add_template(message: types.Message):
    await message.answer("Шаблон атауын енгізіңіз (мысалы: 'Жаңа өнім')\nТоқтатқыңыз келсе /cancel деп жазыңыз.")
    await dp.current_state(user=message.from_user.id).set_state("TEMPLATE_TITLE")

@dp.message(state="TEMPLATE_TITLE")
async def input_template_title(message: types.Message):
    if message.text == "/cancel":
        await message.answer("Әрекет тоқтатылды.", reply_markup=main_kb)
        await dp.current_state(user=message.from_user.id).clear()
        return
    await dp.current_state(user=message.from_user.id).update_data(title=message.text)
    await message.answer("Енді шаблон мәтінін енгізіңіз:")
    await dp.current_state(user=message.from_user.id).set_state("TEMPLATE_CONTENT")

@dp.message(state="TEMPLATE_CONTENT")
async def input_template_content(message: types.Message):
    data = await dp.current_state(user=message.from_user.id).get_data()
    title = data.get("title", "Без названия")
    content = message.text
    add_template_db(message.from_user.id, title, content)
    await message.answer("✅ Шаблон сақталды.", reply_markup=main_kb)
    await dp.current_state(user=message.from_user.id).clear()

@dp.message(F.text == "📆 Апта жоспары")
async def cmd_week_plan(message: types.Message):
    await message.answer(
        "Апталық жоспар қосу: /addschedule\n"
        "Бар жоспарларды көру: /schedules\n\n"
        "Мысалы:\n/addschedule weekly:mon:09:00\n"
        "Содан кейін мәтінді жібересіз."
    )

@dp.message(Command("addschedule"))
async def cmd_add_schedule(message: types.Message):
    args = message.get_args()
    if not args:
        await message.answer("Қолдану: /addschedule weekly:mon:09:00 немесе /addschedule daily:09:00")
        return
    await dp.current_state(user=message.from_user.id).update_data(cron=args)
    await dp.current_state(user=message.from_user.id).set_state("SCHEDULE_TEXT")
    await message.answer("Жоспар үшін жіберілетін мәтінді енгізіңіз:")

@dp.message(state="SCHEDULE_TEXT")
async def save_schedule_text(message: types.Message):
    data = await dp.current_state(user=message.from_user.id).get_data()
    cron = data.get("cron")
    text = message.text
    add_schedule_db(message.from_user.id, f"Жоспар {cron}", text, cron)
    job_id = f"{message.from_user.id}_{int(datetime.utcnow().timestamp())}"
    schedule_job(job_id, cron, message.chat.id, text)
    await message.answer("✅ Апталық жоспар сақталды және жоспарланды.", reply_markup=main_kb)
    await dp.current_state(user=message.from_user.id).clear()

@dp.message(Command("schedules"))
async def cmd_list_schedules(message: types.Message):
    rows = list_schedules_db(message.from_user.id)
    if not rows:
        await message.answer("Жоспарлар жоқ.")
        return
    text = "Сіздің жоспарларыңыз:\n\n"
    for r in rows:
        text += f"ID:{r[0]} — {r[1]}\n{r[2]}\nCron: {r[3]}\n\n"
    await message.answer(text)

@dp.message(F.text == "🧠 Сұрақ қою (Gemini)")
async def ask_menu(message: types.Message):
    await message.answer("Сұрағыңызды жазыңыз (немесе /cancel):")
    await dp.current_state(user=message.from_user.id).set_state("GEMINI_ASK")

@dp.message(state="GEMINI_ASK")
async def handle_gemini_ask(message: types.Message):
    if message.text == "/cancel":
        await message.answer("Әрекет тоқтатылды.", reply_markup=main_kb)
        await dp.current_state(user=message.from_user.id).clear()
        return
    prompt = f"Пайдаланушы сұрағы: {message.text}\nЖауапты қысқаша әрі қазақша бер."
    answer = await ask_gemini(prompt)
    await message.answer(f"🧠 Gemini жауап:\n\n{answer}", reply_markup=main_kb)
    await dp.current_state(user=message.from_user.id).clear()

@dp.message(F.text == "✉️ Хабар тарату (admin)")
async def admin_broadcast_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Сіз админ емессіз.")
        return
    await message.answer("Жіберілетін мәтінді енгізіңіз:")
    await dp.current_state(user=message.from_user.id).set_state("BROADCAST_TEXT")

@dp.message(state="BROADCAST_TEXT")
async def handle_broadcast(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Сіз админ емессіз.")
        await dp.current_state(user=message.from_user.id).clear()
        return
    text = message.text
    await bot.send_message(message.from_user.id, f"Сіз жібердіңіз:\n\n{text}")
    await message.answer("✅ Хабар жіберілді.", reply_markup=main_kb)
    await dp.current_state(user=message.from_user.id).clear()

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    await dp.current_state(user=message.from_user.id).clear()
    await message.answer("Әрекет тоқтатылды.", reply_markup=main_kb)

def load_schedules_from_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, title, content, cron FROM schedules")
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        job_id = f"db_{r[0]}"
        schedule_job(job_id, r[4], r[1], r[3])

async def main():
    load_schedules_from_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот тоқтатылды.")
