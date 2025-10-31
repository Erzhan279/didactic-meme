import os
import json
import logging
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, db
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not WEBHOOK_BASE_URL:
    raise SystemExit("⚠️ BOT_TOKEN және WEBHOOK_BASE_URL орнатылмаған.")

# ---------------- FIREBASE INIT ----------------
def initialize_firebase():
    """firebase_secret.json GitHub реподан оқиды"""
    try:
        if os.path.exists("firebase_secret.json"):
            with open("firebase_secret.json", "r") as f:
                creds = json.load(f)
        else:
            raise FileNotFoundError("firebase_secret.json табылмады")

        cred = credentials.Certificate(creds)
        firebase_admin.initialize_app(cred, {
            "databaseURL": "https://manybot-kz-default-rtdb.firebaseio.com/"
        })
        logger.info("✅ Firebase сәтті қосылды")
    except Exception as e:
        raise SystemExit(f"🚫 Firebase қатесі: {e}")

initialize_firebase()

# ---------------- BOT INIT ----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------------- FSM ----------------
class Form(StatesGroup):
    AWAIT_TOKEN = State()
    BROADCAST = State()

# ---------------- MAIN MENU ----------------
main_kb = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="➕ Бот қосу"), types.KeyboardButton(text="📢 Хабар тарату")],
        [types.KeyboardButton(text="👥 Жазылушылар"), types.KeyboardButton(text="ℹ️ Көмек")],
    ],
    resize_keyboard=True
)

# ---------------- HANDLERS ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Сәлем 👋 Бұл 🇰🇿 *Manybot KZ!*\n\n"
        "Бот жасау және хабар тарату үшін:\n"
        "/addbot — жаңа бот қосу\n"
        "/newpost — хабар тарату\n"
        "/subscribers — жазылушылар саны\n"
        "/help — көмек",
        reply_markup=main_kb,
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help(message: types.Message):
    await message.answer(
        "🧭 Командалар:\n"
        "/addbot — жаңа бот қосу\n"
        "/token <TOKEN> — токен жіберу\n"
        "/newpost — хабар тарату\n"
        "/subscribers — жазылушылар саны\n"
        "/deletebot <id> — ботты өшіру\n"
        "/bots — өз боттарыңды көру\n"
        "/help — көмек"
    )

# -------- ADD BOT --------
@dp.message(Command("addbot"))
async def addbot(message: types.Message, state: FSMContext):
    await message.answer("Жаңа бот қосу үшін /token <ТВОЙ_ТОКЕН> деп жаз.")
    await state.set_state(Form.AWAIT_TOKEN)

@dp.message(Form.AWAIT_TOKEN, F.text.startswith("/token "))
async def token_add(message: types.Message, state: FSMContext):
    token = message.text.split(" ", 1)[1].strip()
    if ":" not in token:
        await message.answer("⚠️ Токен дұрыс емес форматта.")
        return

    await message.answer("🔍 Токен тексерілуде...")

    try:
        tmp_bot = Bot(token=token)
        me = await tmp_bot.get_me()
        await tmp_bot.session.close()
    except Exception:
        await message.answer("❌ Токен жарамсыз.")
        return

    ref = db.reference("bots").push({
        "owner": message.from_user.id,
        "bot_id": me.id,
        "username": me.username,
        "token": token,
        "created_at": datetime.utcnow().isoformat()
    })

    webhook_url = f"{WEBHOOK_BASE_URL}/u/{message.from_user.id}_{me.id}"

    try:
        user_bot = Bot(token=token)
        await user_bot.set_webhook(webhook_url)
        await user_bot.session.close()
    except Exception as e:
        logger.error(f"Webhook қатесі: {e}")

    await message.answer(f"✅ @{me.username} қосылды!\n🌐 Webhook: {webhook_url}")
    await state.clear()

# -------- MY BOTS --------
@dp.message(Command("bots"))
async def list_bots(message: types.Message):
    all_bots = db.reference("bots").get() or {}
    my_bots = [b for b in all_bots.items() if b[1]["owner"] == message.from_user.id]
    if not my_bots:
        await message.answer("Сізде бот жоқ.")
        return
    text = "\n".join([f"@{b[1]['username']} — ID: {b[0]}" for b in my_bots])
    await message.answer(f"🧩 Сіздің боттарыңыз:\n\n{text}")

