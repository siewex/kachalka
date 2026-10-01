"""Full workout flow on a real SQLite file, plus the Telegram handlers driven by a fake Bot."""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot import service, texts
from bot.catalog import EXERCISES, PROGRAMS
from bot.config import Config
from bot.db import Database
from bot.handlers import workout as wh
from bot.runtime import Runtime

UID = 111


def run(coro):
    return asyncio.run(coro)


async def make_db(tmp_path: Path, program="bbr") -> Database:
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.create_user(UID, "Тест")
    await db.update_user(UID, program=program)
    return db


def test_bbr_session_progresses_and_rotates(tmp_path):
    async def go():
        db = await make_db(tmp_path)
        user = await db.get_user(UID)
        w = await service.start_workout(db, user)
        assert w.day_key == "A"

        with pytest.raises(service.WorkoutError):
            await service.log_set(db, w, 5)  # weight not set yet

        # row: all 5s, AMRAP 11 -> double jump
        await service.set_weight(db, w, 40)
        for reps in (5, 5):
            r = await service.log_set(db, await db.get_workout(w.id), reps)
            assert r.kind == "next_set"
        r = await service.log_set(db, await db.get_workout(w.id), 11)
        assert r.kind == "exercise_done" and r.outcome.weight == 45

        # bench: failed -> deload
        w = await db.get_workout(w.id)
        await service.set_weight(db, w, 60)
        for reps in (5, 4, 3):
            r = await service.log_set(db, await db.get_workout(w.id), reps)
        assert r.outcome.verdict == "deload" and r.outcome.weight == 52.5

        # double tap guard
        w = await db.get_workout(w.id)
        with pytest.raises(service.WorkoutError):
            await service.log_set(db, w, 5, expect=(0, 0))

        # squat: skip -> no progression, workout over
        assert await service.skip_exercise(db, w) is True
        summary = await service.finish_workout(db, await db.get_workout(w.id))
        assert summary.status == "done"
        assert summary.tonnage == 40 * 21 + 60 * 12
        assert [e.item.ex_key for e in summary.exercises] == ["barbell_row", "bench_press"]

        assert await db.get_lift(UID, "barbell_row") == (45, 0)
        assert await db.get_lift(UID, "bench_press") == (52.5, 0)
        assert (await db.get_user(UID)).day_idx == 1

        # next workout is B and remembers weights; the next A starts with new weights
        w2 = await service.start_workout(db, await db.get_user(UID))
        assert w2.day_key == "B"
        await service.finish_workout(db, w2)  # no sets -> cancelled, rotation unchanged
        assert (await db.get_workout(w2.id)).status == "cancelled"
        assert (await db.get_user(UID)).day_idx == 1
        w3 = await service.start_workout(db, await db.get_user(UID), day_idx=0)
        items = await db.items(w3.id)
        assert [(i.ex_key, i.weight) for i in items] == [("barbell_row", 45), ("bench_press", 52.5), ("squat", None)]
        await db.close()

    run(go())


def test_swap_and_record(tmp_path):
    async def go():
        db = await make_db(tmp_path, program="fullbody")
        await db.set_lift(UID, "db_bench_press", 22)
        user = await db.get_user(UID)
        w = await service.start_workout(db, user)
        await service.set_weight(db, w, 50)
        await service.log_set(db, await db.get_workout(w.id), 8)
        w = await db.get_workout(w.id)
        assert w.set_idx == 1
        with pytest.raises(service.WorkoutError):
            await service.swap_exercise(db, w, "deadlift")
        item = await service.swap_exercise(db, w, "leg_press")
        assert item.ex_key == "leg_press" and item.weight is None
        w = await db.get_workout(w.id)
        assert w.set_idx == 0 and await db.sets_for(w.id, 0) == []
        await db.close()

    run(go())


def test_all_cards_fit_caption_limit():
    for program in PROGRAMS.values():
        for day in program.days:
            for i, it in enumerate(day.items):
                from bot.db import Item
                item = Item(0, i, it.ex, it.sets, it.reps_lo, it.reps_hi, it.amrap, it.rule, it.rest, 100.0, None, None)
                card = texts.exercise_card(item, i + 1, len(day.items), [])
                assert len(re.sub(r"<[^>]+>", "", card)) <= texts.CAPTION_LIMIT
                assert "<blockquote expandable>" in card


def test_every_exercise_has_tips():
    for ex in EXERCISES.values():
        assert len(ex.cues) >= 2 and len(ex.mistakes) >= 2, ex.key


# --- handlers with a fake Bot ---


def fake_bot():
    bot = MagicMock()
    counter = iter(range(1000, 100000))

    async def send(*args, **kwargs):
        m = MagicMock()
        m.message_id = next(counter)
        m.animation = None
        m.video = None
        m.document = None
        return m

    bot.send_message = AsyncMock(side_effect=send)
    bot.send_animation = AsyncMock(side_effect=send)
    bot.edit_message_text = AsyncMock()
    bot.edit_message_reply_markup = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


def sent_texts(bot) -> list[str]:
    out = []
    for call in bot.send_message.call_args_list + bot.send_animation.call_args_list:
        out.append(call.kwargs.get("caption") or (call.args[1] if len(call.args) > 1 else call.kwargs.get("text", "")))
    return out


def test_handlers_drive_a_workout(tmp_path, monkeypatch):
    monkeypatch.setattr(wh, "REST_TICK", 0.01)

    async def go():
        db = await make_db(tmp_path)
        bot = fake_bot()
        rt = Runtime()
        config = Config("x", frozenset({UID}), tmp_path / "t.db", tmp_path / "media", "Europe/Moscow")
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=UID, user_id=UID))

        await service.start_workout(db, await db.get_user(UID))
        await wh.show_exercise(bot, UID, UID, db, rt, config, state)
        assert await state.get_state() == wh.Form.weight.state  # asks for the weight first
        assert "Тяга штанги в наклоне" in sent_texts(bot)[0]

        await state.clear()
        w = await db.active_workout(UID)
        await service.set_weight(db, w, 40)
        await wh.send_set_prompt(bot, UID, UID, db, rt)
        assert UID in rt.prompts

        await wh.record(bot, UID, UID, db, rt, config, state, 5, expect=(0, 0))
        assert UID in rt.timers  # rest timer running
        await db.update_item(w.id, 0, rest=0)
        rt.timers[UID].ends_at = 0  # expire now
        await asyncio.sleep(0.05)
        assert UID not in rt.timers
        assert "Отдых окончен" in sent_texts(bot)[-1]

        await wh.record(bot, UID, UID, db, rt, config, state, 5)
        await wh.record(bot, UID, UID, db, rt, config, state, 8)
        texts_ = sent_texts(bot)
        assert any("В следующий раз: 42,5 кг" in t for t in texts_)
        assert any("Жим штанги лёжа" in t for t in texts_)  # next exercise card

        await wh.finish(bot, UID, UID, db, rt, state)
        assert "Тренировка завершена" in sent_texts(bot)[-1]
        assert await db.active_workout(UID) is None
        assert rt.timers == {} and rt.prompts == {}
        await db.close()

    run(go())
