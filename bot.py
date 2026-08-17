import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import TOKEN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPLICATIONS_FILE = os.path.join(BASE_DIR, "applications.json")
BINDING_FILE = os.path.join(BASE_DIR, "staff_binding.json")
PENDING_FILE = os.path.join(BASE_DIR, "pending.json")
ACCEPTED_FILE = os.path.join(BASE_DIR, "accepted.json")

_staff_group_raw = os.environ.get("STAFF_GROUP_ID", "").strip()
try:
    STAFF_GROUP_ID = int(_staff_group_raw) if _staff_group_raw else None
except ValueError:
    STAFF_GROUP_ID = None


def load_bindings():
    try:
        with open(BINDING_FILE, encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def save_bindings(d):
    try:
        with open(BINDING_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def load_target(key):
    env_map = {"zrra": "ZRRA_GROUP_ID", "tgk": "TGK_GROUP_ID"}
    if key in env_map:
        v = os.environ.get(env_map[key], "").strip()
        if v:
            try:
                return int(v)
            except ValueError:
                pass
    return load_bindings().get(key)


BINDINGS = load_bindings()
BOUND_GROUP_ID = BINDINGS.get("inbox") if BINDINGS.get("inbox") is not None else STAFF_GROUP_ID

# Конфиг по должностям: куда слать одноразовую ссылку (invite) и где выдавать права/префикс (promote)
ROLE_CONFIG = {
    "МОДЕРАТОР ZRRA": {"invite": "zrra", "promote": ["zrra"]},
    "МОДЕРАТОР ++": {"invite": "zrra", "promote": ["zrra", "tgk"]},
    "МОДЕРАТОР ТГК": {"invite": None, "promote": ["tgk"]},
    "ХЕЛПЕР / ПОМОЩНИК": {"invite": None, "promote": ["tgk"]},
}

KEY_NAMES = {
    "inbox": "группа приёма заявок ZGS STAFF",
    "zrra": "целевая группа ЗРР (одноразовые ссылки + права)",
    "tgk": "официальный ТГК (выдача прав/префикса, без ссылки)",
}

# Красивый префикс (админ-титул), выдаваемый при выдаче прав. Максимум 16 символов.
TITLE_BY_POS = {
    "МОДЕРАТОР ZRRA": "Модератор ZRRA",
    "МОДЕРАТОР ТГК": "Модератор ТГК",
    "МОДЕРАТОР ++": "Модератор++",
    "ХЕЛПЕР / ПОМОЩНИК": "Хелпер",
}

# Права, которые бот выдаёт принятым (умеренный набор модератора)
MOD_PERMISSIONS = dict(
    can_manage_chat=True,
    can_delete_messages=True,
    can_restrict_members=True,
    can_invite_users=True,
    can_pin_messages=True,
    can_manage_topics=True,
)


def load_pending():
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_pending(d):
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def load_accepted():
    try:
        with open(ACCEPTED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_accepted(d):
    try:
        with open(ACCEPTED_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


PENDING = load_pending()
ACCEPTED = load_accepted()  # user_id -> {"gid": ..., "title": ...}


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
    pos = context.user_data["position"]
    data = {
        "user_id": user.id,
        "username": user.username,
        "position": pos,
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

    lines = [f"✅ Заявка на должность «{pos}» принята!", "", "Ваши ответы:"]
    for i, a in enumerate(data["answers"], 1):
        lines.append(f"{i}. {a['q']}\n   ➜ {a['a']}")
    summary = "\n".join(lines)
    await context.bot.send_message(chat_id, summary)

    if BOUND_GROUP_ID:
        try:
            if user.username:
                who = f"@{user.username}"
            else:
                who = f"tg://user?id={user.id} (ID: {user.id})"
            app_id = f"{user.id}_{int(time.time())}"
            PENDING[app_id] = {"user_id": user.id, "position": pos}
            save_pending(PENDING)

            header = f"👤 Заявка от {who}\n📋 Должность: {pos}\n\n"
            group_lines = [header.rstrip()]
            for i, a in enumerate(data["answers"], 1):
                group_lines.append(f"{i}. {a['q']}\n   ➜ {a['a']}")
            group_text = "\n".join(group_lines)
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Принять", callback_data=f"accept:{app_id}"),
                    InlineKeyboardButton("❌ Отказать", callback_data=f"reject:{app_id}"),
                ],
                [
                    InlineKeyboardButton("🚫 Заблокировать", callback_data=f"ban:{app_id}"),
                ],
            ])
            await context.bot.send_message(BOUND_GROUP_ID, group_text, reply_markup=kb)
        except Exception as exc:
            logging.exception("Failed to forward application to group: %s", exc)

    context.user_data.clear()


async def decision_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        action, app_id = query.data.split(":", 1)
    except Exception:
        return
    app = PENDING.pop(app_id, None)
    save_pending(PENDING)
    if app is None:
        try:
            await query.edit_message_text(
                (query.message.text or "") + "\n\n(заявка уже обработана)"
            )
        except Exception:
            pass
        return

    user_id = app["user_id"]
    pos = app["position"]
    role_cfg = ROLE_CONFIG.get(pos, {"invite": None, "promote": []})

    if action == "accept":
        await accept_user(context, user_id, pos, role_cfg)
        note = "✅ Принято модератором"
    elif action == "reject":
        try:
            await context.bot.send_message(user_id, "❌ К сожалению, ваша заявка отклонена.")
        except Exception:
            pass
        note = "❌ Отклонено модератором"
    else:  # ban
        await ban_user(context, user_id, pos)
        note = "🚫 Заблокирован модератором"

    try:
        await query.edit_message_text((query.message.text or "") + f"\n\n{note}")
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def ban_user(context: ContextTypes.DEFAULT_TYPE, user_id, pos):
    targets = []
    for gkey in ("zrra", "tgk"):
        gid = load_target(gkey)
        if gid:
            targets.append(gid)
    if BOUND_GROUP_ID:
        targets.append(BOUND_GROUP_ID)
    for gid in dict.fromkeys(targets):
        try:
            await context.bot.ban_chat_member(gid, user_id)
        except Exception as exc:
            logging.warning("ban failed in %s for %s: %s", gid, user_id, exc)
    try:
        await context.bot.send_message(user_id, "🚫 Вы заблокированы.")
    except Exception:
        pass


async def try_promote(context, gid, user_id, title):
    try:
        await context.bot.promote_chat_member(gid, user_id, **MOD_PERMISSIONS)
        try:
            await context.bot.set_chat_administrator_custom_title(
                gid, user_id, custom_title=title[:16]
            )
        except Exception:
            pass
        logging.info("Promoted user %s in group %s as %s", user_id, gid, title)
        return True
    except Exception as exc:
        logging.warning("Failed to promote user %s in %s: %s", user_id, gid, exc)
        return False


async def accept_user(context: ContextTypes.DEFAULT_TYPE, user_id, pos, role_cfg):
    invite_key = role_cfg.get("invite")
    promote_groups = role_cfg.get("promote", [])
    title = TITLE_BY_POS.get(pos, pos)

    # Одноразовая ссылка-приглашение (если должность предполагает группу ЗРР)
    if invite_key:
        target = load_target(invite_key)
        inv = None
        if target:
            try:
                link = await context.bot.create_chat_invite_link(target, member_limit=1)
                inv = link.invite_link
            except Exception as exc:
                logging.warning("create_chat_invite_link failed: %s", exc)
        if pos == "МОДЕРАТОР ZRRA":
            text = (
                f"✅ Вы приняты!\n{inv}"
                if inv else "✅ Вы приняты! (не удалось создать ссылку — свяжитесь с админом)"
            )
        else:  # МОДЕРАТОР ++
            text = (
                f"✅ Вы приняты в МОДЕРАТОР++! Вам выдадут префикс и права в основном ТГК.\n{inv}"
                if inv else "✅ Вы приняты в МОДЕРАТОР++! (ссылка не создана — свяжитесь с админом)"
            )
        try:
            await context.bot.send_message(user_id, text)
        except Exception:
            pass
    else:
        if pos == "МОДЕРАТОР ТГК":
            text = "✅ Вы приняты! Скоро вам выдадут префикс и права, ожидайте."
        else:  # ХЕЛПЕР
            text = "✅ Вы приняты в ХЕЛПЕР! Вам выдадут префикс и права."
        try:
            await context.bot.send_message(user_id, text)
        except Exception:
            pass

    # Выдача прав/префикса
    pending_promote = []
    for gkey in promote_groups:
        gid = load_target(gkey)
        if not gid:
            continue
        ok = await try_promote(context, gid, user_id, title)
        if not ok:
            pending_promote.append(gid)

    if pending_promote:
        ACCEPTED[str(user_id)] = {"promote": pending_promote, "title": title}
        save_accepted(ACCEPTED)
    return "готово"


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    if cm is None:
        return
    new_member = cm.new_chat_member
    if new_member is None or new_member.status != "member":
        return
    uid = str(new_member.user.id)
    gid = cm.chat.id
    rec = ACCEPTED.get(uid)
    if not rec or gid not in rec.get("promote", []):
        return
    ok = await try_promote(context, gid, new_member.user.id, rec.get("title") or "STAFF")
    if ok:
        rec["promote"].remove(gid)
        if rec["promote"]:
            ACCEPTED[uid] = rec
        else:
            ACCEPTED.pop(uid, None)
        save_accepted(ACCEPTED)


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    text = f"🆔 ID этой группы/чата: {chat.id}\n🙋 Ваш ID: {user.id}"
    if chat.username:
        text += f"\n@{chat.username}"
    if update.message:
        await update.message.reply_text(text)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.my_chat_member
    if cm is None:
        return
    new = cm.new_chat_member
    if new is not None and new.status in ("member", "administrator"):
        try:
            await context.bot.send_message(
                cm.chat.id,
                f"✅ Бот добавлен сюда.\n🆔 ID этой группы: {cm.chat.id}\n\n"
                "Чтобы привязать как цель:\n"
                "!привязать зрра\n!привязать тгк\n"
                "(или задай переменную окружения ZRRA_GROUP_ID / TGK_GROUP_ID)",
            )
        except Exception:
            pass


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Заявка отменена. Чтобы начать заново — /start.")
    return ConversationHandler.END


async def bind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        if update.message:
            await update.message.reply_text("Команду привязки можно использовать только в группе.")
        return
    gid = chat.id

    arg = None
    if context.args:
        arg = context.args[0]
    elif update.message and update.message.text:
        m = re.search(r"привязать\s*(\S+)?", update.message.text, re.IGNORECASE)
        arg = m.group(1) if (m and m.group(1)) else None
    raw = (arg.lower() if arg else "inbox")

    NORMALIZE = {
        "зрра": "zrra", "zrra": "zrra",
        "тгк": "tgk", "tgk": "tgk",
        "inbox": "inbox",
    }
    key = NORMALIZE.get(raw)
    if key is None:
        await update.message.reply_text(
            "❌ Неизвестный ключ. Доступно:\n"
            "  !привязать — группа приёма заявок\n"
            "  !привязать зрра — целевая группа ЗРР\n"
            "  !привязать тгк — официальный ТГК (права/префикс)"
        )
        return

    binds = load_bindings()
    old = binds.get(key)
    binds[key] = gid
    save_bindings(binds)
    if key == "inbox":
        global BOUND_GROUP_ID
        BOUND_GROUP_ID = gid
    msg = (
        f"✅ Привязана {KEY_NAMES[key]}.\nID группы: {gid}\n"
    )
    if old is not None and old != gid:
        msg += f"(была привязана другая: {old} — заменено)\n"
    msg += "(чтобы привязка переживала редеплои, можно также задать STAFF_GROUP_ID, но файл сохраняется на диске)"
    await update.message.reply_text(msg)


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
    app.add_handler(CallbackQueryHandler(decision_cb, pattern="^(accept|reject|ban):"))
    app.add_handler(
        ChatMemberHandler(on_chat_member, ChatMemberHandler.ANY_CHAT_MEMBER)
    )
    app.add_handler(CommandHandler("bind", bind_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(r"(?i)^!?\s*привязать(?:\s+\S+)?$"), bind_cmd)
    )
    app.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    logging.info("ZGS STAFF BOT запущен.")
    app.run_polling(drop_pending_updates=True, bootstrap_retries=30, close_loop=False)


if __name__ == "__main__":
    main()
