"""Exercise and program catalog, built from bot/content/*.py."""

from dataclasses import dataclass

from .content.exercises import EXERCISES_DATA
from .content.programs import PROGRAMS_DATA


@dataclass(frozen=True)
class Exercise:
    key: str
    name: str
    edb_id: str
    muscles: str
    equipment: str  # barbell | dumbbell | cable | machine | bodyweight
    slots: tuple[str, ...]
    increment: float
    step: float
    cues: tuple[str, ...]
    mistakes: tuple[str, ...]
    restrict: frozenset[str]
    level: int
    kind: str  # compound | iso
    start: tuple[float, float]  # (men, women) fraction of body weight for a beginner
    heavy: bool
    bodyweight: bool  # weight = extra load on top of body weight
    reps_only: bool  # no weight at all, progress by reps
    min_weight: float

    @property
    def alternatives(self) -> tuple[str, ...]:
        return ALTERNATIVES.get(self.key, ())


@dataclass(frozen=True)
class PlanItem:
    ex: str
    sets: int
    reps_lo: int
    reps_hi: int
    amrap: bool  # last set is "as many reps as possible"
    rule: str  # linear | double
    rest: int


@dataclass(frozen=True)
class Day:
    key: str
    name: str
    items: tuple[PlanItem, ...]


@dataclass(frozen=True)
class Program:
    key: str
    name: str
    description: str
    days: tuple[Day, ...]


def _exercise(e: dict) -> Exercise:
    min_weight = e.get("min_weight")
    if min_weight is None:
        min_weight = 20.0 if e["equipment"] == "barbell" else 0.0
    return Exercise(
        key=e["key"],
        name=e["name"],
        edb_id=e["edb_id"],
        muscles=e["muscles"],
        equipment=e["equipment"],
        slots=tuple(e.get("slots", ())),
        increment=float(e["increment"]),
        step=float(e["step"]),
        cues=tuple(e["cues"]),
        mistakes=tuple(e["mistakes"]),
        restrict=frozenset(e.get("restrict", ())),
        level=e.get("level", 0),
        kind=e.get("kind", "iso"),
        start=tuple(e.get("start", (0, 0))),
        heavy=e.get("heavy", False),
        bodyweight=bool(e.get("bodyweight", False)),
        reps_only=bool(e.get("reps_only", False)),
        min_weight=float(min_weight),
    )


def build_program(p: dict) -> Program:
    """Turn a program dict (built-in or generated) into a Program, validating exercise keys."""
    days = []
    for d in p["days"]:
        items = []
        for it in d["items"]:
            if it["ex"] not in EXERCISES:
                raise ValueError(f"program {p['key']}: unknown exercise {it['ex']}")
            reps = it["reps"]
            lo, hi = (reps, reps) if isinstance(reps, int) else reps
            items.append(
                PlanItem(
                    ex=it["ex"],
                    sets=it["sets"],
                    reps_lo=lo,
                    reps_hi=hi,
                    amrap=it.get("amrap", False),
                    rule=it["rule"],
                    rest=it.get("rest", p["rest"]),
                )
            )
        days.append(Day(key=d["key"], name=d["name"], items=tuple(items)))
    return Program(key=p["key"], name=p["name"], description=p["description"], days=tuple(days))


EXERCISES = {e["key"]: _exercise(e) for e in EXERCISES_DATA}

# exercises sharing the main movement pattern can replace each other mid-workout
ALTERNATIVES = {
    ex.key: tuple(o.key for o in EXERCISES.values() if o.key != ex.key and o.slots[:1] == ex.slots[:1])
    for ex in EXERCISES.values()
}

PROGRAMS = {p["key"]: build_program(p) for p in PROGRAMS_DATA}
