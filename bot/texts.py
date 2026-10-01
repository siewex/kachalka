"""Message formatting (Telegram HTML)."""

import re
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from . import progression
from .catalog import EXERCISES, Exercise, Program
from .db import Item, SetRow
from .service import Summary

CAPTION_LIMIT = 1024


def num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def weight_text(weight: float | None, ex: Exercise) -> str:
    if weight is None:
        return "вес не задан"
    if ex.bodyweight:
        return "свой вес" if weight == 0 else f"свой вес +{num(weight)} кг"
    if ex.equipment == "dumbbell":
        return f"{num(weight)} кг (гантель)"
    return f"{num(weight)} кг"


def reps_text(item: Item, set_idx: int | None = None) -> str:
    """'5', '5+' for an AMRAP set, '6–10' for a range."""
    if item.reps_lo != item.reps_hi:
        return f"{item.reps_lo}–{item.reps_hi}"
    if item.amrap and (set_idx is None or set_idx == item.sets - 1):
        return f"{item.reps_lo}+"
    return str(item.reps_lo)


def scheme_text(item: Item) -> str:
    if item.reps_lo != item.reps_hi:
        return f"{item.sets}×{item.reps_lo}–{item.reps_hi}"
    if item.amrap:
        return f"{item.sets}×{item.reps_lo}, последний на максимум"
    return f"{item.sets}×{item.reps_lo}"


def sets_line(sets: list[SetRow], ex: Exercise) -> str:
    if not sets:
        return ""
    weights = {s.weight for s in sets}
    reps = ", ".join(str(s.reps) for s in sets)
    if len(weights) == 1:
        return f"{weight_text(sets[0].weight, ex)} × {reps}"
    return ", ".join(f"{num(s.weight)}×{s.reps}" for s in sets)


def exercise_card(item: Item, position: int, total: int, last: list[SetRow]) -> str:
    ex = EXERCISES[item.ex_key]
    head = [
        f"<b>{position}/{total} · {escape(ex.name)}</b>",
        f"🎯 {escape(ex.muscles)}",
        f"📋 {scheme_text(item)} · {weight_text(item.weight, ex)}",
    ]
    if last:
        head.append(f"🕘 В прошлый раз: {sets_line(last, ex)}")
    cues = list(ex.cues)
    mistakes = list(ex.mistakes)
    while True:
        body = "✅ <b>На что обратить внимание</b>\n" + "\n".join(f"• {escape(c)}" for c in cues)
        if mistakes:
            body += "\n\n⚠️ <b>Частые ошибки</b>\n" + "\n".join(f"• {escape(m)}" for m in mistakes)
        text = "\n".join(head) + f"\n\n<blockquote expandable>{body}</blockquote>"
        # captions are limited to 1024 chars; drop tips from the end until it fits
        if len(_plain(text)) <= CAPTION_LIMIT or (not mistakes and len(cues) <= 1):
            return text
        if mistakes:
            mistakes.pop()
        else:
            cues.pop()


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def set_prompt(item: Item, set_idx: int) -> str:
    ex = EXERCISES[item.ex_key]
    target = reps_text(item, set_idx)
    text = f"<b>{escape(ex.name)}</b>\nПодход {set_idx + 1}/{item.sets} · {weight_text(item.weight, ex)} × {target}"
    if item.amrap and set_idx == item.sets - 1:
        text += "\n🔥 Последний подход — сделай сколько сможешь с хорошей техникой (оставь 1–2 повтора в запасе)"
    text += "\n\nНажми число повторов или напиши его сообщением."
    return text


def set_done(item: Item, set_idx: int, reps: int) -> str:
    ex = EXERCISES[item.ex_key]
    return f"✔️ {escape(ex.name)} · подход {set_idx + 1}/{item.sets}: {weight_text(item.weight, ex)} × {reps}"


VERDICTS = {
    "up": "🔼 Отлично! В следующий раз: {w}",
    "up2": "⏫ Мощно, большой шаг! В следующий раз: {w}",
    "hold": "➡️ Держим вес и добираем повторы: {w}",
    "fail": "➡️ Не дотянул до нижней границы — повторим тот же вес: {w}",
    "deload": "🔽 Сбрасываем вес на ~10%, чтобы снова разогнаться: {w}",
    "top": "🔝 Верх диапазона во всех подходах! Усложняй: медленнее опускание, пауза или вариант потяжелее",
}


def exercise_result(item: Item, sets: list[SetRow], outcome: progression.Outcome) -> str:
    ex = EXERCISES[item.ex_key]
    return (
        f"<b>{escape(ex.name)}</b>: {sets_line(sets, ex)}\n"
        + VERDICTS[outcome.verdict].format(w=weight_text(outcome.weight, ex))
    )


