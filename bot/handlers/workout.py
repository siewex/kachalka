"""The workout itself: exercise cards, set buttons, rest timer, finishing."""

import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import media, planner, service, texts
from ..catalog import EXERCISES
from ..config import Config
from ..db import Database
from ..runtime import RestTimer, Runtime

log = logging.getLogger(__name__)
router = Router()

REST_TICK = 15  # seconds between countdown edits; Telegram dislikes frequent edits


class Form(StatesGroup):
    weight = State()


WEIGHT_HINTS = {
    "barbell": "Вес штанги целиком, вместе с грифом (обычно гриф 20 кг).",
    "dumbbell": "Вес одной гантели.",
    "cable": "Вес по стеку блока.",
    "machine": "Вес блинов на тренажёре.",
    "bodyweight": "Дополнительный вес. Без отягощения — напиши 0.",
}


async def _quiet(coro):
    try:
        return await coro
    except TelegramBadRequest:
        return None


# --- building blocks ---


async def suggested_weight(db: Database, user_id: int, ex_key: str) -> float | None:
    user = await db.get_user(user_id)
    if not user or not user.profile:
        return None
    return planner.start_weight(EXERCISES[ex_key], planner.Profile.from_dict(user.profile))


async def ask_weight(bot: Bot, chat_id: int, user_id: int, db: Database, state: FSMContext, ex_key: str,
                     target: str) -> None:
    ex = EXERCISES[ex_key]
    await state.set_state(Form.weight)
    await state.update_data(target=target, ex=ex_key)
    text = f"⚖️ Какой рабочий вес для «{ex.name}»?\n{WEIGHT_HINTS[ex.equipment]}\n\nНапиши число, например <code>40</code> или <code>42,5</code>."
    suggestion = await suggested_weight(db, user_id, ex_key) if target == "workout" else None
    markup = None
    if suggestion is not None:
        text += (f"\n\n💡 По твоей анкете для старта предлагаю <b>{texts.weight_text(suggestion, ex)}</b>. "
                 "Сделай разминочный подход: если целевые повторы даются слишком легко — добавь вес.")
        markup = kb.weight_pick(suggestion, texts.weight_text(suggestion, ex))
    elif target == "workout":
        text += "\n\nНе знаешь? Сделай пару разминочных подходов и выбери вес, с которым целевые повторы даются уверенно. Лучше начать легче — прогрессия быстро догонит."
    await bot.send_message(chat_id, text, reply_markup=markup)


async def ask_bodyweight(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(Form.weight)
    await state.update_data(target="body", ex=None)
    await bot.send_message(chat_id, "⚖️ Сколько ты весишь сегодня? Напиши число в кг, например <code>74,3</code>.\n"
                                    "Лучше взвешиваться утром натощак — так цифры сравнимы.")


async def close_prompt(bot: Bot, rt: Runtime, user_id: int, text: str | None = None) -> bool:
    prompt = rt.prompts.pop(user_id, None)
    if not prompt:
        return False
    chat_id, message_id = prompt
    if text:
        await _quiet(bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id))
    else:
        await _quiet(bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None))
    return True


async def drop_timer(bot: Bot, rt: Runtime, user_id: int) -> None:
    timer = rt.pop_timer(user_id)
    if timer:
        await _quiet(bot.delete_message(timer.chat_id, timer.message_id))


async def send_set_prompt(bot: Bot, chat_id: int, user_id: int, db: Database, rt: Runtime, prefix: str = "") -> None:
    w = await db.active_workout(user_id)
    item = await service.current_item(db, w) if w else None
    if item is None or item.weight is None:
        return
    await close_prompt(bot, rt, user_id)
    msg = await bot.send_message(
        chat_id, prefix + texts.set_prompt(item, w.set_idx),
        reply_markup=kb.set_keyboard(w.id, item, w.ex_idx, w.set_idx),
    )
    rt.prompts[user_id] = (chat_id, msg.message_id)


async def show_exercise(
    bot: Bot, chat_id: int, user_id: int, db: Database, rt: Runtime, config: Config, state: FSMContext,
) -> None:
    w = await db.active_workout(user_id)
    item = await service.current_item(db, w) if w else None
    if item is None:
        return
    total = len(await db.items(w.id))
    last = await db.last_session_sets(user_id, item.ex_key, exclude_workout=w.id)
    await media.send_exercise(
        bot, chat_id, db, config.media_dir, item.ex_key, texts.exercise_card(item, item.idx + 1, total, last)
    )
    if item.weight is None:
        await ask_weight(bot, chat_id, user_id, db, state, item.ex_key, target="workout")
    else:
        await send_set_prompt(bot, chat_id, user_id, db, rt)


