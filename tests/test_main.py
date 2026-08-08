import asyncio
import builtins
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import main


@pytest.fixture(autouse=True)
def clean_state():
    main.games.clear()
    main.poll_to_chat.clear()
    yield
    main.games.clear()
    main.poll_to_chat.clear()


@pytest.fixture
def fake_bot(monkeypatch):
    bot = MagicMock()
    bot.stop_poll = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_poll = AsyncMock(
        return_value=SimpleNamespace(message_id=111, poll=SimpleNamespace(id="poll-1"))
    )
    monkeypatch.setattr(main, "bot", bot)
    return bot


@pytest.fixture
def questions(monkeypatch):
    data = [
        {"question": f"Savol {i}", "options": [f"{i}a", f"{i}b", f"{i}c"], "correct": f"{i}b"}
        for i in range(1, 121)
    ]
    monkeypatch.setattr(main, "ALL_QUESTIONS", data)
    return data


def make_message(chat_id=42):
    message = MagicMock()
    message.chat.id = chat_id
    message.answer = AsyncMock()
    return message


def make_callback(data, chat_id=42):
    callback = MagicMock()
    callback.data = data
    callback.message.edit_text = AsyncMock()
    callback.message.delete = AsyncMock()
    callback.message.chat.id = chat_id
    return callback


def make_poll_answer(poll_id="poll-1", option_ids=(0,), user_id=7, name="Ali"):
    return SimpleNamespace(
        poll_id=poll_id,
        option_ids=list(option_ids),
        user=SimpleNamespace(id=user_id, full_name=name),
    )


def keyboard_callback_data(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def keyboard_texts(markup):
    return [btn.text for row in markup.inline_keyboard for btn in row]


class TestQuestionsFile:
    def test_bundled_questions_have_valid_shape(self):
        with open(main.QUESTIONS_PATH, encoding="utf-8") as file:
            data = json.load(file)

        assert data
        for question in data:
            assert question["question"]
            assert len(question["options"]) >= 2
            assert question["correct"] in question["options"]

    def test_missing_file_falls_back_to_placeholder(self, monkeypatch):
        real_open = builtins.open

        def fail_on_questions(path, *args, **kwargs):
            if str(path).endswith("questions.json"):
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fail_on_questions)
        try:
            importlib.reload(main)
            assert main.ALL_QUESTIONS == [{"question": "Test", "options": ["A", "B"], "correct": "A"}]
        finally:
            monkeypatch.undo()
            importlib.reload(main)


class TestGetBlocksKeyboard:
    def test_block_count_rounds_up(self, questions):
        markup = main.get_blocks_keyboard(42)

        assert len(keyboard_callback_data(markup)) == 3

    def test_last_block_is_clamped_to_question_count(self, monkeypatch, questions):
        monkeypatch.setattr(main, "ALL_QUESTIONS", questions[:60])

        texts = keyboard_texts(main.get_blocks_keyboard(42))

        assert "1-50" in texts[0]
        assert "51-60" in texts[1]

    def test_callback_data_carries_block_index_and_chat(self, questions):
        assert keyboard_callback_data(main.get_blocks_keyboard(-100500)) == [
            "block:0:-100500",
            "block:1:-100500",
            "block:2:-100500",
        ]

    def test_two_buttons_per_row(self, questions):
        markup = main.get_blocks_keyboard(42)

        assert [len(row) for row in markup.inline_keyboard] == [2, 1]


class TestSafeStopPoll:
    async def test_stops_poll(self, fake_bot):
        await main.safe_stop_poll(42, 111)

        fake_bot.stop_poll.assert_awaited_once_with(42, 111)

    async def test_swallows_api_errors(self, fake_bot):
        fake_bot.stop_poll.side_effect = RuntimeError("poll already closed")

        await main.safe_stop_poll(42, 111)


