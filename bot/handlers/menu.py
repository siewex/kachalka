"""Start, program choice, progress, settings."""

import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import texts
from ..catalog import EXERCISES, PROGRAMS
from ..config import Config
from ..db import Database
from .workout import ask_weight

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if not user:
        user = await db.create_user(message.from_user.id, message.from_user.first_name or "")
    if not user.program:
        await message.answer(
            f"Привет, {escape(user.name)}! 👋\n\n"
            "Я веду тренировки: показываю упражнения с техникой, считаю подходы, держу отдых "
            "и сам решаю, какой вес ставить в следующий раз.\n\nВыбери программу:",
            reply_markup=kb.main_menu(),
        )
        await message.answer(_programs_text(), reply_markup=kb.programs_keyboard(None))
        return
    await message.answer("С возвращением! Жми «🏋️ Тренировка», когда будешь в зале.", reply_markup=kb.main_menu())


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


def _programs_text() -> str:
    parts = []
    for p in PROGRAMS.values():
        days = ", ".join(d.name for d in p.days)
        parts.append(f"<b>{escape(p.name)}</b>\n{escape(p.description)}\n<i>{escape(days)}</i>")
    return "\n\n".join(parts)


@router.callback_query(kb.ProgCb.filter())
async def on_program(cb: CallbackQuery, callback_data: kb.ProgCb, db: Database):
    if await db.active_workout(cb.from_user.id):
        await cb.answer("Сначала заверши текущую тренировку", show_alert=True)
        return
    user = await db.get_user(cb.from_user.id) or await db.create_user(cb.from_user.id, cb.from_user.first_name or "")
    await db.update_user(user.user_id, program=callback_data.key, day_idx=0)
    program = PROGRAMS[callback_data.key]
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Программа: <b>{escape(program.name)}</b>\n\n{escape(program.description)}\n\n"
        "Перед первым подходом каждого упражнения я спрошу рабочий вес. "
        "Начни легче, чем кажется, — прогрессия быстро догонит."
    )


# --- program ---


@router.message(F.text == kb.BTN_PROGRAM)
async def program_menu(message: Message, db: Database, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if not user or not user.program:
        await message.answer(_programs_text(), reply_markup=kb.programs_keyboard(None))
        return
    program = PROGRAMS[user.program]
    lifts = await db.all_lifts(user.user_id)
    parts = [f"<b>{escape(program.name)}</b>\n{escape(program.description)}"]
    for i in range(len(program.days)):
        parts.append(texts.day_preview(user.program, i, lifts, with_program=False))
    nxt = program.days[user.day_idx % len(program.days)].name
    parts.append(f"Следующая по плану: <b>{escape(nxt)}</b>")
    await message.answer("\n\n".join(parts), reply_markup=kb.program_menu())


@router.callback_query(kb.MenuCb.filter(F.action == "programs"))
async def on_programs(cb: CallbackQuery, db: Database):
    user = await db.get_user(cb.from_user.id)
    await cb.answer()
    await cb.message.answer(_programs_text(), reply_markup=kb.programs_keyboard(user.program if user else None))


@router.callback_query(kb.MenuCb.filter(F.action == "lifts"))
async def on_lifts(cb: CallbackQuery, db: Database):
    user = await db.get_user(cb.from_user.id)
    keys: list[str] = []
    if user and user.program:
        for day in PROGRAMS[user.program].days:
            for it in day.items:
                for k in (it.ex, *EXERCISES[it.ex].alternatives):
                    if k not in keys:
                        keys.append(k)
    await cb.answer()
    await cb.message.answer("Для какого упражнения поменять рабочий вес?", reply_markup=kb.lifts_keyboard(keys))


@router.callback_query(kb.MenuCb.filter(F.action == "lift"))
async def on_lift(cb: CallbackQuery, callback_data: kb.MenuCb, state: FSMContext, bot: Bot):
    await cb.answer()
    await ask_weight(bot, cb.message.chat.id, state, callback_data.key, target="lift")


# --- progress ---


@router.message(F.text == kb.BTN_PROGRESS)
async def progress(message: Message, db: Database, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    total, month = await db.workout_stats(user_id, int(time.time()) - 30 * 86400)
    exercises = await db.trained_exercises(user_id)
    if not exercises:
        await message.answer("Пока нет завершённых тренировок. Самое время начать 💪")
        return
    lifts = await db.all_lifts(user_id)
    lines = [f"📈 Тренировок всего: <b>{total}</b>, за 30 дней: <b>{month}</b>", "", "Текущие рабочие веса:"]
    for key in exercises:
        ex = EXERCISES[key]
        lines.append(f"• {escape(ex.name)}: {texts.weight_text(lifts.get(key), ex)}")
    lines += ["", "Выбери упражнение, чтобы посмотреть историю:"]
    await message.answer("\n".join(lines), reply_markup=kb.history_keyboard(exercises))


@router.callback_query(kb.HistCb.filter())
async def on_history(cb: CallbackQuery, callback_data: kb.HistCb, db: Database, config: Config):
    ex = EXERCISES[callback_data.key]
    sessions = await db.history(cb.from_user.id, ex.key, limit=10)
    lift = await db.get_lift(cb.from_user.id, ex.key)
    await cb.answer()
    await cb.message.answer(texts.history_text(ex, sessions, lift[0] if lift else None, config.tz))


# --- settings ---


def _settings_text() -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        "<b>Шаг прибавки</b>: «мелкий» — прибавлять вес вдвое меньшими шагами "
        "(например +2,5 кг вместо +5 в приседе). Удобно, если рост веса идёт тяжело.\n"
        "<b>Отдых</b>: «по программе» — у тяжёлых базовых упражнений отдых длиннее."
    )


@router.message(F.text == kb.BTN_SETTINGS)
async def settings(message: Message, db: Database, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id) or await db.create_user(
        message.from_user.id, message.from_user.first_name or ""
    )
    await message.answer(_settings_text(), reply_markup=kb.settings_keyboard(user.inc_scale, user.rest_override))


@router.callback_query(kb.MenuCb.filter(F.action.in_({"scale", "rest"})))
async def on_setting(cb: CallbackQuery, callback_data: kb.MenuCb, db: Database):
    user = await db.get_user(cb.from_user.id)
    if callback_data.action == "scale":
        await db.update_user(user.user_id, inc_scale=0.5 if user.inc_scale >= 1 else 1.0)
    else:
        choices = kb.REST_CHOICES
        nxt = choices[(choices.index(user.rest_override) + 1) % len(choices)] if user.rest_override in choices else 0
        await db.update_user(user.user_id, rest_override=nxt)
    user = await db.get_user(cb.from_user.id)
    await cb.answer("Сохранено. Отдых применится со следующей тренировки." if callback_data.action == "rest" else "Сохранено")
    await cb.message.edit_reply_markup(reply_markup=kb.settings_keyboard(user.inc_scale, user.rest_override))


@router.message()
async def fallback(message: Message):
    await message.answer(
        "Не понял 🤔 Пользуйся кнопками меню. Во время тренировки можно просто писать число повторов.",
        reply_markup=kb.main_menu(),
    )