async def start_rest(bot: Bot, chat_id: int, user_id: int, db: Database, rt: Runtime, workout_id: int, seconds: int) -> None:
    await drop_timer(bot, rt, user_id)
    msg = await bot.send_message(chat_id, texts.rest_text(seconds, seconds), reply_markup=kb.rest_keyboard(workout_id))
    task = asyncio.create_task(_rest_loop(bot, chat_id, user_id, db, rt))
    rt.timers[user_id] = RestTimer(task, chat_id, msg.message_id, workout_id, time.monotonic() + seconds, seconds)


async def _rest_loop(bot: Bot, chat_id: int, user_id: int, db: Database, rt: Runtime) -> None:
    try:
        while True:
            timer = rt.timers.get(user_id)
            if timer is None:
                return
            left = timer.left()
            if left <= 0:
                break
            await asyncio.sleep(min(REST_TICK, left))
            timer = rt.timers.get(user_id)
            if timer and timer.left() > 0:
                await _quiet(bot.edit_message_text(
                    text=texts.rest_text(timer.left(), timer.total), chat_id=chat_id,
                    message_id=timer.message_id, reply_markup=kb.rest_keyboard(timer.workout_id),
                ))
        await drop_timer(bot, rt, user_id)
        await send_set_prompt(bot, chat_id, user_id, db, rt, prefix="⏰ <b>Отдых окончен!</b>\n\n")
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("rest timer failed")


async def finish(bot: Bot, chat_id: int, user_id: int, db: Database, rt: Runtime, state: FSMContext) -> None:
    await drop_timer(bot, rt, user_id)
    await close_prompt(bot, rt, user_id)
    await state.clear()
    w = await db.active_workout(user_id)
    if not w:
        return
    summary = await service.finish_workout(db, w)
    await bot.send_message(chat_id, texts.summary_text(summary), reply_markup=kb.main_menu())


async def record(
    bot: Bot, chat_id: int, user_id: int, db: Database, rt: Runtime, config: Config, state: FSMContext,
    reps: int, expect: tuple[int, int] | None = None,
) -> None:
    w = await db.active_workout(user_id)
    if not w:
        raise service.WorkoutError("Нет активной тренировки")
    set_idx = w.set_idx
    res = await service.log_set(db, w, reps, expect)
    await drop_timer(bot, rt, user_id)
    done = texts.set_done(res.item, set_idx, reps)
    if not await close_prompt(bot, rt, user_id, done):
        await bot.send_message(chat_id, done)

    if res.kind == "next_set":
        await start_rest(bot, chat_id, user_id, db, rt, w.id, res.item.rest)
        return
    await bot.send_message(chat_id, texts.exercise_result(res.item, res.sets, res.outcome))
    if res.kind == "exercise_done":
        await show_exercise(bot, chat_id, user_id, db, rt, config, state)
    else:
        await finish(bot, chat_id, user_id, db, rt, state)


# --- entry point ---


@router.message(F.text == kb.BTN_WORKOUT)
async def workout_menu(message: Message, db: Database, rt: Runtime, config: Config, state: FSMContext, bot: Bot):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if not user or not user.program:
        await message.answer("Сначала выбери программу:", reply_markup=kb.programs_keyboard(None))
        return
    if await db.active_workout(user.user_id):
        await message.answer("Продолжаем тренировку 💪")
        await show_exercise(bot, message.chat.id, user.user_id, db, rt, config, state)
        return
    program = await service.get_program(db, user)
    if program is None:
        await message.answer("Сначала выбери программу:", reply_markup=kb.programs_keyboard(None))
        return
    idx, _ = service.day_for(program, user)
    await message.answer(**await _preview(db, user, idx))


async def _preview(db: Database, user, idx: int) -> dict:
    program = await service.get_program(db, user)
    lifts = await db.all_lifts(user.user_id)
    profile = planner.Profile.from_dict(user.profile) if user.profile else None
    suggest = (lambda ex: planner.start_weight(ex, profile)) if profile else None
    text = texts.day_preview(program, idx, lifts, suggest=suggest)
    if len(program.days) > 1:
        other = (idx + 1) % len(program.days)
        markup = kb.start_keyboard(idx, other, program.days[other].name)
    else:
        markup = kb.start_keyboard(idx, idx, program.days[idx].name)
    return {"text": text, "reply_markup": markup}