def rest_text(seconds_left: int, total: int) -> str:
    m, s = divmod(max(seconds_left, 0), 60)
    filled = round(10 * (1 - seconds_left / total)) if total else 10
    bar = "▰" * filled + "▱" * (10 - filled)
    return f"⏱ Отдых {m}:{s:02d}\n{bar}"


def day_preview(program: Program, day_idx: int, lifts: dict[str, float], with_program: bool = True,
                suggest=None) -> str:
    """suggest(ex) -> starting weight, shown for exercises without a working weight yet."""
    day = program.days[day_idx]
    header = f"<b>{escape(day.name)}</b>"
    lines = [f"{header} · {escape(program.name)}", ""] if with_program else [header]
    for i, it in enumerate(day.items, 1):
        ex = EXERCISES[it.ex]
        scheme = (
            f"{it.sets}×{it.reps_lo}–{it.reps_hi}" if it.reps_lo != it.reps_hi
            else f"{it.sets}×{it.reps_lo}{'+' if it.amrap else ''}"
        )
        weight = 0.0 if ex.reps_only else lifts.get(it.ex)
        if weight is None and suggest is not None and (s := suggest(ex)) is not None:
            shown = f"старт ~{weight_text(s, ex)}"
        else:
            shown = weight_text(weight, ex)
        lines.append(f"{i}. {escape(ex.name)} — {scheme} · {shown}")
    return "\n".join(lines)


def duration_text(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    return f"{h} ч {m} мин" if h else f"{m} мин"


def summary_text(s: Summary) -> str:
    if s.status == "cancelled":
        return "Тренировка отменена — ни одного подхода не записано."
    lines = [f"🏁 <b>Тренировка завершена</b> · {duration_text(s.duration)}"]
    if s.tonnage:
        lines.append(f"Тоннаж: {thousands(round(s.tonnage))} кг")
    lines.append("")
    records = []
    for es in s.exercises:
        ex = EXERCISES[es.item.ex_key]
        line = f"• {escape(ex.name)}: {sets_line(es.sets, ex)}"
        if es.item.next_weight is not None:
            arrow = {"up": "🔼", "up2": "⏫", "deload": "🔽", "top": "🔝"}.get(es.item.verdict, "➡️")
            line += f"\n   {arrow} дальше: {weight_text(es.item.next_weight, ex)}"
        else:
            line += "\n   ⏸ не доделано — вес не меняем"
        lines.append(line)
        if es.record:
            records.append(f"🏆 {escape(ex.name)}: новый рекорд, расчётный максимум ≈ {num(round(es.record, 1))} кг")
    if records:
        lines += [""] + records
    return "\n".join(lines)


def fmt_date(ts: int, tz: str) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz)).strftime("%d.%m")


def history_text(ex: Exercise, sessions: list[tuple[int, list[SetRow]]], current: float | None, tz: str) -> str:
    lines = [f"<b>{escape(ex.name)}</b>", f"Рабочий вес: {weight_text(current, ex)}", ""]
    if not sessions:
        lines.append("Пока нет записей.")
    best = 0.0
    for started, sets in sessions:
        line = f"{fmt_date(started, tz)} — {sets_line(sets, ex)}"
        if not ex.bodyweight:
            top = max(progression.e1rm(s.weight, s.reps) for s in sets)
            best = max(best, top)
            line += f"  (1ПМ≈{num(round(top))})"
        lines.append(line)
    if best:
        lines += ["", f"Лучший расчётный максимум за период: ≈ {num(round(best, 1))} кг"]
    return "\n".join(lines)


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def nutrition_text(n, advice: str) -> str:
    return (
        "🍽 <b>Питание (ориентир)</b>\n"
        f"Калории: <b>{thousands(n.calories)} ккал</b> в день\n"
        f"Белок: <b>{n.protein} г</b> · жиры: <b>{n.fat} г</b> · углеводы: <b>{n.carbs} г</b>\n"
        f"<i>Поддержание веса ≈ {thousands(n.maintenance)} ккал, ИМТ {num(n.bmi)}</i>\n\n"
        f"{escape(advice)}\n\n"
        "<i>Расчёт по формуле Миффлина–Сан Жеора. Это ориентир, а не медицинская рекомендация.</i>"
    )


def bodyweight_text(entries: list[tuple[int, float]], tz: str) -> str:
    if not entries:
        return "⚖️ Вес тела пока не записан."
    latest = entries[0][1]
    lines = [f"⚖️ Вес тела: <b>{num(latest)} кг</b>"]
    if len(entries) > 1:
        first_ts, first = entries[-1]
        delta = latest - first
        sign = "+" if delta > 0 else ""
        lines.append(f"Изменение с {fmt_date(first_ts, tz)}: {sign}{num(round(delta, 1))} кг")
    lines.append(" · ".join(f"{fmt_date(ts, tz)} {num(w)}" for ts, w in entries[:6]))
    return "\n".join(lines)
