import os
import json
import random
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession

TOKEN = "8603585449:AAGCZJFndbzUNTLXSHNyFiowXMUmrxKi6p0"

# Limitlardan qochish uchun session sozlamalari
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

games = {}
poll_to_chat = {}

# Savollarni yuklash
ALL_QUESTIONS = []
try:
    with open("questions.json", "r", encoding="utf-8") as file:
        ALL_QUESTIONS = json.load(file)
except FileNotFoundError:
    ALL_QUESTIONS = []

BLOCK_SIZE = 50

def get_blocks_keyboard(chat_id):
    builder = InlineKeyboardBuilder()
    total = len(ALL_QUESTIONS)
    block_count = (total + BLOCK_SIZE - 1) // BLOCK_SIZE
    for i in range(block_count):
        builder.button(text=f"📦 Blok {i+1}", callback_data=f"block:{i}:{chat_id}")
    builder.adjust(2)
    return builder.as_markup()

@dp.callback_query(F.data.startswith("block:"))
async def set_block(callback: types.CallbackQuery):
    _, block_idx, chat_id = callback.data.split(":")
    block_idx, chat_id = int(block_idx), int(chat_id)
    
    start, end = block_idx * BLOCK_SIZE, (block_idx + 1) * BLOCK_SIZE
    block_qs = ALL_QUESTIONS[start:end].copy()
    random.shuffle(block_qs)
    
    games[chat_id] = {
        "questions": block_qs,
        "current_index": 0,
        "results": {},
        "is_group": callback.message.chat.type in ["group", "supergroup"],
        "block_num": block_idx + 1,
        "current_msg_id": None
    }
    await callback.message.edit_text(f"{block_idx+1}-blok tanlandi. Test boshlanmoqda...")
    await send_next_question(chat_id)

async def send_next_question(chat_id):
    game = games.get(chat_id)
    if not game or game["current_index"] >= len(game["questions"]):
        return await finish_quiz(chat_id)
        
    q = game["questions"][game["current_index"]]
    options = list(q["options"])
    random.shuffle(options)
    correct_idx = options.index(q["correct"])
    
    game["current_correct_idx"] = correct_idx
    
    try:
        # Limitdan qochish uchun ozgina pauza
        await asyncio.sleep(1) 
        poll = await bot.send_poll(
            chat_id=chat_id,
            question=f"Savol {game['current_index']+1}: {q['question']}",
            options=options,
            type="quiz",
            correct_option_id=correct_idx,
            is_anonymous=False
        )
        game["current_msg_id"] = poll.message_id
        poll_to_chat[poll.poll.id] = chat_id
    except Exception as e:
        print(f"Xatolik: {e}")

@dp.poll_answer()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    chat_id = poll_to_chat.get(poll_answer.poll_id)
    game = games.get(chat_id)
    if not game: return
    
    if poll_answer.option_ids and poll_answer.option_ids[0] == game["current_correct_idx"]:
        user = poll_answer.user.id
        game["results"][user] = game["results"].get(user, 0) + 1
        
    # FAQAT shaxsiy chatlarda tezlashtiramiz, guruhda taymer kutadi
    if not game["is_group"]:
        game["current_index"] += 1
        await send_next_question(chat_id)

async def finish_quiz(chat_id):
    game = games.pop(chat_id, None)
    if game:
        await bot.send_message(chat_id, "Test yakunlandi!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
