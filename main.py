import os
import logging
import sqlite3
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
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

# --- DB INIT ---
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

# --- States ---
class Form(StatesGroup):
    TEMPLATE_TITLE = State()
    TEMPLATE_CONTENT = State()
    SCHEDULE_TEXT = State()
    GEMINI_ASK = State()
    BROADCAST_TEXT = State()

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

# --- Gemini ---
def init_genai_client():
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY орнатылмаған.")
        return None
    if not GEMINI_AVAILABLE:
        logger.warning("google.genai кітапханасы табылмады.")
        return None
    return genai.Client(api_key=GEMINI_API_KEY)

genai_client = init_genai_client()

async def ask_gemini(prompt: str) -> str:
    if not genai_client:
        return "Кешіріңіз, Gemini клиенті жоқ."
    try:
        response = genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return getattr(response, "text", str(response))
    except Exception as e:
        logger.exception("Gemini қате: %s", e)
        return "Gemini-ден жауап алу кезінде қате болды."

# --- Keyboard ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📄 Шаблондар"), KeyboardButton(text="➕ Шаблон қосу")],
        [KeyboardButton(text="📆 Апта жоспары"), KeyboardButton(text="✉️ Хабар тарату (admin)")],
        [KeyboardButton(text="🧠 Сұрақ қою (Gemini)"), KeyboardButton(text="🛠️ Баптаулар")],
    ],
    resize_keyboard=True
)

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    ensure_admin(message.from_user.id)
    await message.answer(
        "Сәлем! 🇰🇿\nБұл — қазақша Manybot прототипі.",
        reply_markup=main_kb
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("/templates — шаблондар\n/addtemplate — шаблон қосу")

@dp.message(F.text == "📄 Шаблондар")
async def show_templates(message: types.Message):
    rows = list_templates_db(message.from_user.id)
    if not rows:
        await message.answer("Шаблондар жоқ.")
        return
    text = "📋 Шаблондар:\n\n" + "\n\n".join(f"{r[1]}:\n{r[2]}" for r in rows)
    await message.answer(text)

@dp.message(F.text == "➕ Шаблон қосу")
async def start_add_template(message: types.Message, state: FSMContext):
    await message.answer("Шаблон атауын енгізіңіз:")
    await state.set_state(Form.TEMPLATE_TITLE)

@dp.message(Form.TEMPLATE_TITLE)
async def input_template_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Шаблон мазмұнын енгізіңіз:")
    await state.set_state(Form.TEMPLATE_CONTENT)

@dp.message(Form.TEMPLATE_CONTENT)
async def input_template_content(message: types.Message, state: FSMContext):
    data = await state.get_data()
    add_template_db(message.from_user.id, data["title"], message.text)
    await message.answer("✅ Шаблон сақталды.", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "🧠 Сұрақ қою (Gemini)")
async def ask_gemini_menu(message: types.Message, state: FSMContext):
    await message.answer("Сұрағыңызды жазыңыз:")
    await state.set_state(Form.GEMINI_ASK)

@dp.message(Form.GEMINI_ASK)
async def handle_gemini_question(message: types.Message, state: FSMContext):
    answer = await ask_gemini(f"Қазақша жауап бер: {message.text}")
    await message.answer(answer, reply_markup=main_kb)
    await state.clear()

# --- Run ---
async def main():
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