# -------- DELETE BOT --------
@dp.message(Command("deletebot"))
async def delete_bot(message: types.Message):
    args = message.get_args()
    if not args:
        await message.answer("Қолдану: /deletebot <id>")
        return
    bot_id = args.strip()
    ref = db.reference(f"bots/{bot_id}")
    data = ref.get()
    if not data:
        await message.answer("Бот табылмады.")
        return
    if data["owner"] != message.from_user.id:
        await message.answer("Бұл бот сізге тиесілі емес.")
        return
    ref.delete()
    await message.answer("✅ Бот өшірілді.")

# -------- NEW POST --------
@dp.message(Command("newpost"))
async def newpost(message: types.Message, state: FSMContext):
    all_bots = db.reference("bots").get() or {}
    my_bots = [b for b in all_bots.items() if b[1]["owner"] == message.from_user.id]
    if not my_bots:
        await message.answer("Сізде бот жоқ.")
        return
    text = "Қай боттан жібереміз?\n\n"
    for b in my_bots:
        text += f"{b[1]['username']} — ID: {b[0]}\n"
    await message.answer(text)
    await state.set_state(Form.BROADCAST)

@dp.message(Form.BROADCAST)
async def broadcast_msg(message: types.Message, state: FSMContext):
    lines = message.text.split("\n", 1)
    if len(lines) < 2:
        await message.answer("Қолдану: <bot_id>\n<мәтін>")
        return
    bot_id, text = lines[0].strip(), lines[1].strip()
    bot_data = db.reference(f"bots/{bot_id}").get()
    if not bot_data:
        await message.answer("Бот табылмады.")
        return
    if bot_data["owner"] != message.from_user.id:
        await message.answer("Бұл бот сізге тиесілі емес.")
        return
    user_bot = Bot(token=bot_data["token"])
    subs = db.reference(f"subscribers/{bot_id}").get() or {}
    sent = 0
    for uid in subs.keys():
        try:
            await user_bot.send_message(uid, text)
            sent += 1
        except:
            pass
    await user_bot.session.close()
    await message.answer(f"✅ {sent} адамға хабар жіберілді.")
    await state.clear()

# -------- SUBSCRIBERS --------
@dp.message(Command("subscribers"))
async def subscribers(message: types.Message):
    subs = db.reference("subscribers").get() or {}
    total = sum(len(v) for v in subs.values())
    await message.answer(f"Барлығы: {total} жазылушы.")

# -------- USER BOT WEBHOOK --------
async def user_bot_webhook(request):
    payload = await request.json()
    path = request.path.split("/u/")[-1]
    if "_" not in path:
        return web.Response(status=400)
    owner_id, botid = path.split("_")
    bots = db.reference("bots").get() or {}
    found = None
    for key, data in bots.items():
        if str(data["bot_id"]) == botid:
            found = (key, data)
            break
    if not found:
        return web.Response(status=404)
    bot_key, bot_data = found
    user_bot = Bot(token=bot_data["token"])
    try:
        msg = payload.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")
        if text and text.startswith("/start"):
            db.reference(f"subscribers/{bot_key}/{chat_id}").set(True)
            await user_bot.send_message(chat_id, "Сәлем! Сіз жазылдыңыз ✅")
    finally:
        await user_bot.session.close()
    return web.Response(status=200)

# -------- ROOT --------
async def root(request):
    return web.Response(text="✅ Manybot Firebase version is running on Render (Aiogram 3)")

# -------- APP --------
def create_app():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_post(f"/{BOT_TOKEN}", lambda req: dp.feed_webhook_update(bot, req))
    app.router.add_post("/u/{owner_bot}", user_bot_webhook)
    return app

# -------- RUN --------
if __name__ == "__main__":
    import asyncio
    async def main():
        app = create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        await bot.set_webhook(f"{WEBHOOK_BASE_URL}/{BOT_TOKEN}")
        logger.info(f"🌐 Webhook listening on port {PORT}")
        while True:
            await asyncio.sleep(3600)  # keep alive loop

    asyncio.run(main())
