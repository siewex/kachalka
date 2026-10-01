"""Calories and macros from the questionnaire (Mifflin–St Jeor). A guideline, not medical advice."""

from dataclasses import dataclass

from .planner import ACTIVITY, Profile

GOAL_FACTOR = {"mass": 1.10, "cut": 0.85, "strength": 1.05}
PROTEIN_PER_KG = {"mass": 1.8, "cut": 2.0, "strength": 1.8}
FAT_PER_KG = {"mass": 0.9, "cut": 0.8, "strength": 0.9}


@dataclass(frozen=True)
class Nutrition:
    bmr: int
    maintenance: int
    calories: int
    protein: int
    fat: int
    carbs: int
    bmi: float


def calculate(p: Profile, weight: float | None = None) -> Nutrition:
    w = weight or p.weight
    bmr = 10 * w + 6.25 * p.height - 5 * p.age + (5 if p.sex == "m" else -161)
    maintenance = bmr * ACTIVITY[p.activity][1]
    calories = maintenance * GOAL_FACTOR[p.goal]
    if p.goal == "cut":
        calories = max(calories, bmr * 1.1)  # don't go too low
    bmi = w / (p.height / 100) ** 2
    # with a high BMI count protein and fat from a reference weight at BMI 25
    ref = min(w, 25 * (p.height / 100) ** 2) if bmi >= 30 else w
    protein = PROTEIN_PER_KG[p.goal] * ref
    fat = FAT_PER_KG[p.goal] * ref
    carbs = max(0.0, (calories - protein * 4 - fat * 9) / 4)
    return Nutrition(
        bmr=round(bmr), maintenance=round(maintenance), calories=round(calories / 10) * 10,
        protein=round(protein), fat=round(fat), carbs=round(carbs), bmi=round(bmi, 1),
    )


def advice(p: Profile) -> str:
    if p.goal == "mass":
        return ("Вес тела должен расти примерно на 0,25–0,5% в неделю. Если две недели стоит на месте — "
                "добавь ~150 ккал. Растёт быстрее 0,5 кг/нед — немного убавь.")
    if p.goal == "cut":
        return ("Цель — минус 0,5–1% веса в неделю. Если две недели стоит — убавь ~150 ккал или добавь шагов. "
                "Белок держи высоким — он сохраняет мышцы.")
    return "Держи вес тела примерно стабильным или с лёгким плюсом — так силовые растут быстрее всего."
