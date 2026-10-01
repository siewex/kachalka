"""Exercise and program catalog loaded from bot/data/*.json."""

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Exercise:
    key: str
    name: str
    edb_id: str
    muscles: str
    equipment: str  # barbell | dumbbell | cable | machine | bodyweight
    increment: float  # normal weight jump after a successful session
    step: float  # smallest weight change available in the gym
    bodyweight: bool
    cues: tuple[str, ...]
    mistakes: tuple[str, ...]
    alternatives: tuple[str, ...]


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


def _load_exercises() -> dict[str, Exercise]:
    raw = json.loads((DATA_DIR / "exercises.json").read_text(encoding="utf-8"))
    result = {}
    for e in raw:
        result[e["key"]] = Exercise(
            key=e["key"],
            name=e["name"],
            edb_id=e["edb_id"],
            muscles=e["muscles"],
            equipment=e["equipment"],
            increment=float(e["increment"]),
            step=float(e["step"]),
            bodyweight=bool(e.get("bodyweight", False)),
            cues=tuple(e["cues"]),
            mistakes=tuple(e["mistakes"]),
            alternatives=tuple(e.get("alternatives", [])),
        )
    return result


def _load_programs(exercises: dict[str, Exercise]) -> dict[str, Program]:
    raw = json.loads((DATA_DIR / "programs.json").read_text(encoding="utf-8"))
    result = {}
    for p in raw:
        days = []
        for d in p["days"]:
            items = []
            for it in d["items"]:
                if it["ex"] not in exercises:
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
        result[p["key"]] = Program(
            key=p["key"], name=p["name"], description=p["description"], days=tuple(days)
        )
    return result


EXERCISES = _load_exercises()
PROGRAMS = _load_programs(EXERCISES)

for _ex in EXERCISES.values():
    for _alt in _ex.alternatives:
        if _alt not in EXERCISES:
            raise ValueError(f"exercise {_ex.key}: unknown alternative {_alt}")
