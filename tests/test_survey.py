"""Questionnaire end-to-end through the real Dispatcher."""

import asyncio
import itertools
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, Update

from bot import keyboards as kb
from bot import service
from bot.__main__ import AccessMiddleware
from bot.config import Config
from bot.db import Database
from bot.handlers import menu, survey, workout
from bot.runtime import Runtime
from tests.test_dispatcher import CHAT, ME, UID, FakeSession


class Chat:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.session = FakeSession()
        self.upd = itertools.count(1)

    async def start(self):
        self.db = Database(self.tmp_path / "bot.db")
        await self.db.connect()
        self.bot = Bot("1:x", session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        config = Config("1:x", frozenset({UID}), self.tmp_path / "bot.db", self.tmp_path / "media", "Europe/Moscow")
        self.dp = Dispatcher(storage=MemoryStorage(), db=self.db, rt=Runtime(), config=config)
        access = AccessMiddleware(config.allowed_users)
        self.dp.message.outer_middleware(access)
        self.dp.callback_query.outer_middleware(access)
        for r in (survey.router, workout.router, menu.router):
            r._parent_router = None  # routers are module singletons; tests build several dispatchers
        self.dp.include_routers(survey.router, workout.router, menu.router)

    async def say(self, text):
        m = Message(message_id=next(self.session.ids), date=datetime.now(), chat=CHAT, from_user=ME, text=text)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), message=m))

    def button(self, prefix: str, text: str | None = None) -> tuple[Message, str]:
        for m in reversed(self.session.sent):
            if m.reply_markup:
                for row in m.reply_markup.inline_keyboard:
                    for b in row:
                        if b.callback_data.startswith(prefix) and (text is None or text in b.text):
                            return m, b.callback_data
        raise AssertionError(f"no button {prefix} {text}")

    async def press(self, prefix: str, text: str | None = None):
        msg, data = self.button(prefix, text)
        cb = CallbackQuery(id=str(next(self.upd)), from_user=ME, chat_instance="c", message=msg, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), callback_query=cb))

    def texts(self, last: int = 1) -> str:
        return "\n".join(m.text or "" for m in self.session.sent[-last:])


def test_questionnaire_builds_program_and_suggests_weights(tmp_path):
    async def go():
        c = Chat(tmp_path)
        await c.start()
        try:
            await scenario(c)
        finally:
            await c.db.close()

    async def scenario(c: Chat):
        await c.say("/start")
        assert "анкет" in c.texts()
        await c.press("m:survey")
        assert "1/11" in c.texts()

        await c.press("sv:sex", "Женский")
        await c.say("26")
        await c.say("абв")  # garbage is rejected, question stays
        assert "Напиши число" in c.texts()
        await c.say("165")
        await c.say("58,5")
        await c.press("sv:activity", "5–8")
        await c.say("10")  # typing while buttons are expected
        assert "кнопкой" in c.texts()
        await c.press("sv:goal", "Рельеф")
        await c.press("sv:focus", "Ягодицы")
        await c.press("sv:level", "Новичок")
        await c.press("sv:days", "3")
        await c.press("sv:minutes", "60")
        await c.press("sv:restr", "Колени")
        await c.press("sv:restr", "→")  # fake API keeps the old markup; label differs only after the edit

        preview = c.texts(2)
        assert "Рельеф / сушка · ягодицы и ноги · 3 дн/нед" in preview
        assert "Ягодичный мост со штангой" in preview and "старт ~" in preview
        assert "Болгарские выпады" not in preview  # knees
        assert "Калории:" in preview

        await c.press("sv:confirm")
        user = await c.db.get_user(UID)
        assert user.program == service.CUSTOM
        assert user.profile["sex"] == "f" and user.profile["weight"] == 58.5
        assert user.profile["restrictions"] == ["knees"]
        assert await c.db.bodyweights(UID)

        # workout: first exercise offers the suggested starting weight as a button
        await c.say(kb.BTN_WORKOUT)
        assert "Ягодицы A" in c.texts()
        await c.press("start:go")
        assert "предлагаю" in c.texts()
        await c.press("wp:")
        assert "Подход 1/3" in c.texts()
        w = await c.db.active_workout(UID)
        item = await service.current_item(c.db, w)
        assert item.ex_key == "glute_bridge" and item.weight == 25

        # body weight log updates the profile used for calories
        await c.say("/finish")
        await c.press("w:", "Да")
        await c.say(kb.BTN_PROGRESS)
        await c.press("m:bodyweight")
        await c.say("57,8")
        assert "Записал" in c.texts() and "-0,7 кг" in c.texts()
        assert (await c.db.get_user(UID)).profile["weight"] == 57.8

        # nutrition from the program menu
        await c.say(kb.BTN_PROGRAM)
        await c.press("m:nutrition")
        assert "Калории:" in c.texts()

        # retaking offers to keep the previous numbers
        await c.press("m:survey")
        await c.press("sv:sex", "Женский")
        assert "Оставить 26 лет" in str(c.session.sent[-1].reply_markup)

    asyncio.run(go())
