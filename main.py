import os
import json
import html
import random
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

bot = Bot(token=TOKEN)
dp = Dispatcher()

games = {}
poll_to_chat = {}

# Savollarni yuklash
ALL_QUESTIONS = []
try:
    with open("questions.json", "r", encoding="utf-8") as file:
        ALL_QUESTIONS = json.load(file)
except FileNotFoundError:
    ALL_QUESTIONS = [{"question": "Test", "options": ["A", "B"], "correct": "A"}]

BLOCK_SIZE = 50
ALLOWED_TIME_LIMITS = (15, 30, 60)

async def safe_stop_poll(chat_id, message_id):
    try:
        await bot.stop_poll(chat_id, message_id)
    except Exception:
        pass

def get_blocks_keyboard():
    builder = InlineKeyboardBuilder()
    total_questions = len(ALL_QUESTIONS)
    block_count = (total_questions + BLOCK_SIZE - 1) // BLOCK_SIZE
    for i in range(block_count):
        start_num = i * BLOCK_SIZE + 1
        end_num = min((i + 1) * BLOCK_SIZE, total_questions)
        builder.button(text=f"📦 Blok {i+1} ({start_num}-{end_num})", callback_data=f"block:{i}")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("👋 Viktorina botiga xush kelibsiz!\n/quiz - Testni boshlash\n/stop - To'xtatish va natijalarni ko'rish")

@dp.message(Command("quiz"))
async def choose_block_msg(message: types.Message):
    await message.answer("📚 Viktorina blokini tanlang:", reply_markup=get_blocks_keyboard())

@dp.message(Command("stop"))
async def stop_quiz_cmd(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in games:
        return await message.answer("❌ Hozirda hech qanday faol test mavjud emas.")
    
    # Taymerni va poll-ni to'xtatamiz
    game = games[chat_id]
    if game.get("task"): game["task"].cancel()
    await safe_stop_poll(chat_id, game.get("current_msg_id"))
    
    await message.answer("🛑 Viktorina to'xtatildi. Natijalar hisoblanmoqda...")
    await finish_quiz(chat_id)

@dp.callback_query(F.data.startswith("block:"))
async def set_block_and_show_timer(callback: types.CallbackQuery):
    if callback.message is None:
        return await callback.answer("❌ Xatolik.", show_alert=True)
    chat_id = callback.message.chat.id
    try:
        block_idx = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
    block_count = (len(ALL_QUESTIONS) + BLOCK_SIZE - 1) // BLOCK_SIZE
    if not 0 <= block_idx < block_count:
        return await callback.answer("❌ Bunday blok mavjud emas.", show_alert=True)

    block_data = ALL_QUESTIONS[block_idx * BLOCK_SIZE : (block_idx + 1) * BLOCK_SIZE]
    shuffled_questions = []
    for q in block_data:
        opts = list(q["options"])
        random.shuffle(opts)
        shuffled_questions.append({"question": q["question"], "options": opts, "correct": q["correct"]})
    random.shuffle(shuffled_questions)

    games[chat_id] = {
        "questions": shuffled_questions, "current_index": 0, "time_limit": 30,
        "results": {}, "block_num": block_idx + 1
    }
    
    builder = InlineKeyboardBuilder()
    for t in ALLOWED_TIME_LIMITS: builder.button(text=f"{t} sek", callback_data=f"time:{t}")
    await callback.message.edit_text("⏱ Vaqtni tanlang:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("time:"))
async def set_time_and_start(callback: types.CallbackQuery):
    if callback.message is None:
        return await callback.answer("❌ Xatolik.", show_alert=True)
    chat_id = callback.message.chat.id
    try:
        seconds = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
    if seconds not in ALLOWED_TIME_LIMITS or chat_id not in games:
        return await callback.answer("❌ Bu test faol emas.", show_alert=True)
    games[chat_id]["time_limit"] = seconds
    await callback.message.delete()
    await send_next_question(chat_id)

async def send_next_question(chat_id):
    if chat_id not in games or games[chat_id]["current_index"] >= len(games[chat_id]["questions"]):
        return await finish_quiz(chat_id)
    
    game = games[chat_id]
    q = game["questions"][game["current_index"]]
    correct_idx = next((i for i, opt in enumerate(q["options"]) if str(opt).strip().lower() == str(q["correct"]).strip().lower()), 0)
    
    poll_msg = await bot.send_poll(chat_id, f"🎲 {game['block_num']}-Blok | {game['current_index']+1}-savol:\n{q['question']}"[:300],
                                    options=[o[:100] for o in q["options"]], type="quiz", correct_option_id=correct_idx, is_anonymous=False)
    
    game["current_msg_id"] = poll_msg.message_id
    poll_to_chat[poll_msg.poll.id] = chat_id
    game["task"] = asyncio.create_task(wait_for_timer(chat_id, game["time_limit"]))

async def wait_for_timer(chat_id, duration):
    await asyncio.sleep(duration)
    if chat_id in games:
        games[chat_id]["current_index"] += 1
        await send_next_question(chat_id)

@dp.poll_answer()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    chat_id = poll_to_chat.get(poll_answer.poll_id)
    if not chat_id or chat_id not in games: return
    if not poll_answer.option_ids: return
    game = games[chat_id]
    
    if game.get("task"): game["task"].cancel()
    await safe_stop_poll(chat_id, game["current_msg_id"])
    
    q = game["questions"][game["current_index"]]
    correct_idx = next((i for i, opt in enumerate(q["options"]) if str(opt).strip().lower() == str(q["correct"]).strip().lower()), 0)
    
    user_id = poll_answer.user.id
    if user_id not in game["results"]: game["results"][user_id] = {"name": poll_answer.user.full_name, "correct": 0}
    if poll_answer.option_ids[0] == correct_idx: game["results"][user_id]["correct"] += 1
    
    game["current_index"] += 1
    await send_next_question(chat_id)

async def finish_quiz(chat_id):
    if chat_id not in games: return
    game = games.pop(chat_id)
    results = game["results"]
    
    report = f"🏁 <b>{game['block_num']}-Blok natijalari:</b>\n"
    if not results:
        report += "Hech kim javob bermadi."
    else:
        # Natijalarni saralash (eng ko'p to'g'ri javob bergan birinchi)
        sorted_res = sorted(results.values(), key=lambda x: x["correct"], reverse=True)
        for i, d in enumerate(sorted_res, 1):
            report += f"{i}. 👤 {html.escape(str(d['name']))} ➔ <b>{d['correct']} ta</b> to'g'ri\n"

    await bot.send_message(chat_id, report, parse_mode="HTML")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot active"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web_server()
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "poll_answer"])

if __name__ == "__main__":
    asyncio.run(main())
