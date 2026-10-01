"""Questionnaire → personal program, starting weights and nutrition."""

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import nutrition, planner, service, texts
from ..catalog import build_program
from ..db import Database

router = Router()


class Survey(StatesGroup):
    answering = State()


# step -> (question, options) ; options None means a number typed in chat: (min, max, unit)
STEPS: list[tuple[str, str, list[tuple[str, str]] | tuple[float, float, str]]] = [
    ("sex", "Пол? Нужен для расчёта калорий и стартовых весов.", [("m", "Мужской"), ("f", "Женский")]),
    ("age", "Сколько тебе лет? Напиши числом.", (14, 80, "лет")),
    ("height", "Рост в сантиметрах? Например, <code>178</code>.", (130, 220, "см")),
    ("weight", "Вес в килограммах? Например, <code>74,5</code>.", (35, 250, "кг")),
    ("activity", "Насколько ты активен вне зала?",
     [(str(k), v[0]) for k, v in planner.ACTIVITY.items()]),
    ("goal", "Главная цель?", list(planner.GOALS.items())),
    ("focus", "На что сделать акцент?", list(planner.FOCUSES.items())),
    ("level", "Опыт тренировок в зале?", [(str(k), v) for k, v in planner.LEVELS.items()]),
    ("days", "Сколько дней в неделю готов тренироваться?", [(str(d), f"{d}") for d in planner.DAYS_OPTIONS]),
    ("minutes", "Сколько времени на одну тренировку?", [(str(m), f"{m} мин") for m in planner.MINUTES_OPTIONS]),
    ("restrictions", "Что-то беспокоит? Отметь всё, что относится, — такие упражнения не попадут в программу.", None),
]
STEP_KEYS = [s[0] for s in STEPS]
INT_FIELDS = {"activity", "level", "days", "minutes", "age", "height"}


async def start_survey(bot: Bot, chat_id: int, user_id: int, db: Database, state: FSMContext) -> None:
    user = await db.get_user(user_id)
    await state.set_state(Survey.answering)
    await state.set_data({"answers": {}, "prev": (user.profile if user else None) or {}, "restrictions": []})
    await bot.send_message(
        chat_id,
        "🧩 <b>Подберём программу</b>\n11 коротких вопросов — и я соберу тренировки, стартовые веса и ориентир по питанию.",
    )
    await _ask(bot, chat_id, state, 0)


async def _ask(bot: Bot, chat_id: int, state: FSMContext, idx: int) -> None:
    key, question, options = STEPS[idx]
    data = await state.get_data()
    await state.update_data(step=idx)
    head = f"<b>{idx + 1}/{len(STEPS)}</b> · {question}"
    if key == "restrictions":
        await bot.send_message(chat_id, head, reply_markup=kb.restrictions_keyboard(planner.RESTRICTIONS, data["restrictions"]))
    elif isinstance(options, list):
        cols = 2 if key in ("sex", "days", "minutes") else 1
        await bot.send_message(chat_id, head, reply_markup=kb.choice_keyboard(key, options, cols))
    else:
        prev = data["prev"].get(key)
        markup = None
        if prev:
            label = f"Оставить {texts.num(prev)} {options[2]}"
            markup = kb.choice_keyboard(key, [(str(prev), label)])
        await bot.send_message(chat_id, head, reply_markup=markup)


async def _answer(bot: Bot, chat_id: int, user_id: int, db: Database, state: FSMContext, key: str, value) -> None:
    data = await state.get_data()
    answers = data["answers"]
    if key != "restrictions":  # restrictions are collected separately by the toggle buttons
        answers[key] = int(float(value)) if key in INT_FIELDS else value
        await state.update_data(answers=answers)
    idx = STEP_KEYS.index(key) + 1
    if idx < len(STEPS):
        await _ask(bot, chat_id, state, idx)
    else:
        await _review(bot, chat_id, user_id, db, state)


