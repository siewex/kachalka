"""Builds a personal program from questionnaire answers. Rule-based, no AI.

Day templates list movement-pattern slots with a priority (1 essential, 2 normal, 3 extra).
Training time decides how many slots make it into a day; the rest is filled from the
exercise catalog, respecting experience and joint problems, without repeating an
exercise within the week when an alternative exists.
"""

import math
from dataclasses import asdict, dataclass, field

from .catalog import EXERCISES, Exercise

GOALS = {"mass": "Набор массы", "cut": "Рельеф / сушка", "strength": "Сила"}
FOCUSES = {"balanced": "Сбалансированно", "glutes": "Ягодицы и ноги", "upper": "Верх тела и руки"}
LEVELS = {0: "Новичок (до 6 мес.)", 1: "Средний (6 мес. – 2 года)", 2: "Опытный (2+ года)"}
RESTRICTIONS = {"knees": "Колени", "back": "Спина / поясница", "shoulders": "Плечи"}
ACTIVITY = {
    1: ("Сидячая работа, мало хожу", 1.2),
    2: ("Немного движения, 5–8 тыс. шагов", 1.375),
    3: ("Активно, 8–12 тыс. шагов", 1.55),
    4: ("Очень активно / физический труд", 1.725),
}
DAYS_OPTIONS = (2, 3, 4, 5)
MINUTES_OPTIONS = (45, 60, 75, 90)


@dataclass
class Profile:
    sex: str  # m | f
    age: int
    height: int  # cm
    weight: float  # kg
    activity: int
    goal: str
    focus: str
    level: int
    days: int
    minutes: int
    restrictions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# --- templates: (day name, [(slot, priority), ...]) ---

T = list[tuple[str, list[tuple[str, int]]]]

BALANCED: dict[int, T] = {
    2: [
        ("Всё тело A", [("SQUAT", 1), ("HPUSH", 1), ("HPULL", 1), ("HAM_ISO", 2), ("LAT_DELT", 2),
                        ("BICEPS", 3), ("CORE", 3), ("CALVES", 3)]),
        ("Всё тело B", [("HINGE", 1), ("INCLINE", 1), ("VPULL", 1), ("LUNGE", 2), ("REAR_DELT", 2),
                        ("TRICEPS", 3), ("CORE", 3), ("LAT_DELT", 3)]),
    ],
    3: [
        ("Всё тело A", [("SQUAT", 1), ("HPUSH", 1), ("VPULL", 1), ("HAM_ISO", 2), ("LAT_DELT", 2),
                        ("TRICEPS", 3), ("CORE", 3)]),
        ("Всё тело B", [("HINGE", 1), ("VPUSH", 1), ("HPULL", 1), ("LUNGE", 2), ("CHEST_ISO", 2),
                        ("BICEPS", 3), ("CALVES", 3)]),
        ("Всё тело C", [("SQUAT", 1), ("INCLINE", 1), ("VPULL", 1), ("THRUST", 2), ("REAR_DELT", 2),
                        ("BICEPS", 3), ("TRICEPS", 3)]),
    ],
    4: [
        ("Верх A", [("HPUSH", 1), ("HPULL", 1), ("VPUSH", 1), ("VPULL", 2), ("LAT_DELT", 2),
                    ("TRICEPS", 3), ("BICEPS", 3)]),
        ("Низ A", [("SQUAT", 1), ("HINGE", 1), ("LUNGE", 2), ("HAM_ISO", 2), ("CALVES", 3), ("CORE", 3)]),
        ("Верх B", [("INCLINE", 1), ("VPULL", 1), ("HPULL", 1), ("CHEST_ISO", 2), ("LAT_DELT", 2),
                    ("BICEPS", 3), ("TRICEPS", 3)]),
        ("Низ B", [("HINGE", 1), ("SQUAT", 1), ("THRUST", 2), ("QUAD_ISO", 2), ("HAM_ISO", 3),
                   ("CALVES", 3), ("CORE", 3)]),
    ],
    5: [
        ("Верх", [("HPUSH", 1), ("HPULL", 1), ("VPUSH", 2), ("VPULL", 2), ("LAT_DELT", 2),
                  ("BICEPS", 3), ("TRICEPS", 3)]),
        ("Низ", [("SQUAT", 1), ("HINGE", 1), ("LUNGE", 2), ("HAM_ISO", 2), ("CALVES", 3), ("CORE", 3)]),
        ("Жимы", [("INCLINE", 1), ("VPUSH", 1), ("CHEST_ISO", 2), ("LAT_DELT", 2), ("TRICEPS", 2),
                  ("TRICEPS", 3)]),
        ("Тяги", [("VPULL", 1), ("HPULL", 1), ("HPULL", 2), ("REAR_DELT", 2), ("BICEPS", 2), ("BICEPS", 3)]),
        ("Ноги", [("SQUAT", 1), ("HINGE", 1), ("QUAD_ISO", 2), ("HAM_ISO", 2), ("THRUST", 3), ("CALVES", 3)]),
    ],
}