class TestCommands:
    async def test_start_cmd_sends_help(self):
        message = make_message()

        await main.start_cmd(message)

        text = message.answer.await_args.args[0]
        assert "/quiz" in text and "/stop" in text

    async def test_quiz_cmd_offers_blocks(self, questions):
        message = make_message(chat_id=5)

        await main.choose_block_msg(message)

        markup = message.answer.await_args.kwargs["reply_markup"]
        assert keyboard_callback_data(markup)[0] == "block:0:5"

    async def test_stop_cmd_without_active_game(self, fake_bot):
        message = make_message()

        await main.stop_quiz_cmd(message)

        assert "faol test mavjud emas" in message.answer.await_args.args[0]
        fake_bot.stop_poll.assert_not_awaited()

    async def test_stop_cmd_cancels_timer_and_reports(self, fake_bot):
        task = MagicMock()
        main.games[42] = {
            "questions": [],
            "current_index": 0,
            "time_limit": 30,
            "results": {},
            "block_num": 1,
            "task": task,
            "current_msg_id": 111,
        }

        await main.stop_quiz_cmd(make_message())

        task.cancel.assert_called_once()
        fake_bot.stop_poll.assert_awaited_once_with(42, 111)
        fake_bot.send_message.assert_awaited_once()
        assert 42 not in main.games


class TestBlockSelection:
    async def test_creates_game_for_selected_block(self, questions):
        callback = make_callback("block:1:42")

        await main.set_block_and_show_timer(callback)

        game = main.games[42]
        assert game["block_num"] == 2
        assert game["current_index"] == 0
        assert game["time_limit"] == 30
        assert game["results"] == {}
        assert len(game["questions"]) == main.BLOCK_SIZE
        assert {q["question"] for q in game["questions"]} == {
            q["question"] for q in questions[50:100]
        }

    async def test_shuffling_preserves_options_and_answer(self, questions):
        await main.set_block_and_show_timer(make_callback("block:0:42"))

        by_text = {q["question"]: q for q in questions}
        for shuffled in main.games[42]["questions"]:
            original = by_text[shuffled["question"]]
            assert sorted(shuffled["options"]) == sorted(original["options"])
            assert shuffled["correct"] == original["correct"]

    async def test_offers_time_options(self, questions):
        callback = make_callback("block:0:42")

        await main.set_block_and_show_timer(callback)

        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        assert keyboard_callback_data(markup) == ["time:15:42", "time:30:42", "time:60:42"]

    async def test_negative_chat_id_is_parsed(self, questions):
        await main.set_block_and_show_timer(make_callback("block:0:-100500"))

        assert -100500 in main.games


class TestTimeSelection:
    async def test_sets_time_limit_and_starts(self, fake_bot, questions, monkeypatch):
        send_next = AsyncMock()
        monkeypatch.setattr(main, "send_next_question", send_next)
        main.games[42] = {"questions": [], "current_index": 0, "time_limit": 30, "results": {}, "block_num": 1}
        callback = make_callback("time:60:42")

        await main.set_time_and_start(callback)

        assert main.games[42]["time_limit"] == 60
        callback.message.delete.assert_awaited_once()
        send_next.assert_awaited_once_with(42)


