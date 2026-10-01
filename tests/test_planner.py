import asyncio
import itertools

import aiosqlite

from bot import nutrition, planner
from bot.catalog import EXERCISES, build_program
from bot.db import Database
from bot.planner import Profile, generate, start_weight


def profiles():
    restrictions = [[], ["knees"], ["back"], ["shoulders"], ["knees", "back", "shoulders"]]
    for sex, goal, focus, level, days, minutes, restr in itertools.product(
        "mf", planner.GOALS, planner.FOCUSES, planner.LEVELS, planner.DAYS_OPTIONS,
        planner.MINUTES_OPTIONS, restrictions,
    ):
        yield Profile(sex, 30, 170, 70, 2, goal, focus, level, days, minutes, restr)


def test_every_answer_combination_builds_a_sane_program():
    count = 0
    for p in profiles():
        plan = generate(p)
        program = build_program(plan)
        assert len(program.days) == p.days
        week = []
        for day in program.days:
            keys = [it.ex for it in day.items]
            assert 3 <= len(keys) <= planner.per_day(p), (p, day.name, keys)
            assert len(set(keys)) == len(keys), (p, day.name, keys)  # no repeats within a day
            for it in day.items:
                ex = EXERCISES[it.ex]
                assert not (ex.restrict & set(p.restrictions)), (p, ex.key)
                assert ex.level <= p.level, (p, ex.key)
                assert it.reps_lo < it.reps_hi and it.sets >= 2
            # big lifts first: the first exercise of a day is a compound
            assert EXERCISES[day.items[0].ex].kind == "compound", (p, day.name)
            week += keys
        if p.focus == "glutes":
            slots = {s for k in week for s in EXERCISES[k].slots}
            assert "THRUST" in slots, p
            if planner.per_day(p) >= 5:
                assert "ABDUCT" in slots or "GLUTE_ISO" in slots, p
        count += 1
    assert count > 1000


def test_glute_focus_trains_glutes_more_often():
    base = dict(sex="f", age=26, height=165, weight=58, activity=2, goal="cut", level=0, days=3, minutes=60)
    glutes = generate(Profile(**base, focus="glutes"))
    balanced = generate(Profile(**base, focus="balanced"))

    def glute_work(plan):
        return sum(1 for d in plan["days"] for it in d["items"]
                   if {"THRUST", "ABDUCT", "GLUTE_ISO", "LUNGE"} & set(EXERCISES[it["ex"]].slots))

    assert glute_work(glutes) > glute_work(balanced)


def test_goal_changes_reps_and_rest():
    base = dict(sex="m", age=28, height=180, weight=75, activity=2, focus="balanced", level=1, days=4, minutes=75)
    mass = generate(Profile(**base, goal="mass"))
    cut = generate(Profile(**base, goal="cut"))
    first = lambda plan: plan["days"][0]["items"][0]  # noqa: E731
    assert first(mass)["reps"] == [6, 10] and first(mass)["rest"] == 150
    assert first(cut)["reps"] == [8, 12] and first(cut)["rest"] == 90


def test_start_weights_are_conservative_and_rounded():
    man = Profile("m", 28, 180, 80, 2, "mass", "balanced", 0, 3, 60)
    woman = Profile("f", 26, 165, 58, 2, "cut", "glutes", 0, 3, 60)
    bench = EXERCISES["bench_press"]
    assert start_weight(bench, man) == 40  # 0.5 * 80
    assert start_weight(bench, woman) == 20  # empty bar minimum
    assert start_weight(EXERCISES["glute_bridge"], woman) == 25  # 0.5 * 58 * 0.9 -> 26.1 -> step 2.5
    assert start_weight(EXERCISES["pull_up"], man) == 0  # body weight only
    assert start_weight(EXERCISES["push_up"], man) is None
    for ex in EXERCISES.values():
        w = start_weight(ex, man)
        if w:
            assert abs(w / ex.step - round(w / ex.step)) < 1e-9, ex.key
            assert w >= ex.min_weight


def test_nutrition_mifflin_st_jeor():
    p = Profile("m", 30, 180, 80, 3, "mass", "balanced", 1, 4, 60)
    n = nutrition.calculate(p)
    assert n.bmr == 1780  # 800 + 1125 - 150 + 5
    assert n.maintenance == round(1780 * 1.55)
    assert n.calories == 3030
    assert n.protein == 144 and n.fat == 72

    w = Profile("f", 26, 165, 58, 2, "cut", "glutes", 0, 3, 60)
    n = nutrition.calculate(w)
    assert n.bmr == round(580 + 1031.25 - 130 - 161)
    assert n.calories < n.maintenance
    assert n.protein == 116


def test_old_database_gets_profile_column(tmp_path):
    async def go():
        path = tmp_path / "old.db"
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                "CREATE TABLE users (user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, program TEXT, "
                "day_idx INTEGER NOT NULL DEFAULT 0, inc_scale REAL NOT NULL DEFAULT 1.0, "
                "rest_override INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"
            )
            await conn.execute("INSERT INTO users (user_id, name, program, created_at) VALUES (1, 'a', 'bbr', 0)")
            await conn.commit()
        db = Database(path)
        await db.connect()
        user = await db.get_user(1)
        assert user.program == "bbr" and user.profile is None
        await db.save_profile(1, {"sex": "m"})
        assert (await db.get_user(1)).profile == {"sex": "m"}
        await db.close()

    asyncio.run(go())