GLUTES: dict[int, T] = {
    2: [
        ("Всё тело A", [("THRUST", 1), ("SQUAT", 1), ("VPULL", 1), ("HPUSH", 2), ("ABDUCT", 2),
                        ("LAT_DELT", 3), ("CORE", 3)]),
        ("Всё тело B", [("HINGE", 1), ("LUNGE", 1), ("HPULL", 1), ("GLUTE_ISO", 2), ("VPUSH", 2),
                        ("HAM_ISO", 3), ("CORE", 3)]),
    ],
    3: [
        ("Ягодицы A", [("THRUST", 1), ("HINGE", 1), ("LUNGE", 2), ("ABDUCT", 2), ("GLUTE_ISO", 3), ("CORE", 3)]),
        ("Верх", [("VPULL", 1), ("HPUSH", 1), ("HPULL", 1), ("VPUSH", 2), ("LAT_DELT", 2),
                  ("REAR_DELT", 3), ("TRICEPS", 3)]),
        ("Ноги и ягодицы B", [("SQUAT", 1), ("THRUST", 1), ("HAM_ISO", 2), ("LUNGE", 2), ("ABDUCT", 2),
                              ("CALVES", 3)]),
    ],
    4: [
        ("Ягодицы A", [("THRUST", 1), ("HINGE", 1), ("LUNGE", 2), ("ABDUCT", 2), ("GLUTE_ISO", 2), ("CORE", 3)]),
        ("Верх A", [("VPULL", 1), ("HPUSH", 1), ("HPULL", 1), ("LAT_DELT", 2), ("TRICEPS", 3), ("BICEPS", 3)]),
        ("Ноги и ягодицы B", [("SQUAT", 1), ("HINGE", 1), ("THRUST", 2), ("HAM_ISO", 2), ("ABDUCT", 3),
                              ("CALVES", 3)]),
        ("Верх B + ягодицы", [("HPULL", 1), ("VPUSH", 1), ("VPULL", 2), ("LAT_DELT", 2), ("GLUTE_ISO", 2),
                              ("REAR_DELT", 3), ("CORE", 3)]),
    ],
    5: [
        ("Ягодицы A", [("THRUST", 1), ("HINGE", 1), ("LUNGE", 2), ("ABDUCT", 2), ("GLUTE_ISO", 3)]),
        ("Верх A", [("VPULL", 1), ("HPUSH", 1), ("HPULL", 1), ("LAT_DELT", 2), ("TRICEPS", 3), ("BICEPS", 3)]),
        ("Ноги", [("SQUAT", 1), ("HAM_ISO", 1), ("QUAD_ISO", 2), ("LUNGE", 2), ("CALVES", 3), ("CORE", 3)]),
        ("Верх B", [("HPULL", 1), ("VPUSH", 1), ("VPULL", 2), ("REAR_DELT", 2), ("LAT_DELT", 2), ("CORE", 3)]),
        ("Ягодицы B", [("THRUST", 1), ("HINGE", 1), ("GLUTE_ISO", 2), ("ABDUCT", 2), ("HAM_ISO", 3)]),
    ],
}