async def _review(bot: Bot, chat_id: int, user_id: int, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    profile = planner.Profile(**data["answers"], restrictions=data["restrictions"])
    plan = planner.generate(profile)
    await state.update_data(profile=profile.to_dict(), plan=plan)

    program = build_program(plan)
    lifts = await db.all_lifts(user_id)
    suggest = lambda ex: planner.start_weight(ex, profile)  # noqa: E731
    parts = [f"📋 <b>{escape(program.name)}</b>", escape(program.description)]
    for i in range(len(program.days)):
        parts.append(texts.day_preview(program, i, lifts, with_program=False, suggest=suggest))
    await bot.send_message(chat_id, "\n\n".join(parts))
    n = nutrition.calculate(profile)
    await bot.send_message(
        chat_id, texts.nutrition_text(n, nutrition.advice(profile)), reply_markup=kb.survey_confirm()
    )


# --- handlers ---


@router.message(Command("survey"))
async def cmd_survey(message: Message, db: Database, state: FSMContext, bot: Bot):
    await start_survey(bot, message.chat.id, message.from_user.id, db, state)


@router.callback_query(kb.MenuCb.filter(F.action == "survey"))
async def on_survey(cb: CallbackQuery, db: Database, state: FSMContext, bot: Bot):
    await cb.answer()
    await start_survey(bot, cb.message.chat.id, cb.from_user.id, db, state)


@router.callback_query(kb.SurveyCb.filter(F.field == "restr"), StateFilter(Survey.answering))
async def on_restriction(cb: CallbackQuery, callback_data: kb.SurveyCb, db: Database, state: FSMContext, bot: Bot):
    data = await state.get_data()
    selected = data["restrictions"]
    if callback_data.value == "done":
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=None)
        await _answer(bot, cb.message.chat.id, cb.from_user.id, db, state, "restrictions", None)
        return
    if callback_data.value in selected:
        selected.remove(callback_data.value)
    else:
        selected.append(callback_data.value)
    await state.update_data(restrictions=selected)
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=kb.restrictions_keyboard(planner.RESTRICTIONS, selected))


@router.callback_query(kb.SurveyCb.filter(F.field == "confirm"))
async def on_confirm(cb: CallbackQuery, db: Database, state: FSMContext):
    data = await state.get_data()
    if "plan" not in data:
        await cb.answer("Анкета устарела — пройди её заново", show_alert=True)
        return
    user_id = cb.from_user.id
    profile = data["profile"]
    if not await db.get_user(user_id):
        await db.create_user(user_id, cb.from_user.first_name or "")
    await db.save_profile(user_id, profile)
    await db.set_custom_program(user_id, data["plan"])
    await db.update_user(user_id, program=service.CUSTOM, day_idx=0)
    await db.add_bodyweight(user_id, profile["weight"])
    await state.clear()
    await cb.answer("Сохранено!")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "✅ Программа сохранена. В зале жми «🏋️ Тренировка» — для каждого упражнения я предложу стартовый вес.\n\n"
        "Раз в неделю записывай вес тела в «📈 Прогресс», чтобы видеть, идёт ли масса или рельеф.",
        reply_markup=kb.main_menu(),
    )


@router.callback_query(kb.SurveyCb.filter(), StateFilter(Survey.answering))
async def on_choice(cb: CallbackQuery, callback_data: kb.SurveyCb, db: Database, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if STEP_KEYS[data.get("step", 0)] != callback_data.field:
        await cb.answer("Этот вопрос уже позади")
        return
    await cb.answer()
    pressed = callback_data.pack()
    label = next((b.text for row in cb.message.reply_markup.inline_keyboard for b in row
                  if b.callback_data == pressed), callback_data.value)
    await cb.message.edit_text(f"{cb.message.html_text}\n<b>→ {escape(label)}</b>")
    await _answer(bot, cb.message.chat.id, cb.from_user.id, db, state, callback_data.field, _parse_value(callback_data))


def _parse_value(cd: kb.SurveyCb):
    if cd.field == "weight":
        return float(cd.value)
    return cd.value


@router.callback_query(kb.SurveyCb.filter())
async def on_stale(cb: CallbackQuery):
    await cb.answer("Анкета уже закрыта. Запусти заново: /survey", show_alert=True)


@router.message(StateFilter(Survey.answering), F.text)
async def on_text(message: Message, db: Database, state: FSMContext, bot: Bot):
    data = await state.get_data()
    key, _, options = STEPS[data.get("step", 0)]
    if isinstance(options, list) or options is None:
        await message.answer("Выбери вариант кнопкой выше 👆")
        return
    lo, hi, unit = options
    try:
        value = float(message.text.strip().replace(",", "."))
    except ValueError:
        value = None
    if value is None or not lo <= value <= hi:
        await message.answer(f"Напиши число от {texts.num(lo)} до {texts.num(hi)} ({unit}).")
        return
    if key != "weight":
        value = int(round(value))
    else:
        value = round(value, 1)
    await _answer(bot, message.chat.id, message.from_user.id, db, state, key, value)
