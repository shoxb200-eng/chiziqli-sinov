import os
import json
import random
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

TOKEN = os.environ.get("BOT_TOKEN", "8603585449:AAGCZJFndbzUNTLXSHNyFiowXMUmrxKi6p0")

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

async def safe_stop_poll(chat_id, message_id):
    try:
        await bot.stop_poll(chat_id, message_id)
    except:
        pass

def get_blocks_keyboard(chat_id):
    builder = InlineKeyboardBuilder()
    total_questions = len(ALL_QUESTIONS)
    block_count = (total_questions + BLOCK_SIZE - 1) // BLOCK_SIZE
    for i in range(block_count):
        start_num = i * BLOCK_SIZE + 1
        end_num = min((i + 1) * BLOCK_SIZE, total_questions)
        builder.button(text=f"📦 Blok {i+1} ({start_num}-{end_num})", callback_data=f"block:{i}:{chat_id}")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("👋 Viktorina botiga xush kelibsiz!\n/quiz - Testni boshlash\n/stop - To'xtatish")

@dp.message(Command("quiz"))
async def choose_block_msg(message: types.Message):
    await message.answer("📚 Viktorina blokini tanlang:", reply_markup=get_blocks_keyboard(message.chat.id))

@dp.callback_query(F.data.startswith("block:"))
async def set_block_and_show_timer(callback: types.CallbackQuery):
    _, block_idx, chat_id = callback.data.split(":")
    block_idx, chat_id = int(block_idx), int(chat_id)
    
    # Blok ichida savollar va variantlarni aralashtirish
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
    for t in [15, 30, 60]: builder.button(text=f"{t} sek", callback_data=f"time:{t}:{chat_id}")
    await callback.message.edit_text("⏱ Vaqtni tanlang:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("time:"))
async def set_time_and_start(callback: types.CallbackQuery):
    _, seconds, chat_id = callback.data.split(":")
    games[int(chat_id)]["time_limit"] = int(seconds)
    await callback.message.delete()
    await send_next_question(int(chat_id))

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
    report = "🏁 **Natijalar:**\n" + "\n".join([f"{d['name']}: {d['correct']}" for d in game["results"].values()])
    await bot.send_message(chat_id, report, parse_mode="Markdown")

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
