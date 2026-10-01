"""Workout flow, independent of Telegram so it can be tested directly."""

from dataclasses import dataclass, field

from . import progression
from .catalog import EXERCISES, PROGRAMS, Day
from .db import Database, Item, SetRow, User, Workout, now


class WorkoutError(Exception):
    pass


def day_for(user: User, day_idx: int | None = None) -> tuple[int, Day]:
    program = PROGRAMS[user.program]
    idx = (user.day_idx if day_idx is None else day_idx) % len(program.days)
    return idx, program.days[idx]


async def start_workout(db: Database, user: User, day_idx: int | None = None) -> Workout:
    if await db.active_workout(user.user_id):
        raise WorkoutError("Уже есть активная тренировка")
    idx, day = day_for(user, day_idx)
    items = []
    for it in day.items:
        lift = await db.get_lift(user.user_id, it.ex)
        items.append(
            dict(
                ex_key=it.ex, sets=it.sets, reps_lo=it.reps_lo, reps_hi=it.reps_hi, amrap=it.amrap,
                rule=it.rule, rest=user.rest_override or it.rest, weight=lift[0] if lift else None,
            )
        )
    wid = await db.create_workout(user.user_id, user.program, day.key, idx, items)
    return await db.get_workout(wid)


async def current_item(db: Database, w: Workout) -> Item | None:
    items = await db.items(w.id)
    return items[w.ex_idx] if w.ex_idx < len(items) else None


async def set_weight(db: Database, w: Workout, weight: float) -> None:
    item = await current_item(db, w)
    if item is None:
        raise WorkoutError("Нет текущего упражнения")
    await db.update_item(w.id, item.idx, weight=weight)
    await db.set_lift(w.user_id, item.ex_key, weight)


@dataclass
class SetResult:
    kind: str  # next_set | exercise_done | workout_done
    item: Item
    sets: list[SetRow] = field(default_factory=list)
    outcome: progression.Outcome | None = None


async def log_set(db: Database, w: Workout, reps: int, expect: tuple[int, int] | None = None) -> SetResult:
    """Record reps for the current set and advance. `expect` guards against double taps."""
    if w.status != "active":
        raise WorkoutError("Тренировка уже завершена")
    if expect is not None and expect != (w.ex_idx, w.set_idx):
        raise WorkoutError("Этот подход уже записан")
    item = await current_item(db, w)
    if item is None:
        raise WorkoutError("Все упражнения уже сделаны")
    if item.weight is None:
        raise WorkoutError("Сначала укажи рабочий вес")

    await db.add_set(w.id, item.idx, item.ex_key, w.set_idx + 1, item.weight, reps)

    if w.set_idx + 1 < item.sets:
        await db.update_workout(w.id, set_idx=w.set_idx + 1)
        return SetResult("next_set", item)

    sets = await db.sets_for(w.id, item.idx)
    outcome = await _apply_progression(db, w.user_id, item, sets)
    item.next_weight, item.verdict = outcome.weight, outcome.verdict

    total = len(await db.items(w.id))
    await db.update_workout(w.id, ex_idx=w.ex_idx + 1, set_idx=0)
    kind = "workout_done" if w.ex_idx + 1 >= total else "exercise_done"
    return SetResult(kind, item, sets, outcome)


async def _apply_progression(db: Database, user_id: int, item: Item, sets: list[SetRow]) -> progression.Outcome:
    ex = EXERCISES[item.ex_key]
    user = await db.get_user(user_id)
    lift = await db.get_lift(user_id, item.ex_key)
    outcome = progression.next_weight(
        rule=item.rule,
        weight=item.weight,
        reps=[s.reps for s in sets],
        reps_lo=item.reps_lo,
        reps_hi=item.reps_hi,
        amrap=item.amrap,
        increment=progression.scaled_increment(ex.increment, ex.step, user.inc_scale),
        step=ex.step,
        fails=lift[1] if lift else 0,
    )
    await db.set_lift(user_id, item.ex_key, outcome.weight, outcome.fails)
    await db.update_item(item.workout_id, item.idx, next_weight=outcome.weight, verdict=outcome.verdict)
    return outcome


async def skip_exercise(db: Database, w: Workout) -> bool:
    """Move to the next exercise without progression. Returns True if the workout has no more exercises."""
    total = len(await db.items(w.id))
    await db.update_workout(w.id, ex_idx=w.ex_idx + 1, set_idx=0)
    return w.ex_idx + 1 >= total


async def swap_exercise(db: Database, w: Workout, new_key: str) -> Item:
    item = await current_item(db, w)
    if item is None:
        raise WorkoutError("Нет текущего упражнения")
    if new_key not in EXERCISES[item.ex_key].alternatives:
        raise WorkoutError("Эту замену нельзя сделать")
    lift = await db.get_lift(w.user_id, new_key)
    await db.delete_sets(w.id, item.idx)
    await db.update_item(w.id, item.idx, ex_key=new_key, weight=lift[0] if lift else None)
    await db.update_workout(w.id, set_idx=0)
    return (await db.items(w.id))[item.idx]


@dataclass
class ExerciseSummary:
    item: Item
    sets: list[SetRow]
    record: float | None  # new best estimated 1RM, if beaten


@dataclass
class Summary:
    status: str  # done | cancelled
    duration: int
    tonnage: float
    exercises: list[ExerciseSummary]


async def finish_workout(db: Database, w: Workout) -> Summary:
    all_sets = await db.sets_for(w.id)
    status = "done" if all_sets else "cancelled"
    finished = now()
    await db.update_workout(w.id, status=status, finished_at=finished)
    if status == "done":
        user = await db.get_user(w.user_id)
        # the next workout continues the A/B rotation after the day just done
        await db.update_user(w.user_id, day_idx=w.day_idx + 1 if user.program == w.program else user.day_idx)

    exercises = []
    for item in await db.items(w.id):
        sets = [s for s in all_sets if s.idx == item.idx]
        if not sets:
            continue
        record = None
        ex = EXERCISES[item.ex_key]
        if not ex.bodyweight:
            best_now = max(progression.e1rm(s.weight, s.reps) for s in sets)
            previous = await db.all_sets_for_exercise(w.user_id, item.ex_key, exclude_workout=w.id)
            best_before = max((progression.e1rm(s.weight, s.reps) for s in previous), default=0)
            if previous and best_now > best_before:
                record = best_now
        exercises.append(ExerciseSummary(item, sets, record))

    tonnage = sum(s.weight * s.reps for s in all_sets)
    return Summary(status, finished - w.started_at, tonnage, exercises)