class TestSendNextQuestion:
    def _start_game(self, questions, index=0, time_limit=30):
        main.games[42] = {
            "questions": questions,
            "current_index": index,
            "time_limit": time_limit,
            "results": {},
            "block_num": 3,
        }

    async def test_marks_correct_option_id(self, fake_bot):
        self._start_game([{"question": "Q", "options": ["a", "b", "c"], "correct": "c"}])

        await main.send_next_question(42)

        assert fake_bot.send_poll.await_args.kwargs["correct_option_id"] == 2
        main.games[42]["task"].cancel()

    async def test_correct_option_matching_ignores_case_and_spaces(self, fake_bot):
        self._start_game([{"question": "Q", "options": ["a", " B "], "correct": "b"}])

        await main.send_next_question(42)

        assert fake_bot.send_poll.await_args.kwargs["correct_option_id"] == 1
        main.games[42]["task"].cancel()

    async def test_unmatched_correct_answer_falls_back_to_first_option(self, fake_bot):
        self._start_game([{"question": "Q", "options": ["a", "b"], "correct": "z"}])

        await main.send_next_question(42)

        assert fake_bot.send_poll.await_args.kwargs["correct_option_id"] == 0
        main.games[42]["task"].cancel()

    async def test_truncates_long_question_and_options(self, fake_bot):
        self._start_game([{"question": "x" * 500, "options": ["y" * 200, "b"], "correct": "b"}])

        await main.send_next_question(42)

        assert len(fake_bot.send_poll.await_args.args[1]) == 300
        assert all(len(o) <= 100 for o in fake_bot.send_poll.await_args.kwargs["options"])
        main.games[42]["task"].cancel()

    async def test_poll_is_a_non_anonymous_quiz_with_header(self, fake_bot):
        self._start_game([{"question": "Q", "options": ["a", "b"], "correct": "b"}], index=1)
        main.games[42]["questions"].append({"question": "Q2", "options": ["a", "b"], "correct": "a"})

        await main.send_next_question(42)

        kwargs = fake_bot.send_poll.await_args.kwargs
        assert kwargs["type"] == "quiz"
        assert kwargs["is_anonymous"] is False
        assert "3-Blok | 2-savol" in fake_bot.send_poll.await_args.args[1]
        main.games[42]["task"].cancel()

    async def test_registers_poll_and_timer(self, fake_bot):
        self._start_game([{"question": "Q", "options": ["a", "b"], "correct": "b"}], time_limit=15)

        await main.send_next_question(42)

        assert main.poll_to_chat == {"poll-1": 42}
        assert main.games[42]["current_msg_id"] == 111
        assert isinstance(main.games[42]["task"], asyncio.Task)
        main.games[42]["task"].cancel()

    async def test_finishes_when_questions_are_exhausted(self, fake_bot):
        self._start_game([{"question": "Q", "options": ["a", "b"], "correct": "b"}], index=1)

        await main.send_next_question(42)

        fake_bot.send_poll.assert_not_awaited()
        fake_bot.send_message.assert_awaited_once()
        assert 42 not in main.games

    async def test_unknown_chat_is_ignored(self, fake_bot):
        await main.send_next_question(999)

        fake_bot.send_poll.assert_not_awaited()
        fake_bot.send_message.assert_not_awaited()


class TestWaitForTimer:
    async def test_advances_to_next_question_after_timeout(self, monkeypatch):
        monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
        send_next = AsyncMock()
        monkeypatch.setattr(main, "send_next_question", send_next)
        main.games[42] = {"questions": [], "current_index": 0, "time_limit": 30, "results": {}, "block_num": 1}

        await main.wait_for_timer(42, 30)

        main.asyncio.sleep.assert_awaited_once_with(30)
        assert main.games[42]["current_index"] == 1
        send_next.assert_awaited_once_with(42)

    async def test_stopped_game_is_not_advanced(self, monkeypatch):
        monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
        send_next = AsyncMock()
        monkeypatch.setattr(main, "send_next_question", send_next)

        await main.wait_for_timer(42, 30)

        send_next.assert_not_awaited()


