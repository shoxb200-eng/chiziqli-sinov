import os
import json
import random
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

TOKEN = "8603585449:AAGCZJFndbzUNTLXSHNyFiowXMUmrxKi6p0"

bot = Bot(token=TOKEN)
dp = Dispatcher()

games = {}
poll_to_chat = {}

# Savollarni yuklash
ALL_QUESTIONS = []
try:
    with open("questions.json", "r", encoding="utf-8") as file:
        ALL_QUESTIONS = json.load(file)
    print(f"Yuklandi: {len(ALL_QUESTIONS)} ta savol.")
except FileNotFoundError:
    ALL_QUESTIONS = [{"question": f"Test {i}", "options": ["A", "B"], "correct": "A"} for i in range(1, 101)]

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
    await message.answer("Salom! /quiz buyrug'i orqali testni boshlang.")

@dp.message(Command("quiz"))
async def choose_block_msg(message: types.Message):
    if message.chat.id in games:
        return await message.answer("Test allaqachon boshlangan!")
    await message.answer("Blokni tanlang:", reply_markup=get_blocks_keyboard(message.chat.id))

@dp.callback_query(F.data.startswith("block:"))
async def set_block_and_show_timer(callback: types.CallbackQuery):
    _, block_idx, chat_id = callback.data.split(":")
    block_idx, chat_id = int(block_idx), int(chat_id)
    
    start_idx = block_idx * BLOCK_SIZE
    end_idx = min((block_idx + 1) * BLOCK_SIZE, len(ALL_QUESTIONS))
    
    # MUHIM: Faqat shu blokni ajratib olish va aralashtirish
    block_questions = ALL_QUESTIONS[start_idx:end_idx].copy()
    random.shuffle(block_questions)
    
    games[chat_id] = {
        "questions": block_questions,
        "current_index": 0,
        "results": {},
        "block_num": block_idx + 1,
        "time_limit": 30,
        "current_msg_id": None
    }
    
    await callback.message.edit_text(f"{block_idx+1}-blok tanlandi. Davom etish uchun vaqtni tanlang (30s):")
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

@dp.poll_answer()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    chat_id = poll_to_chat.get(poll_answer.poll_id)
    game = games.get(chat_id)
    if not game: return
    
    if poll_answer.option_ids[0] == game["current_correct_idx"]:
        user = poll_answer.user.id
        game["results"][user] = game["results"].get(user, 0) + 1
        
    game["current_index"] += 1
    await send_next_question(chat_id)

async def finish_quiz(chat_id):
    game = games.pop(chat_id, None)
    if not game: return
    await bot.send_message(chat_id, "Test yakunlandi!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
