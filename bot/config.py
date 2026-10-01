import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_users: frozenset[int]
    db_path: Path
    media_dir: Path
    tz: str


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")
    allowed = frozenset(
        int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x
    )
    data_dir = Path(os.getenv("DATA_DIR", ROOT / "data"))
    return Config(
        bot_token=token,
        allowed_users=allowed,
        db_path=data_dir / "bot.db",
        media_dir=data_dir / "media",
        tz=os.getenv("TZ_NAME", "Europe/Moscow"),
    )
