"""Deterministic progression rules: what weight to use next time.

linear  — Basic Beginner Routine: hit every target rep -> +increment
          (doubled if the AMRAP set goes over 10 reps); miss any -> -10%.
double  — double progression in a rep range: all sets at the top of the
          range -> +increment; below the bottom of the range twice in a row -> -10%.
"""

import math
from dataclasses import dataclass

AMRAP_BONUS_REPS = 10
DOUBLE_FAILS_TO_DELOAD = 2
DELOAD_FACTOR = 0.9


@dataclass(frozen=True)
class Outcome:
    weight: float
    fails: int
    verdict: str  # up | up2 | hold | fail | deload | top


def round_to_step(weight: float, step: float) -> float:
    return round(round(weight / step) * step, 2)


def deload(weight: float, step: float) -> float:
    if weight <= 0:
        return 0.0
    new = math.floor(weight * DELOAD_FACTOR / step + 1e-9) * step
    if new >= weight:
        new = weight - step
    return round(max(new, 0.0), 2)


def next_weight(
    *,
    rule: str,
    weight: float,
    reps: list[int],
    reps_lo: int,
    reps_hi: int,
    amrap: bool,
    increment: float,
    step: float,
    fails: int,
) -> Outcome:
    if not reps:
        return Outcome(weight, fails, "hold")

    if rule == "linear":
        if all(r >= reps_lo for r in reps):
            bonus = amrap and reps[-1] > AMRAP_BONUS_REPS
            inc = increment * 2 if bonus else increment
            return Outcome(round_to_step(weight + inc, step), 0, "up2" if bonus else "up")
        return Outcome(deload(weight, step), 0, "deload")

    if rule == "double":
        if all(r >= reps_hi for r in reps):
            return Outcome(round_to_step(weight + increment, step), 0, "up")
        if any(r < reps_lo for r in reps):
            fails += 1
            if fails >= DOUBLE_FAILS_TO_DELOAD:
                return Outcome(deload(weight, step), 0, "deload")
            return Outcome(weight, fails, "fail")
        return Outcome(weight, 0, "hold")

    raise ValueError(f"unknown rule {rule}")


def reps_only(*, reps: list[int], reps_lo: int, reps_hi: int) -> Outcome:
    """Body-weight exercises without load: progress is more reps, then a harder variation."""
    if reps and all(r >= reps_hi for r in reps):
        return Outcome(0.0, 0, "top")
    if any(r < reps_lo for r in reps):
        return Outcome(0.0, 0, "fail")
    return Outcome(0.0, 0, "hold")


def scaled_increment(increment: float, step: float, scale: float) -> float:
    """Smaller jumps for a 'fine' weight-step setting, but never below the gym step."""
    return max(step, round_to_step(increment * scale, step))


def e1rm(weight: float, reps: int) -> float:
    """Epley estimate of the one-rep max."""
    if reps <= 0 or weight <= 0:
        return 0.0
    if reps == 1:
        return weight
    return weight * (1 + reps / 30)
