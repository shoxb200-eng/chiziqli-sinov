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

# Bot xotirasi
games = {}
poll_to_chat = {}

# JSON fayldan barcha savollarni yuklash
ALL_QUESTIONS = []
try:
    with open("questions.json", "r", encoding="utf-8") as file:
        ALL_QUESTIONS = json.load(file)
    print(f"Muvaffaqiyatli yuklandi: {len(ALL_QUESTIONS)} ta savol.")
except FileNotFoundError:
    ALL_QUESTIONS = [{"question": f"Test savoli {i}", "options": ["Variant A", "Variant B"], "correct": "Variant A"} for i in range(1, 13)]
    print("questions.json topilmadi, vaqtincha test ma'lumotlari yaratildi.")

# Har bir blokda nechta savol bo'lishi
BLOCK_SIZE = 50 

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
    await message.answer(
        "👋 Salom! Men Blokli va Aqlli Viktorina botman.\n\n"
        "• Testni boshlash: /quiz\n"
        "• Testni vaqtidan oldin to'xtatish: /stop\n\n"
        "Savollar 50 tadan bo'lingan va har safar tasodifiy (random) tartibda beriladi!"
    )

@dp.message(Command("quiz"))
async def choose_block_msg(message: types.Message):
    chat_id = message.chat.id
    if chat_id in games:
        return await message.answer("⚠️ Bu chatda hozirda faol viktorina ketmoqda. Uni to'xtatish uchun /stop buyrug'ini bering.")
    if not ALL_QUESTIONS:
        return await message.answer("Xatolik: Savollar bazasi bo'sh!")
    await message.answer("📚 Viktorina blokini tanlang:", reply_markup=get_blocks_keyboard(chat_id))

