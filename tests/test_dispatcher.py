"""End-to-end: real Dispatcher and routers, fake Telegram API."""

import asyncio
import itertools
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup, EditMessageText, SendAnimation, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.__main__ import AccessMiddleware
from bot import keyboards as kb
from bot.config import Config
from bot.db import Database
from bot.handlers import menu, workout
from bot.runtime import Runtime

UID = 42
CHAT = Chat(id=UID, type="private")
ME = User(id=UID, is_bot=False, first_name="Азат")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[Message] = []
        self.calls: list[TelegramMethod] = []
        self.ids = itertools.count(100)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendAnimation)):
            text = getattr(method, "text", None) or getattr(method, "caption", None)
            msg = Message(message_id=next(self.ids), date=datetime.now(), chat=CHAT, text=text,
                          reply_markup=method.reply_markup if hasattr(method.reply_markup, "inline_keyboard") else None)
            self.sent.append(msg)
            return msg
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
            return True
        return True

    async def stream_content(self, *a, **k):
        yield b""

    async def close(self):
        pass


def test_full_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(workout, "REST_TICK", 0.01)

    async def go():
        db = Database(tmp_path / "bot.db")
        await db.connect()
        session = FakeSession()
        bot = Bot("1:x", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        config = Config("1:x", frozenset({UID}), tmp_path / "bot.db", tmp_path / "media", "Europe/Moscow")
        dp = Dispatcher(storage=MemoryStorage(), db=db, rt=Runtime(), config=config)
        access = AccessMiddleware(config.allowed_users)
        dp.message.outer_middleware(access)
        dp.callback_query.outer_middleware(access)
        dp.include_routers(workout.router, menu.router)
        upd = itertools.count(1)

        async def say(text, user=ME):
            m = Message(message_id=next(session.ids), date=datetime.now(), chat=CHAT, from_user=user, text=text)
            await dp.feed_update(bot, Update(update_id=next(upd), message=m))

        async def press(data: str, msg: Message | None = None):
            msg = msg or session.sent[-1]
            cb = CallbackQuery(id=str(next(upd)), from_user=ME, chat_instance="c", message=msg, data=data)
            await dp.feed_update(bot, Update(update_id=next(upd), callback_query=cb))

        def last_text():
            return session.sent[-1].text or ""

        def find_button(prefix: str, text: str | None = None) -> str:
            for m in reversed(session.sent):
                if m.reply_markup:
                    for row in m.reply_markup.inline_keyboard:
                        for b in row:
                            if b.callback_data.startswith(prefix) and (text is None or b.text == text):
                                return b.callback_data
            raise AssertionError(f"no button {prefix} {text}")

        # stranger is refused and told their ID
        await say("/start", User(id=7, is_bot=False, first_name="X"))
        assert "Твой Telegram ID: <code>7</code>" in last_text()

        await say("/start")
        assert "Basic Beginner Routine" in last_text()
        await press(kb.ProgCb(key="bbr").pack())
        assert (await db.get_user(UID)).program == "bbr"

        await say(kb.BTN_WORKOUT)
        assert "Тренировка A" in last_text()
        await press(kb.StartCb(action="other", day=1).pack())  # preview switch edits in place
        await press(kb.StartCb(action="go", day=0).pack())
        assert "Какой рабочий вес" in last_text()

        await say("40")
        assert "Подход 1/3" in last_text()
        await press(find_button("rep:", "✅ 5"))
        assert "Отдых" in last_text()
        await say("5")  # typing reps during rest logs the next set and drops the timer
        await asyncio.sleep(0.05)
        await press(find_button("w:", "⏭ Дальше"))
        assert "Подход 3/3" in last_text() and "Последний подход" in last_text()
        await press(find_button("rep:", "11"))
        assert any("В следующий раз: 45 кг" in (m.text or "") for m in session.sent[-4:])
        assert "Жим штанги лёжа" in (session.sent[-2].text or "") or "Какой рабочий вес" in last_text()

        # swap bench for dumbbells, then finish early
        await say("60")
        await press(find_button("w:", "🔄 Замена"))
        await press(find_button("swap:"))
        assert "Какой рабочий вес" in last_text()
        await say("/finish")
        await press(find_button("w:", "✅ Да, завершить"))
        assert "Тренировка завершена" in last_text()
        assert "Тяга штанги в наклоне" in last_text()

        await say(kb.BTN_PROGRESS)
        assert "Тренировок всего: <b>1</b>" in last_text()
        await press(find_button("hist:"))
        assert "Рабочий вес: 45 кг" in last_text()

        await say(kb.BTN_SETTINGS)
        await press(kb.MenuCb(action="scale").pack())
        assert (await db.get_user(UID)).inc_scale == 0.5

        await say(kb.BTN_PROGRAM)
        assert "Следующая по плану: <b>Тренировка B</b>" in last_text()

        await say("привет")
        assert "Не понял" in last_text()
        await db.close()

    asyncio.run(go())
