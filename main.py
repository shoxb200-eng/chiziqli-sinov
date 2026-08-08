import os
import json
import html
import random
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("quiz-bot")

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

QUESTIONS_FILE = os.environ.get("QUESTIONS_FILE", "questions.json")

bot = Bot(token=TOKEN)
dp = Dispatcher()

games = {}
poll_to_chat = {}


def load_questions(path):
    """Load and validate the question bank. Raises on unusable input."""
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = json.load(file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Question file {path!r} not found") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Question file {path!r} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Question file {path!r} could not be read: {exc}") from exc

    if not isinstance(raw, list):
        raise RuntimeError(f"Question file {path!r} must contain a list of questions")

    questions = []
    for index, item in enumerate(raw):
        problem = validate_question(item)
        if problem:
            logger.warning("Skipping question #%d in %s: %s", index + 1, path, problem)
            continue
        questions.append(item)

    if not questions:
        raise RuntimeError(f"Question file {path!r} contains no usable questions")
    logger.info("Loaded %d/%d questions from %s", len(questions), len(raw), path)
    return questions


def validate_question(item):
    """Return a description of why the question is unusable, or None if it is fine."""
    if not isinstance(item, dict):
        return "entry is not an object"
    for key in ("question", "options", "correct"):
        if key not in item:
            return f"missing {key!r} field"
    if not isinstance(item["options"], list) or len(item["options"]) < 2:
        return "'options' must be a list of at least two answers"
    if find_correct_index(item["options"], item["correct"]) is None:
        return "'correct' does not match any of the options"
    return None


def find_correct_index(options, correct):
    """Index of the correct answer within options, or None when absent."""
    target = str(correct).strip().lower()
    for i, opt in enumerate(options):
        if str(opt).strip().lower() == target:
            return i
    return None


ALL_QUESTIONS = load_questions(QUESTIONS_FILE)

BLOCK_SIZE = 50


async def safe_stop_poll(chat_id, message_id):
    if message_id is None:
        return
    try:
        await bot.stop_poll(chat_id, message_id)
    except TelegramAPIError as exc:
        # The poll is often already closed or deleted; that is not fatal.
        logger.debug("Could not stop poll %s in chat %s: %s", message_id, chat_id, exc)


async def report_to_chat(chat_id, text, parse_mode=None):
    try:
        await bot.send_message(chat_id, text, parse_mode=parse_mode)
    except TelegramAPIError as exc:
        logger.warning("Could not send message to chat %s: %s", chat_id, exc)


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
    await message.answer("👋 Viktorina botiga xush kelibsiz!\n/quiz - Testni boshlash\n/stop - To'xtatish va natijalarni ko'rish")


@dp.message(Command("quiz"))
async def choose_block_msg(message: types.Message):
    await message.answer("📚 Viktorina blokini tanlang:", reply_markup=get_blocks_keyboard(message.chat.id))


@dp.message(Command("stop"))
async def stop_quiz_cmd(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in games:
        return await message.answer("❌ Hozirda hech qanday faol test mavjud emas.")

    # Taymerni va poll-ni to'xtatamiz
    game = games[chat_id]
    await cancel_timer(game)
    await safe_stop_poll(chat_id, game.get("current_msg_id"))

    await message.answer("🛑 Viktorina to'xtatildi. Natijalar hisoblanmoqda...")
    await finish_quiz(chat_id)


@dp.callback_query(F.data.startswith("block:"))
async def set_block_and_show_timer(callback: types.CallbackQuery):
    try:
        _, block_idx, chat_id = callback.data.split(":")
        block_idx, chat_id = int(block_idx), int(chat_id)
    except ValueError:
        logger.warning("Malformed block callback data: %r", callback.data)
        return await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)

    block_data = ALL_QUESTIONS[block_idx * BLOCK_SIZE : (block_idx + 1) * BLOCK_SIZE]
    if not block_data:
        logger.warning("Empty question block %s requested in chat %s", block_idx, chat_id)
        return await callback.answer("❌ Bu blokda savollar yo'q.", show_alert=True)

    shuffled_questions = []
    for q in block_data:
        opts = list(q["options"])
        random.shuffle(opts)
        shuffled_questions.append({"question": q["question"], "options": opts, "correct": q["correct"]})
    random.shuffle(shuffled_questions)

    games[chat_id] = {
        "questions": shuffled_questions, "current_index": 0, "time_limit": 30,
        "results": {}, "block_num": block_idx + 1, "task": None, "current_msg_id": None,
    }

    builder = InlineKeyboardBuilder()
    for t in [15, 30, 60]:
        builder.button(text=f"{t} sek", callback_data=f"time:{t}:{chat_id}")
    await callback.answer()
    try:
        await callback.message.edit_text("⏱ Vaqtni tanlang:", reply_markup=builder.as_markup())
    except TelegramAPIError as exc:
        logger.warning("Could not show timer keyboard in chat %s: %s", chat_id, exc)
        games.pop(chat_id, None)
        await report_to_chat(chat_id, "⚠️ Testni boshlashda xatolik yuz berdi, /quiz bilan qaytadan urinib ko'ring.")


