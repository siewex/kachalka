"""Exercise animations: download from ExerciseDB once, upload to Telegram once, then reuse file_id."""

import asyncio
import logging
import shutil
from pathlib import Path

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

from .catalog import EXERCISES
from .db import Database

log = logging.getLogger(__name__)

GIF_URL = "https://static.exercisedb.dev/media/{id}.gif"


def media_path(media_dir: Path, ex_key: str) -> Path | None:
    for ext in ("mp4", "gif"):
        p = media_dir / f"{ex_key}.{ext}"
        if p.exists():
            return p
    return None


async def ensure_media(media_dir: Path) -> None:
    """Download missing GIFs; convert to a sharper, smaller MP4 if ffmpeg is installed."""
    media_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    missing = [ex for ex in EXERCISES.values() if media_path(media_dir, ex.key) is None]
    if not missing:
        return
    log.info("Downloading %d exercise animations", len(missing))
    async with aiohttp.ClientSession(headers={"User-Agent": "fit-bot (personal use)"}) as session:
        for ex in missing:
            gif = media_dir / f"{ex.key}.gif"
            try:
                async with session.get(GIF_URL.format(id=ex.edb_id), timeout=aiohttp.ClientTimeout(total=30)) as r:
                    r.raise_for_status()
                    gif.write_bytes(await r.read())
            except Exception as e:
                log.warning("Animation for %s not downloaded: %s", ex.key, e)
                continue
            if ffmpeg:
                await _to_mp4(ffmpeg, gif, media_dir / f"{ex.key}.mp4")
            await asyncio.sleep(1)  # be gentle with the free API


async def _to_mp4(ffmpeg: str, src: Path, dst: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
        "-vf", "scale=480:-2:flags=lanczos,format=yuv420p", "-movflags", "+faststart", "-an", str(dst),
    )
    if await proc.wait() == 0:
        src.unlink(missing_ok=True)
    else:
        dst.unlink(missing_ok=True)


async def send_exercise(
    bot: Bot, chat_id: int, db: Database, media_dir: Path, ex_key: str, caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    file_id = await db.get_media(ex_key)
    if file_id:
        try:
            return await bot.send_animation(chat_id, file_id, caption=caption, reply_markup=reply_markup)
        except TelegramBadRequest:
            await db.set_media(ex_key, None)

    path = media_path(media_dir, ex_key)
    if path is None:
        return await bot.send_message(chat_id, caption, reply_markup=reply_markup)
    msg = await bot.send_animation(chat_id, FSInputFile(path), caption=caption, reply_markup=reply_markup)
    sent = msg.animation or msg.video or msg.document
    if sent:
        await db.set_media(ex_key, sent.file_id)
    return msg
