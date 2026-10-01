from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .catalog import EXERCISES, PROGRAMS
from .db import Item

BTN_WORKOUT = "🏋️ Тренировка"
BTN_PROGRESS = "📈 Прогресс"
BTN_PROGRAM = "📋 Программа"
BTN_SETTINGS = "⚙️ Настройки"


class RepCb(CallbackData, prefix="rep"):
    w: int  # workout id
    e: int  # exercise index
    s: int  # set index
    r: int  # reps


class WCb(CallbackData, prefix="w"):
    action: str  # weight | swap | skip | finish | finish_yes | finish_no | rest_skip | rest_add
    w: int


class SwapCb(CallbackData, prefix="swap"):
    w: int
    key: str


class StartCb(CallbackData, prefix="start"):
    action: str  # go | other
    day: int


class ProgCb(CallbackData, prefix="prog"):
    key: str


class MenuCb(CallbackData, prefix="m"):
    action: str  # programs | lifts | lift | scale | rest
    key: str = ""


class HistCb(CallbackData, prefix="hist"):
    key: str


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_WORKOUT)],
            [KeyboardButton(text=BTN_PROGRESS), KeyboardButton(text=BTN_PROGRAM), KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def rep_options(item: Item, set_idx: int) -> list[int]:
    lo, hi = item.reps_lo, item.reps_hi
    if lo != hi:
        return list(range(max(1, lo - 3), hi + 3))
    if item.amrap and set_idx == item.sets - 1:
        return list(range(max(1, lo - 3), lo + 8))
    return list(range(1, lo + 2))


def set_keyboard(workout_id: int, item: Item, ex_idx: int, set_idx: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    fixed_target = item.reps_lo == item.reps_hi and not (item.amrap and set_idx == item.sets - 1)
    for r in rep_options(item, set_idx):
        label = f"✅ {r}" if fixed_target and r == item.reps_lo else str(r)
        kb.button(text=label, callback_data=RepCb(w=workout_id, e=ex_idx, s=set_idx, r=r))
    kb.adjust(5)
    actions = [InlineKeyboardButton(text="⚖️ Вес", callback_data=WCb(action="weight", w=workout_id).pack())]
    if EXERCISES[item.ex_key].alternatives:
        actions.append(InlineKeyboardButton(text="🔄 Замена", callback_data=WCb(action="swap", w=workout_id).pack()))
    actions.append(InlineKeyboardButton(text="⏭ Пропустить", callback_data=WCb(action="skip", w=workout_id).pack()))
    kb.row(*actions)
    kb.row(InlineKeyboardButton(text="🏁 Завершить тренировку", callback_data=WCb(action="finish", w=workout_id).pack()))
    return kb.as_markup()


def rest_keyboard(workout_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="+30 сек", callback_data=WCb(action="rest_add", w=workout_id).pack()),
            InlineKeyboardButton(text="⏭ Дальше", callback_data=WCb(action="rest_skip", w=workout_id).pack()),
        ]]
    )


def finish_confirm(workout_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, завершить", callback_data=WCb(action="finish_yes", w=workout_id).pack()),
            InlineKeyboardButton(text="↩️ Продолжить", callback_data=WCb(action="finish_no", w=workout_id).pack()),
        ]]
    )


def swap_keyboard(workout_id: int, ex_key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for alt in EXERCISES[ex_key].alternatives:
        kb.button(text=EXERCISES[alt].name, callback_data=SwapCb(w=workout_id, key=alt))
    kb.adjust(1)
    return kb.as_markup()


def start_keyboard(day_idx: int, other_idx: int, other_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Начать", callback_data=StartCb(action="go", day=day_idx).pack())],
            [InlineKeyboardButton(text=f"🔀 Вместо этого: {other_name}", callback_data=StartCb(action="other", day=other_idx).pack())],
        ]
    )


def programs_keyboard(current: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in PROGRAMS.values():
        kb.button(text=("✅ " if p.key == current else "") + p.name, callback_data=ProgCb(key=p.key))
    kb.adjust(1)
    return kb.as_markup()


def program_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Изменить рабочий вес", callback_data=MenuCb(action="lifts").pack())],
            [InlineKeyboardButton(text="🔁 Сменить программу", callback_data=MenuCb(action="programs").pack())],
        ]
    )


def lifts_keyboard(ex_keys: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key in ex_keys:
        kb.button(text=EXERCISES[key].name, callback_data=MenuCb(action="lift", key=key))
    kb.adjust(1)
    return kb.as_markup()


def history_keyboard(ex_keys: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key in ex_keys:
        kb.button(text=EXERCISES[key].name, callback_data=HistCb(key=key))
    kb.adjust(2)
    return kb.as_markup()


REST_CHOICES = [0, 60, 90, 120, 180]


def settings_keyboard(inc_scale: float, rest_override: int) -> InlineKeyboardMarkup:
    scale = "обычный" if inc_scale >= 1 else "мелкий"
    rest = "по программе" if not rest_override else f"{rest_override} сек"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Шаг прибавки веса: {scale}", callback_data=MenuCb(action="scale").pack())],
            [InlineKeyboardButton(text=f"Отдых между подходами: {rest}", callback_data=MenuCb(action="rest").pack())],
        ]
    )
