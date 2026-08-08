import random

from aiogram.utils.keyboard import InlineKeyboardBuilder


def find_correct_option_index(question):
    """Index of the option matching the question's correct answer (0 if none)."""
    correct = str(question["correct"]).strip().lower()
    return next(
        (i for i, opt in enumerate(question["options"]) if str(opt).strip().lower() == correct),
        0,
    )


def shuffle_questions(questions):
    """Copy of the questions with both options and question order shuffled."""
    shuffled = []
    for q in questions:
        options = list(q["options"])
        random.shuffle(options)
        shuffled.append({"question": q["question"], "options": options, "correct": q["correct"]})
    random.shuffle(shuffled)
    return shuffled


def build_keyboard(buttons, columns=1):
    """Inline keyboard from (text, callback_data) pairs."""
    builder = InlineKeyboardBuilder()
    for text, callback_data in buttons:
        builder.button(text=text, callback_data=callback_data)
    builder.adjust(columns)
    return builder.as_markup()