@dp.message(Command("stop"))
async def stop_quiz_cmd(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in games:
        return await message.answer("❌ Hozirda hech qanday faol test mavjud emas.")
        
    if message.chat.type in ["group", "supergroup"]:
        member = await message.chat.get_member(message.from_user.id)
        if member.status not in ["creator", "administrator"]:
            return await message.answer("⚠️ Guruhdagi testni faqat adminlar to'xtata oladi!")

    await message.answer("🛑 Viktorina foydalanuvchi buyrug'iga binoan muddatidan oldin to'xtatildi.")
    await finish_quiz(chat_id, auto_paused=False)

@dp.callback_query(F.data.startswith("block:"))
async def set_block_and_show_timer(callback: types.CallbackQuery):
    _, block_idx, chat_id = callback.data.split(":")
    block_idx = int(block_idx)
    chat_id = int(chat_id)
    
    if chat_id in games:
        return await callback.answer("Bu chatda o'yin boshlanib ketgan!", show_alert=True)
        
    start_idx = block_idx * BLOCK_SIZE
    end_idx = start_idx + BLOCK_SIZE
    block_questions = ALL_QUESTIONS[start_idx:end_idx]
    
    shuffled_questions = random.sample(block_questions, len(block_questions))
    is_group = callback.message.chat.type in ["group", "supergroup"]
    
    games[chat_id] = {
        "questions": shuffled_questions,
        "current_index": 0,
        "time_limit": 30,
        "results": {},
        "is_group": is_group,
        "current_poll_id": None,
        "current_msg_id": None,
        "task": None,
        "block_num": block_idx + 1,
        "unanswered_counter": 0,
        "current_poll_answered": False
    }
    
    builder = InlineKeyboardBuilder()
    builder.button(text="15 Sekund", callback_data=f"time:15:{chat_id}")
    builder.button(text="30 Sekund", callback_data=f"time:30:{chat_id}")
    builder.button(text="1 Daqiqa", callback_data=f"time:60:{chat_id}")
    builder.adjust(3)
    
    await callback.message.edit_text(f"✅ {block_idx+1}-Blok tanlandi ({len(block_questions)} ta savol aralashtirildi).\n⏱ Taymer vaqtini tanlang:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("time:"))
async def set_time_and_start(callback: types.CallbackQuery):
    _, seconds, chat_id = callback.data.split(":")
    chat_id = int(chat_id)
    seconds = int(seconds)
    
    if chat_id not in games:
        return await callback.answer("Sessiya eskirgan. Qaytadan /quiz bering.", show_alert=True)
        
    games[chat_id]["time_limit"] = seconds
    await callback.message.delete()
    await send_next_question(chat_id)

async def send_next_question(chat_id):
    if chat_id not in games:
        return
        
    game = games[chat_id]
    idx = game["current_index"]
    questions = game["questions"]
    
    if idx >= len(questions):
        await finish_quiz(chat_id)
        return
        
    q = questions[idx]
    game["current_poll_answered"] = False
    
    correct_index = 0
    correct_text = str(q.get("correct", "")).strip().lower()
    for i, opt in enumerate(q["options"]):
        if str(opt).strip().lower() == correct_text:
            correct_index = i
            break

    cleaned_options = []
    for opt in q["options"]:
        opt_str = str(opt).strip()
        if len(opt_str) > 100:
            cleaned_options.append(opt_str[:97] + "...")
        else:
            cleaned_options.append(opt_str)

    try:
        poll_msg = await bot.send_poll(
            chat_id=chat_id,
            question=f"🎲 Blok {game['block_num']} | Savol {idx+1}/{len(questions)}:\n{q['question']}"[:300],
            options=cleaned_options,
            type="quiz",
            correct_option_id=correct_index,
            is_anonymous=False,
            explanation="To'g'ri javob belgilandi!"
        )
        
        game["current_poll_id"] = poll_msg.poll.id
        game["current_msg_id"] = poll_msg.message_id
        poll_to_chat[poll_msg.poll.id] = chat_id
        
        game["task"] = asyncio.create_task(wait_for_timer(chat_id, game["time_limit"]))
    except Exception as e:
        print(f"Poll yuborishda xatolik (Savol {idx+1}): {e}")
        game["current_index"] += 1
        await send_next_question(chat_id)

async def wait_for_timer(chat_id, duration):
    await asyncio.sleep(duration)
    if chat_id in games:
        game = games[chat_id]
        try:
            await bot.stop_poll(chat_id, game["current_msg_id"])
        except:
            pass
        
        if not game["current_poll_answered"]:
            game["unanswered_counter"] += 1
        else:
            game["unanswered_counter"] = 0

        if game["unanswered_counter"] >= 3:
            await bot.send_message(chat_id, "💤 Ketma-ket 3 ta savolga hech kim javob bermadi. Viktorina faollik yo'qligi sababli to'xtatildi (pauza).")
            await finish_quiz(chat_id, auto_paused=True)
            return
        
        await asyncio.sleep(1.5)
        game["current_index"] += 1
        await send_next_question(chat_id)

@dp.poll_answer()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    poll_id = poll_answer.poll_id
    if poll_id not in poll_to_chat:
        return
        
    chat_id = poll_to_chat[poll_id]
    if chat_id not in games:
        return
        
    game = games[chat_id]
    game["current_poll_answered"] = True
    
    user_id = poll_answer.user.id
    user_name = poll_answer.user.full_name
    
    if user_id not in game["results"]:
        game["results"][user_id] = {"name": user_name, "correct": 0, "total": 0}
        
    game["results"][user_id]["total"] += 1
    
    idx = game["current_index"]
    q = game["questions"][idx]
    
    correct_index = 0
    correct_text = str(q.get("correct", "")).strip().lower()
    for i, opt in enumerate(q["options"]):
        if str(opt).strip().lower() == correct_text:
            correct_index = i
            break
    
    if poll_answer.option_ids[0] == correct_index:
        game["results"][user_id]["correct"] += 1

    if not game["is_group"]:
        if game["task"]:
            game["task"].cancel()
        try:
            await bot.stop_poll(chat_id, game["current_msg_id"])
        except:
            pass
            
        game["current_index"] += 1
        await asyncio.sleep(1)
        await send_next_question(chat_id)

async def finish_quiz(chat_id, auto_paused=False):
    if chat_id not in games:
        return
        
    game = games[chat_id]
    results = game["results"]
    
    if game["task"]:
        game["task"].cancel()
        
    try:
        await bot.stop_poll(chat_id, game["current_msg_id"])
    except:
        pass
        
    status_text = "pauza holatidagi" if auto_paused else "yakuniy"
    report = f"🏁 **{game['block_num']}-Blok bo'yicha {status_text} natijalar:**\n\n"
    
    if not results:
        report += "Hech kim qatnashmadi yoki savollarga to'g'ri javob berilmadi."
    else:
        sorted_results = sorted(results.items(), key=lambda x: x[1]["correct"], reverse=True)
        for i, (u_id, data) in enumerate(sorted_results, 1):
            report += f"{i}. 👤 {data['name']} ➔ **{data['correct']} ta** to'g'ri ({data['total']} tadan)\n"
            
    await bot.send_message(chat_id, report, parse_mode="Markdown")
    
    if game["current_poll_id"] in poll_to_chat:
        del poll_to_chat[game["current_poll_id"]]
    del games[chat_id]

# --- RENDER UCHUN VEB-SERVER QISMI ---
async def web_handle(request):
    return web.Response(text="Quiz Bot is active and awake!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', web_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # [TUZATISH] Render muhitidagi asosiy portni olish (agar bo'lmasa 10000)
    port = int(os.environ.get("PORT", 10000))
    
    # Barcha IP lardan kelayotgan so'rovlarni eshitish uchun 0.0.0.0 qo'yamiz
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Veb server {port}-portda muvaffaqiyatli ishga tushdi.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
