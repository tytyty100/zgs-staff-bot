import json
import logging
import os
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import TOKEN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPLICATIONS_FILE = os.path.join(BASE_DIR, "applications.json")
_staff_group_raw = os.environ.get("STAFF_GROUP_ID", "").strip()
try:
    STAFF_GROUP_ID = int(_staff_group_raw) if _staff_group_raw else None
except ValueError:
    STAFF_GROUP_ID = None

SELECT_POSITION, QUESTION = range(2)

POSITIONS = [
    "МОДЕРАТОР ТГК",
    "МОДЕРАТОР ZRRA",
    "МОДЕРАТОР ++",
    "ХЕЛПЕР / ПОМОЩНИК",
]

# Тип вопроса: "text" или ("choice", [варианты])
COMMON_QUESTIONS = [
    ("Сколько вам полных лет?", "text"),
    ("Укажите ваш пол:", ("choice", ["Мужской", "Женский", "Другое"])),
    ("Почему вы выбрали именно эту должность?", "text"),
    ("Какой у вас опыт в модераторстве в других ТГК? (если опыта нет — напишите «нет»)", "text"),
    ("Сколько вы в сообществе Zoro Game Store?", ("choice", ["Неделя", "Месяц", "Год", "2 года и более"])),
]

EXTRA_QUESTIONS = {
    "МОДЕРАТОР ТГК": [
        ("В каких именно чатах или каналах вы модерировали? (названия/ссылки)", "text"),
        ("Примерный размер тех сообществ (количество участников)?", "text"),
        ("С какими нарушениями чаще всего приходилось сталкиваться?", "text"),
        ("Готовы ли вы дежурить в ночное время?", ("choice", ["Да", "Нет"])),
        ("Есть ли у вас опыт работы с ботами-модераторами?", ("choice", ["Да", "Нет"])),
    ],
    "МОДЕРАТОР ZRRA": [
        ("Что вы знаете о проекте ZRRA?", "text"),
        ("Какие навыки у вас есть, полезные для ZRRA?", "text"),
        ("Готовы ли вы проверять репорты игроков?", ("choice", ["Да", "Нет"])),
        ("Сколько времени в день готовы уделять проекту?", "text"),
        ("Были ли конфликты с администрацией других проектов? Если да — опишите.", "text"),
    ],
    "МОДЕРАТОР ++": [
        ("Чем, по-вашему, модератор++ отличается от обычного модератора?", "text"),
        ("Готовы ли вы обучать новых модераторов?", ("choice", ["Да", "Нет"])),
        ("Опишите опыт разбора сложных конфликтов.", "text"),
        ("Готовы ли брать на себя дополнительные дежурства?", ("choice", ["Да", "Нет"])),
        ("Укажите ваш Telegram username для связи (с @).", "text"),
    ],
    "ХЕЛПЕР / ПОМОЩНИК": [
        ("Чем именно вы готовы помогать (консультации, техподдержка, контент)?", "text"),
        ("Хорошо ли вы знаете правила сообщества ZGS?", ("choice", ["Да", "Нет"])),
        ("Готовы ли помогать новичкам в личных сообщениях?", ("choice", ["Да", "Нет"])),
        ("Сколько времени в неделю можете уделять помощи?", "text"),
        ("Есть ли у вас полезные навыки (дизайн, программирование, видео, перевод)?", "text"),
    ],
}

QUESTIONS = {pos: COMMON_QUESTIONS + EXTRA_QUESTIONS[pos] for pos in POSITIONS}
TOTAL = len(COMMON_QUESTIONS) + 5  # 10


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(p, callback_data=f"pos:{p}")] for p in POSITIONS
    ])
    text = "Здравствуйте, пожалуйста выберете должность:"
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        logging.info(
            "Bot is in group chat_id=%s — set STAFF_GROUP_ID to this value to receive applications there",
            chat.id,
        )
    return SELECT_POSITION


async def pos_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pos = query.data.split(":", 1)[1]
    context.user_data["position"] = pos
    context.user_data["answers"] = []
    context.user_data["q_index"] = 0
    context.user_data["await_choice"] = False
    await ask_question(update, context)
    return QUESTION


async def ask_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pos = context.user_data["position"]
    idx = context.user_data["q_index"]
    q = QUESTIONS[pos][idx]
    text = f"Вопрос {idx + 1}/{TOTAL}:\n{q[0]}"
    if isinstance(q[1], tuple) and q[1][0] == "choice":
        opts = q[1][1]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(o, callback_data=f"ans:{i}")] for i, o in enumerate(opts)
        ])
        context.user_data["await_choice"] = True
        await context.bot.send_message(chat_id, text, reply_markup=kb)
    else:
        context.user_data["await_choice"] = False
        await context.bot.send_message(chat_id, text)


async def question_text_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("await_choice"):
        await update.message.reply_text("Пожалуйста, выберите вариант кнопкой ниже.")
        return QUESTION
    answer = update.message.text
    pos = context.user_data["position"]
    idx = context.user_data["q_index"]
    q = QUESTIONS[pos][idx]
    context.user_data["answers"].append({"q": q[0], "a": answer})
    context.user_data["q_index"] += 1
    if context.user_data["q_index"] >= TOTAL:
        await finalize(update, context)
        return ConversationHandler.END
    await ask_question(update, context)
    return QUESTION


async def question_choice_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_choice"):
        return QUESTION
    query = update.callback_query
    await query.answer()
    pos = context.user_data["position"]
    idx = context.user_data["q_index"]
    q = QUESTIONS[pos][idx]
    opts = q[1][1]
    choice_idx = int(query.data.split(":", 1)[1])
    chosen = opts[choice_idx]
    context.user_data["answers"].append({"q": q[0], "a": chosen})
    context.user_data["q_index"] += 1
    try:
        await query.edit_message_text(f"{q[0]}\n✅ Ваш ответ: {chosen}")
    except Exception:
        pass
    if context.user_data["q_index"] >= TOTAL:
        await finalize(update, context)
        return ConversationHandler.END
    await ask_question(update, context)
    return QUESTION


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    data = {
        "user_id": user.id,
        "username": user.username,
        "position": context.user_data["position"],
        "answers": context.user_data["answers"],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        arr = []
        if os.path.exists(APPLICATIONS_FILE):
            with open(APPLICATIONS_FILE, encoding="utf-8") as f:
                arr = json.load(f)
        arr.append(data)
        with open(APPLICATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(arr, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    lines = [f"✅ Заявка на должность «{data['position']}» принята!", "", "Ваши ответы:"]
    for i, a in enumerate(data["answers"], 1):
        lines.append(f"{i}. {a['q']}\n   ➜ {a['a']}")
    summary = "\n".join(lines)
    await context.bot.send_message(chat_id, summary)

    if STAFF_GROUP_ID:
        try:
            await context.bot.send_message(
                STAFF_GROUP_ID, "📥 Новая заявка в ZGS STAFF:\n" + summary
            )
        except Exception:
            pass

    context.user_data.clear()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Заявка отменена. Чтобы начать заново — /start.")
    return ConversationHandler.END


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_POSITION: [CallbackQueryHandler(pos_cb, pattern="^pos:")],
            QUESTION: [
                CallbackQueryHandler(question_choice_cb, pattern="^ans:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, question_text_cb),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    app.add_handler(conv)
    logging.info("ZGS STAFF BOT запущен.")
    app.run_polling(drop_pending_updates=True, bootstrap_retries=30, close_loop=False)


if __name__ == "__main__":
    main()
