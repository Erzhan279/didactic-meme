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
from aiohttp import web  # ✅ Web server үшін

# --- Gemini ---
try:
    from google import genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

# --- Config ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 10000))  # ✅ Render міндетті PORT

if not MAIN_BOT_TOKEN:
    raise SystemExit("❌ MAIN_BOT_TOKEN орнатылмаған.")

bot = Bot(token=MAIN_BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
DB_PATH = "manybot_kz.db"

# --- FSM States ---
class Form(StatesGroup):
    TEMPLATE_TITLE = State()
    TEMPLATE_CONTENT = State()
    GEMINI_ASK = State()

# --- DB Init ---
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
    conn.commit()
    conn.close()

init_db()

# --- DB Helpers ---
def add_template(user_id, title, content):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO templates (user_id, title, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, title, content, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

def list_templates(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT title, content FROM templates WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

# --- Gemini ---
def init_genai_client():
    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        return genai.Client(api_key=GEMINI_API_KEY)
    return None

genai_client = init_genai_client()

async def ask_gemini(prompt: str) -> str:
    if not genai_client:
        return "⚠️ Gemini клиенті табылмады."
    try:
        response = genai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return getattr(response, "text", str(response))
    except Exception as e:
        logger.error("Gemini қатесі: %s", e)
        return "⚠️ Gemini жауап бере алмады."

# --- Keyboard ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📄 Шаблондар"), KeyboardButton(text="➕ Шаблон қосу")],
        [KeyboardButton(text="🧠 Сұрақ қою (Gemini)")],
    ],
    resize_keyboard=True
)

# --- Bot Handlers ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Сәлем 👋 Бұл қазақша Manybot!", reply_markup=main_kb)

@dp.message(F.text == "📄 Шаблондар")
async def show_templates(message: types.Message):
    rows = list_templates(message.from_user.id)
    if not rows:
        await message.answer("Сізде шаблондар жоқ.")
        return
    text = "\n\n".join([f"📋 {t[0]}:\n{t[1]}" for t in rows])
    await message.answer(text)

@dp.message(F.text == "➕ Шаблон қосу")
async def add_template_start(message: types.Message, state: FSMContext):
    await message.answer("Шаблон атауын енгізіңіз:")
    await state.set_state(Form.TEMPLATE_TITLE)

@dp.message(Form.TEMPLATE_TITLE)
async def template_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Енді шаблон мазмұнын енгізіңіз:")
    await state.set_state(Form.TEMPLATE_CONTENT)

@dp.message(Form.TEMPLATE_CONTENT)
async def template_content(message: types.Message, state: FSMContext):
    data = await state.get_data()
    add_template(message.from_user.id, data["title"], message.text)
    await message.answer("✅ Шаблон сақталды.", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "🧠 Сұрақ қою (Gemini)")
async def gemini_start(message: types.Message, state: FSMContext):
    await message.answer("Сұрағыңызды жазыңыз:")
    await state.set_state(Form.GEMINI_ASK)

@dp.message(Form.GEMINI_ASK)
async def gemini_ask(message: types.Message, state: FSMContext):
    response = await ask_gemini(message.text)
    await message.answer(response, reply_markup=main_kb)
    await state.clear()

# --- Health Check Web Server ---
async def handle_root(request):
    return web.Response(text="✅ Bot is running on Render!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌍 Web server started on port {PORT}")

# --- MAIN ---
async def main():
    scheduler.start()
    # 🟢 Бір уақытта polling және web server қатар іске қосылады
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