UPPER_SLOTS = {"HPUSH", "INCLINE", "VPUSH", "VPULL", "HPULL"}
ARM_SLOTS = ("LAT_DELT", "BICEPS", "TRICEPS")

# preferred exercises per slot, best first
SLOT_PREFS: dict[str, list[str]] = {
    "SQUAT": ["squat", "leg_press", "hack_squat", "goblet_squat"],
    "LUNGE": ["bulgarian_split_squat", "rear_lunge", "step_up"],
    "QUAD_ISO": ["leg_extension"],
    "HINGE": ["rdl", "deadlift", "db_rdl", "cable_pull_through", "hyperextension"],
    "THRUST": ["glute_bridge"],
    "HAM_ISO": ["lying_leg_curl", "seated_leg_curl"],
    "ABDUCT": ["hip_abduction"],
    "GLUTE_ISO": ["cable_kickback", "glute_machine", "cable_pull_through", "hyperextension"],
    "CALVES": ["standing_calf", "seated_calf"],
    "HPUSH": ["bench_press", "db_bench_press", "machine_chest_press", "dips", "push_up"],
    "INCLINE": ["db_incline_press", "incline_bench"],
    "VPUSH": ["db_shoulder_press", "ohp", "machine_shoulder_press"],
    "CHEST_ISO": ["pec_deck", "db_fly"],
    "TRICEPS": ["triceps_pushdown", "overhead_triceps", "rope_pushdown", "skull_crusher"],
    "VPULL": ["lat_pulldown", "pull_up", "close_grip_pulldown", "chin_up"],
    "HPULL": ["cable_row", "db_row", "machine_row", "barbell_row", "tbar_row"],
    "REAR_DELT": ["face_pull", "reverse_pec_deck", "db_reverse_fly"],
    "LAT_DELT": ["lateral_raise", "cable_lateral_raise"],
    "BICEPS": ["db_curl", "hammer_curl", "ez_curl", "cable_curl"],
    "CORE": ["cable_crunch", "reverse_crunch", "hanging_leg_raise"],
}

# exercises per day by training time (cut has shorter rests, strength longer)
PER_DAY = {45: 4, 60: 5, 75: 6, 90: 7}


@dataclass(frozen=True)
class Scheme:
    sets: int
    reps: tuple[int, int]
    rest: int


def scheme(ex: Exercise, goal: str, priority: int, level: int) -> Scheme:
    compound = ex.kind == "compound"
    if ex.reps_only:
        reps = (8, 15) if goal == "mass" else (10, 20)
        return Scheme(3 if priority < 3 else 2, reps, 60)
    if goal == "strength":
        if compound and priority == 1:
            reps, rest, sets = (4, 6), 180, 4
        elif compound:
            reps, rest, sets = (6, 10), 150, 3
        else:
            reps, rest, sets = (8, 12), 90, 3
    elif goal == "cut":
        if compound:
            reps, rest = ((10, 15) if "THRUST" in ex.slots else (8, 12)), 90
        else:
            reps, rest = (12, 20), 60
        sets = 3
    else:  # mass
        if compound:
            reps = (5, 8) if ex.heavy else (8, 12) if {"THRUST", "LUNGE"} & set(ex.slots) else (6, 10)
            rest = 150
            sets = 4 if priority == 1 and level >= 2 else 3
        else:
            reps, rest, sets = (10, 15), 75, 3
    if ex.heavy:
        rest += 30
    if priority == 3 and level == 0:
        sets = 2
    return Scheme(sets, reps, rest)


def allowed(ex: Exercise, p: Profile) -> bool:
    return ex.level <= p.level and not (ex.restrict & set(p.restrictions))


def pick(slot: str, p: Profile, used: set[str], today: set[str]) -> Exercise | None:
    options = [EXERCISES[k] for k in SLOT_PREFS[slot] if allowed(EXERCISES[k], p)]
    options = [ex for ex in options if ex.key not in today]
    if not options:
        return None
    fresh = [ex for ex in options if ex.key not in used]
    return (fresh or options)[0]