class TestHandlePollAnswer:
    @pytest.fixture
    def running_game(self, monkeypatch):
        send_next = AsyncMock()
        monkeypatch.setattr(main, "send_next_question", send_next)
        task = MagicMock()
        main.poll_to_chat["poll-1"] = 42
        main.games[42] = {
            "questions": [{"question": "Q", "options": ["a", "b"], "correct": "b"}],
            "current_index": 0,
            "time_limit": 30,
            "results": {},
            "block_num": 1,
            "task": task,
            "current_msg_id": 111,
        }
        return SimpleNamespace(send_next=send_next, task=task, game=main.games[42])

    async def test_correct_answer_scores_a_point(self, fake_bot, running_game):
        await main.handle_poll_answer(make_poll_answer(option_ids=(1,)))

        assert running_game.game["results"] == {7: {"name": "Ali", "correct": 1}}

    async def test_wrong_answer_registers_user_without_point(self, fake_bot, running_game):
        await main.handle_poll_answer(make_poll_answer(option_ids=(0,)))

        assert running_game.game["results"] == {7: {"name": "Ali", "correct": 0}}

    async def test_scores_accumulate_per_user(self, fake_bot, running_game):
        running_game.game["results"] = {7: {"name": "Ali", "correct": 4}}

        await main.handle_poll_answer(make_poll_answer(option_ids=(1,)))

        assert running_game.game["results"][7]["correct"] == 5

    async def test_answer_cancels_timer_stops_poll_and_advances(self, fake_bot, running_game):
        await main.handle_poll_answer(make_poll_answer(option_ids=(1,)))

        running_game.task.cancel.assert_called_once()
        fake_bot.stop_poll.assert_awaited_once_with(42, 111)
        assert running_game.game["current_index"] == 1
        running_game.send_next.assert_awaited_once_with(42)

    async def test_unknown_poll_is_ignored(self, fake_bot, running_game):
        await main.handle_poll_answer(make_poll_answer(poll_id="other"))

        assert running_game.game["results"] == {}
        running_game.send_next.assert_not_awaited()

    async def test_answer_for_finished_game_is_ignored(self, fake_bot, running_game):
        main.games.pop(42)

        await main.handle_poll_answer(make_poll_answer())

        running_game.send_next.assert_not_awaited()
        fake_bot.stop_poll.assert_not_awaited()


class TestFinishQuiz:
    async def test_reports_when_nobody_answered(self, fake_bot):
        main.games[42] = {"questions": [], "current_index": 0, "time_limit": 30, "results": {}, "block_num": 2}

        await main.finish_quiz(42)

        report = fake_bot.send_message.await_args.args[1]
        assert "2-Blok natijalari" in report
        assert "Hech kim javob bermadi." in report
        assert fake_bot.send_message.await_args.kwargs["parse_mode"] == "Markdown"

    async def test_results_are_ranked_by_score(self, fake_bot):
        main.games[42] = {
            "questions": [],
            "current_index": 0,
            "time_limit": 30,
            "block_num": 1,
            "results": {
                1: {"name": "Ali", "correct": 2},
                2: {"name": "Vali", "correct": 5},
                3: {"name": "Guli", "correct": 3},
            },
        }

        await main.finish_quiz(42)

        lines = [l for l in fake_bot.send_message.await_args.args[1].splitlines() if "➔" in l]
        assert [l.split("👤 ")[1].split(" ➔")[0] for l in lines] == ["Vali", "Guli", "Ali"]
        assert [l.split(".")[0] for l in lines] == ["1", "2", "3"]
        assert "**5 ta**" in lines[0]

    async def test_game_state_is_cleared(self, fake_bot):
        main.games[42] = {"questions": [], "current_index": 0, "time_limit": 30, "results": {}, "block_num": 1}

        await main.finish_quiz(42)

        assert 42 not in main.games

    async def test_unknown_chat_is_ignored(self, fake_bot):
        await main.finish_quiz(999)

        fake_bot.send_message.assert_not_awaited()


class TestWebServer:
    async def test_health_endpoint_reports_active_on_configured_port(self, monkeypatch):
        port = 18080
        monkeypatch.setenv("PORT", str(port))

        runner = await main.start_web_server()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/") as resp:
                    assert resp.status == 200
                    assert await resp.text() == "Bot active"
        finally:
            await runner.cleanup()


class TestEntrypoint:
    async def test_main_drops_pending_updates_then_polls(self, fake_bot, monkeypatch):
        fake_bot.delete_webhook = AsyncMock()
        monkeypatch.setattr(main, "start_web_server", AsyncMock())
        monkeypatch.setattr(main.dp, "start_polling", AsyncMock())

        await main.main()

        fake_bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
        main.start_web_server.assert_awaited_once()
        main.dp.start_polling.assert_awaited_once_with(
            fake_bot, allowed_updates=["message", "callback_query", "poll_answer"]
        )