@router.callback_query(kb.StartCb.filter())
async def on_start(cb: CallbackQuery, callback_data: kb.StartCb, db: Database, rt: Runtime, config: Config,
                   state: FSMContext, bot: Bot):
    user = await db.get_user(cb.from_user.id)
    if not user or not user.program:
        await cb.answer("Сначала выбери программу", show_alert=True)
        return
    if callback_data.action == "other":
        await _quiet(cb.message.edit_text(**await _preview(db, user, callback_data.day)))
        await cb.answer()
        return
    try:
        await service.start_workout(db, user, callback_data.day)
    except service.WorkoutError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await _quiet(cb.message.edit_reply_markup(reply_markup=None))
    await cb.answer("Поехали!")
    await show_exercise(bot, cb.message.chat.id, user.user_id, db, rt, config, state)


# --- sets ---


@router.callback_query(kb.RepCb.filter())
async def on_rep(cb: CallbackQuery, callback_data: kb.RepCb, db: Database, rt: Runtime, config: Config,
                 state: FSMContext, bot: Bot):
    w = await db.get_workout(callback_data.w)
    if not w or w.status != "active" or w.user_id != cb.from_user.id:
        await cb.answer("Эта тренировка уже закрыта")
        await _quiet(cb.message.edit_reply_markup(reply_markup=None))
        return
    rt.prompts[cb.from_user.id] = (cb.message.chat.id, cb.message.message_id)
    try:
        await record(bot, cb.message.chat.id, cb.from_user.id, db, rt, config, state, callback_data.r,
                     expect=(callback_data.e, callback_data.s))
    except service.WorkoutError as e:
        await cb.answer(str(e))
        return
    await cb.answer()


@router.message(Command("cancel", "finish"))
async def cmd_finish(message: Message, db: Database):
    w = await db.active_workout(message.from_user.id)
    if not w:
        await message.answer("Сейчас нет активной тренировки.")
        return
    await message.answer(
        "Завершить тренировку? Недоделанные упражнения не изменят рабочие веса.",
        reply_markup=kb.finish_confirm(w.id),
    )


async def save_weight(message: Message, user_id: int, db: Database, rt: Runtime, config: Config,
                      state: FSMContext, bot: Bot, value: float) -> None:
    data = await state.get_data()
    if data.get("target") == "body":
        if not 30 <= value <= 300:
            await message.answer("Не похоже на вес тела. Напиши число в кг, например 74,3.")
            return
        await state.clear()
        await db.add_bodyweight(user_id, value)
        user = await db.get_user(user_id)
        if user and user.profile:
            user.profile["weight"] = value
            await db.save_profile(user_id, user.profile)
        await message.answer("Записал ✅\n\n" + texts.bodyweight_text(await db.bodyweights(user_id), config.tz))
        return

    ex = EXERCISES[data["ex"]]
    if value > 500 or (value <= 0 and not ex.bodyweight):
        await message.answer("Не похоже на рабочий вес. Напиши число в кг, например 40.")
        return
    await state.clear()
    w = await db.active_workout(user_id)
    item = await service.current_item(db, w) if w else None
    if data["target"] == "workout" and item and item.ex_key == ex.key:
        await service.set_weight(db, w, value)
        await message.answer(f"Записал: {texts.weight_text(value, ex)}")
        await send_set_prompt(bot, message.chat.id, user_id, db, rt)
    else:
        await db.set_lift(user_id, ex.key, value)
        await message.answer(f"Рабочий вес «{ex.name}»: {texts.weight_text(value, ex)}")


@router.callback_query(kb.WeightPickCb.filter(), StateFilter(Form.weight))
async def on_weight_pick(cb: CallbackQuery, callback_data: kb.WeightPickCb, db: Database, rt: Runtime,
                         config: Config, state: FSMContext, bot: Bot):
    await cb.answer()
    await _quiet(cb.message.edit_reply_markup(reply_markup=None))
    await save_weight(cb.message, cb.from_user.id, db, rt, config, state, bot, callback_data.centi / 100)