def _templates(p: Profile) -> T:
    base = GLUTES if p.focus == "glutes" else BALANCED
    days = base[p.days]
    if p.focus != "upper":
        return days
    result = []
    for name, slots in days:
        slots = list(slots)
        if any(s in UPPER_SLOTS for s, _ in slots):
            slots = [(s, 2 if s in ARM_SLOTS else pr) for s, pr in slots]
            for arm in ARM_SLOTS:
                if not any(s == arm for s, _ in slots):
                    slots.append((arm, 2))
        result.append((name, slots))
    return result


def per_day(p: Profile) -> int:
    n = PER_DAY[p.minutes]
    if p.goal == "cut":
        n += 1
    elif p.goal == "strength":
        n -= 1
    return n


def generate(p: Profile) -> dict:
    """Return a program dict in the same shape as content/programs.py entries."""
    used: set[str] = set()
    days = []
    limit = per_day(p)
    for idx, (name, slots) in enumerate(_templates(p)):
        # keep the most important slots, then restore template order
        ranked = sorted(range(len(slots)), key=lambda i: (slots[i][1], i))
        chosen: list[tuple[int, Exercise, int]] = []
        today: set[str] = set()
        for i in ranked:
            if len(chosen) >= limit:
                break
            slot, priority = slots[i]
            ex = pick(slot, p, used, today)
            if ex is None:
                continue
            today.add(ex.key)
            chosen.append((i, ex, priority))
        used |= today
        items = []
        # compounds first (in template order), isolation work after
        for _, ex, priority in sorted(chosen, key=lambda c: (c[1].kind != "compound", c[0])):
            s = scheme(ex, p.goal, priority, p.level)
            items.append({"ex": ex.key, "sets": s.sets, "reps": list(s.reps), "rule": "double", "rest": s.rest})
        days.append({"key": chr(ord("A") + idx), "name": name, "items": items})

    return {
        "key": "custom",
        "name": f"{GOALS[p.goal]} · {FOCUSES[p.focus].lower()} · {p.days} дн/нед",
        "description": describe(p),
        "rest": 90,
        "days": days,
    }


def describe(p: Profile) -> str:
    parts = [
        "Программа собрана по твоей анкете. Работаем в диапазонах повторений: когда во всех подходах "
        "сделан верх диапазона — вес растёт, если дважды не дотягиваешь до низа — сбрасываем ~10%.",
    ]
    if p.goal == "mass":
        parts.append("Для массы главное — прогресс в весах и профицит калорий. Последние 1–2 повтора "
                     "в подходе должны даваться тяжело.")
    elif p.goal == "cut":
        parts.append("Для рельефа веса держим, а жир убираем дефицитом калорий и шагами: 8–10 тыс. в день. "
                     "По желанию — 15–20 минут кардио после тренировки.")
    else:
        parts.append("Для силы — длинный отдых и тяжёлые базовые упражнения в начале тренировки.")
    if p.restrictions:
        names = ", ".join(RESTRICTIONS[r].lower() for r in p.restrictions)
        parts.append(f"С учётом ограничений ({names}) нагружающие их упражнения исключены. "
                     "Если что-то всё равно болит — остановись и посоветуйся с врачом или тренером.")
    return "\n\n".join(parts)


LEVEL_MULT = {0: 1.0, 1: 1.35, 2: 1.7}


def start_weight(ex: Exercise, p: Profile) -> float | None:
    """Conservative starting weight suggestion. None when there is nothing to suggest."""
    if ex.reps_only:
        return None
    if ex.bodyweight:
        return 0.0
    ratio = ex.start[0] if p.sex == "m" else ex.start[1]
    if not ratio:
        return None
    raw = ratio * p.weight * LEVEL_MULT[p.level]
    if p.goal == "cut":
        raw *= 0.9
    w = math.floor(raw / ex.step) * ex.step
    return round(max(w, ex.min_weight, ex.step), 2)