@dp.callback_query(F.data.startswith("time:"))
async def set_time_and_start(callback: types.CallbackQuery):
    try:
        _, seconds, chat_id = callback.data.split(":")
        seconds, chat_id = int(seconds), int(chat_id)
    except ValueError:
        logger.warning("Malformed time callback data: %r", callback.data)
        return await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)

    game = games.get(chat_id)
    if game is None:
        # The bot restarted or the quiz was stopped after the keyboard was sent.
        return await callback.answer("❌ Test topilmadi, /quiz bilan qaytadan boshlang.", show_alert=True)

    game["time_limit"] = seconds
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramAPIError as exc:
        logger.debug("Could not delete timer message in chat %s: %s", chat_id, exc)
    await send_next_question(chat_id)


async def send_next_question(chat_id):
    game = games.get(chat_id)
    if game is None or game["current_index"] >= len(game["questions"]):
        return await finish_quiz(chat_id)

    q = game["questions"][game["current_index"]]
    correct_idx = find_correct_index(q["options"], q["correct"])
    if correct_idx is None:
        logger.warning("Skipping question with no matching correct option in chat %s", chat_id)
        game["current_index"] += 1
        return await send_next_question(chat_id)

    try:
        poll_msg = await bot.send_poll(
            chat_id,
            f"🎲 {game['block_num']}-Blok | {game['current_index']+1}-savol:\n{q['question']}"[:300],
            options=[str(o)[:100] for o in q["options"]],
            type="quiz", correct_option_id=correct_idx, is_anonymous=False,
        )
    except TelegramAPIError as exc:
        logger.error("Could not send poll to chat %s: %s", chat_id, exc)
        await report_to_chat(chat_id, "⚠️ Savolni yuborishda xatolik yuz berdi. Test to'xtatildi.")
        return await finish_quiz(chat_id)

    game["current_msg_id"] = poll_msg.message_id
    poll_to_chat[poll_msg.poll.id] = chat_id
    game["task"] = asyncio.create_task(wait_for_timer(chat_id, game["time_limit"]))


async def cancel_timer(game):
    """Cancel the pending timer task and wait for it to actually stop."""
    task = game.get("task")
    game["task"] = None
    if task is None or task.done() or task is asyncio.current_task():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def wait_for_timer(chat_id, duration):
    try:
        await asyncio.sleep(duration)
        if chat_id in games:
            games[chat_id]["current_index"] += 1
            await send_next_question(chat_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Nobody awaits this task, so an unlogged failure would stall the quiz silently.
        logger.exception("Quiz timer for chat %s failed", chat_id)
        await report_to_chat(chat_id, "⚠️ Testni davom ettirishda xatolik yuz berdi.")


@dp.poll_answer()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    chat_id = poll_to_chat.get(poll_answer.poll_id)
    game = games.get(chat_id) if chat_id is not None else None
    if game is None:
        logger.debug("Poll answer for unknown poll %s ignored", poll_answer.poll_id)
        return
    if not poll_answer.option_ids:
        # The user retracted their vote; keep the current question running.
        return

    await cancel_timer(game)
    await safe_stop_poll(chat_id, game.get("current_msg_id"))

    if game["current_index"] >= len(game["questions"]):
        return await finish_quiz(chat_id)

    q = game["questions"][game["current_index"]]
    correct_idx = find_correct_index(q["options"], q["correct"])

    user_id = poll_answer.user.id
    if user_id not in game["results"]:
        game["results"][user_id] = {"name": poll_answer.user.full_name, "correct": 0}
    if correct_idx is not None and poll_answer.option_ids[0] == correct_idx:
        game["results"][user_id]["correct"] += 1

    game["current_index"] += 1
    await send_next_question(chat_id)


async def finish_quiz(chat_id):
    game = games.pop(chat_id, None)
    if game is None:
        return
    await cancel_timer(game)
    results = game["results"]

    report = f"🏁 <b>{game['block_num']}-Blok natijalari:</b>\n"
    if not results:
        report += "Hech kim javob bermadi."
    else:
        # Natijalarni saralash (eng ko'p to'g'ri javob bergan birinchi)
        sorted_res = sorted(results.values(), key=lambda x: x["correct"], reverse=True)
        for i, d in enumerate(sorted_res, 1):
            name = html.escape(str(d["name"]))
            report += f"{i}. 👤 {name} ➔ <b>{d['correct']} ta</b> to'g'ri\n"

    # User names are escaped: unescaped ones break the parser and drop the report.
    await report_to_chat(chat_id, report, parse_mode="HTML")


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot active"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()
    return runner


async def main():
    runner = None
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        runner = await start_web_server()
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "poll_answer"])
    finally:
        if runner is not None:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