@router.callback_query(kb.WeightPickCb.filter())
async def on_weight_pick_stale(cb: CallbackQuery):
    await cb.answer("Вес уже задан")


@router.message(StateFilter(None, Form.weight), F.text.regexp(r"^\s*\d{1,3}([.,]\d{1,2})?\s*$"))
async def on_number(message: Message, db: Database, rt: Runtime, config: Config, state: FSMContext, bot: Bot):
    value = float(message.text.strip().replace(",", "."))
    user_id = message.from_user.id

    if await state.get_state() == Form.weight.state:
        await save_weight(message, user_id, db, rt, config, state, bot, value)
        return

    if not value.is_integer() or value > 100:
        await message.answer("Повторы — целое число, например 8.")
        return
    try:
        await record(bot, message.chat.id, user_id, db, rt, config, state, int(value))
    except service.WorkoutError as e:
        await message.answer(f"{e}. Нажми «{kb.BTN_WORKOUT}», чтобы начать.")


# --- workout actions ---


@router.callback_query(kb.WCb.filter())
async def on_action(cb: CallbackQuery, callback_data: kb.WCb, db: Database, rt: Runtime, config: Config,
                    state: FSMContext, bot: Bot):
    user_id, chat_id = cb.from_user.id, cb.message.chat.id
    w = await db.get_workout(callback_data.w)
    if not w or w.status != "active" or w.user_id != user_id:
        await cb.answer("Эта тренировка уже закрыта")
        await _quiet(cb.message.edit_reply_markup(reply_markup=None))
        return
    item = await service.current_item(db, w)
    action = callback_data.action

    if action == "rest_add":
        timer = rt.timers.get(user_id)
        if timer:
            timer.ends_at += 30
            timer.total += 30
            await _quiet(cb.message.edit_text(texts.rest_text(timer.left(), timer.total), reply_markup=kb.rest_keyboard(timer.workout_id)))
        await cb.answer("+30 секунд")
    elif action == "rest_skip":
        await drop_timer(bot, rt, user_id)
        await cb.answer()
        await send_set_prompt(bot, chat_id, user_id, db, rt)
    elif action == "weight" and item:
        await cb.answer()
        await ask_weight(bot, chat_id, user_id, db, state, item.ex_key, target="workout")
    elif action == "swap" and item:
        await cb.answer()
        await cb.message.answer(f"На что заменить «{EXERCISES[item.ex_key].name}»?",
                                reply_markup=kb.swap_keyboard(w.id, item.ex_key))
    elif action == "skip" and item:
        await cb.answer("Пропускаем")
        await drop_timer(bot, rt, user_id)
        rt.prompts[user_id] = (chat_id, cb.message.message_id)
        await close_prompt(bot, rt, user_id, f"⏭ Пропущено: {EXERCISES[item.ex_key].name}")
        if await service.skip_exercise(db, w):
            await finish(bot, chat_id, user_id, db, rt, state)
        else:
            await show_exercise(bot, chat_id, user_id, db, rt, config, state)
    elif action == "finish":
        await cb.answer()
        await cb.message.answer(
            "Завершить тренировку? Недоделанные упражнения не изменят рабочие веса.",
            reply_markup=kb.finish_confirm(w.id),
        )
    elif action == "finish_yes":
        await cb.answer()
        await _quiet(cb.message.delete())
        await finish(bot, chat_id, user_id, db, rt, state)
    elif action == "finish_no":
        await cb.answer("Продолжаем 💪")
        await _quiet(cb.message.delete())
    else:
        await cb.answer()


@router.callback_query(kb.SwapCb.filter())
async def on_swap(cb: CallbackQuery, callback_data: kb.SwapCb, db: Database, rt: Runtime, config: Config,
                  state: FSMContext, bot: Bot):
    w = await db.get_workout(callback_data.w)
    if not w or w.status != "active" or w.user_id != cb.from_user.id:
        await cb.answer("Эта тренировка уже закрыта")
        return
    try:
        item = await service.swap_exercise(db, w, callback_data.key)
    except service.WorkoutError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await cb.answer()
    await _quiet(cb.message.edit_text(f"🔄 Заменено на «{EXERCISES[item.ex_key].name}»"))
    await drop_timer(bot, rt, cb.from_user.id)
    await close_prompt(bot, rt, cb.from_user.id)
    await show_exercise(bot, cb.message.chat.id, cb.from_user.id, db, rt, config, state)
