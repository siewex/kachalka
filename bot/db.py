"""SQLite storage. Small and boring on purpose: two users, a few thousand rows a year."""

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    program     TEXT,
    day_idx     INTEGER NOT NULL DEFAULT 0,
    inc_scale   REAL NOT NULL DEFAULT 1.0,
    rest_override INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lifts (
    user_id INTEGER NOT NULL,
    ex_key  TEXT NOT NULL,
    weight  REAL NOT NULL,
    fails   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, ex_key)
);
CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    program     TEXT NOT NULL,
    day_key     TEXT NOT NULL,
    day_idx     INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    ex_idx      INTEGER NOT NULL DEFAULT 0,
    set_idx     INTEGER NOT NULL DEFAULT 0,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS workouts_user ON workouts(user_id, status);
CREATE TABLE IF NOT EXISTS workout_items (
    workout_id INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    ex_key     TEXT NOT NULL,
    sets       INTEGER NOT NULL,
    reps_lo    INTEGER NOT NULL,
    reps_hi    INTEGER NOT NULL,
    amrap      INTEGER NOT NULL,
    rule       TEXT NOT NULL,
    rest       INTEGER NOT NULL,
    weight     REAL,
    next_weight REAL,
    verdict    TEXT,
    PRIMARY KEY (workout_id, idx)
);
CREATE TABLE IF NOT EXISTS sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    ex_key     TEXT NOT NULL,
    set_no     INTEGER NOT NULL,
    weight     REAL NOT NULL,
    reps       INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sets_workout ON sets(workout_id, idx);
CREATE TABLE IF NOT EXISTS media (
    ex_key  TEXT PRIMARY KEY,
    file_id TEXT NOT NULL
);
"""


@dataclass
class User:
    user_id: int
    name: str
    program: str | None
    day_idx: int
    inc_scale: float
    rest_override: int


@dataclass
class Workout:
    id: int
    user_id: int
    program: str
    day_key: str
    day_idx: int
    status: str
    ex_idx: int
    set_idx: int
    started_at: int
    finished_at: int | None


@dataclass
class Item:
    workout_id: int
    idx: int
    ex_key: str
    sets: int
    reps_lo: int
    reps_hi: int
    amrap: bool
    rule: str
    rest: int
    weight: float | None
    next_weight: float | None
    verdict: str | None


@dataclass
class SetRow:
    workout_id: int
    idx: int
    ex_key: str
    set_no: int
    weight: float
    reps: int
    created_at: int


def now() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _one(self, sql: str, *args):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, *args):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def _exec(self, sql: str, *args) -> int:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # --- users ---

    async def get_user(self, user_id: int) -> User | None:
        row = await self._one("SELECT * FROM users WHERE user_id = ?", user_id)
        return _user(row) if row else None

    async def create_user(self, user_id: int, name: str) -> User:
        await self._exec(
            "INSERT OR IGNORE INTO users (user_id, name, created_at) VALUES (?, ?, ?)",
            user_id, name, now(),
        )
        return await self.get_user(user_id)

    async def update_user(self, user_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE users SET {cols} WHERE user_id = ?", *fields.values(), user_id)

    # --- lifts (current working weight per exercise) ---

    async def get_lift(self, user_id: int, ex_key: str) -> tuple[float, int] | None:
        row = await self._one(
            "SELECT weight, fails FROM lifts WHERE user_id = ? AND ex_key = ?", user_id, ex_key
        )
        return (row["weight"], row["fails"]) if row else None

    async def set_lift(self, user_id: int, ex_key: str, weight: float, fails: int | None = None) -> None:
        if fails is None:
            await self._exec(
                "INSERT INTO lifts (user_id, ex_key, weight) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, ex_key) DO UPDATE SET weight = excluded.weight",
                user_id, ex_key, weight,
            )
        else:
            await self._exec(
                "INSERT INTO lifts (user_id, ex_key, weight, fails) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, ex_key) DO UPDATE SET weight = excluded.weight, fails = excluded.fails",
                user_id, ex_key, weight, fails,
            )

    async def all_lifts(self, user_id: int) -> dict[str, float]:
        rows = await self._all("SELECT ex_key, weight FROM lifts WHERE user_id = ?", user_id)
        return {r["ex_key"]: r["weight"] for r in rows}

    # --- workouts ---

    async def create_workout(self, user_id: int, program: str, day_key: str, day_idx: int, items: list[dict]) -> int:
        cur = await self.conn.execute(
            "INSERT INTO workouts (user_id, program, day_key, day_idx, started_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, program, day_key, day_idx, now()),
        )
        wid = cur.lastrowid
        await self.conn.executemany(
            "INSERT INTO workout_items (workout_id, idx, ex_key, sets, reps_lo, reps_hi, amrap, rule, rest, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (wid, i, it["ex_key"], it["sets"], it["reps_lo"], it["reps_hi"], int(it["amrap"]),
                 it["rule"], it["rest"], it["weight"])
                for i, it in enumerate(items)
            ],
        )
        await self.conn.commit()
        return wid

    async def get_workout(self, workout_id: int) -> Workout | None:
        row = await self._one("SELECT * FROM workouts WHERE id = ?", workout_id)
        return Workout(**dict(row)) if row else None

    async def active_workout(self, user_id: int) -> Workout | None:
        row = await self._one(
            "SELECT * FROM workouts WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", user_id
        )
        return Workout(**dict(row)) if row else None

    async def update_workout(self, workout_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE workouts SET {cols} WHERE id = ?", *fields.values(), workout_id)

    async def items(self, workout_id: int) -> list[Item]:
        rows = await self._all("SELECT * FROM workout_items WHERE workout_id = ? ORDER BY idx", workout_id)
        return [_item(r) for r in rows]

    async def update_item(self, workout_id: int, idx: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(
            f"UPDATE workout_items SET {cols} WHERE workout_id = ? AND idx = ?", *fields.values(), workout_id, idx
        )

    # --- sets ---

    async def add_set(self, workout_id: int, idx: int, ex_key: str, set_no: int, weight: float, reps: int) -> None:
        await self._exec(
            "INSERT INTO sets (workout_id, idx, ex_key, set_no, weight, reps, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            workout_id, idx, ex_key, set_no, weight, reps, now(),
        )

    async def sets_for(self, workout_id: int, idx: int | None = None) -> list[SetRow]:
        if idx is None:
            rows = await self._all("SELECT * FROM sets WHERE workout_id = ? ORDER BY idx, set_no", workout_id)
        else:
            rows = await self._all(
                "SELECT * FROM sets WHERE workout_id = ? AND idx = ? ORDER BY set_no", workout_id, idx
            )
        return [_set(r) for r in rows]

    async def delete_sets(self, workout_id: int, idx: int) -> None:
        await self._exec("DELETE FROM sets WHERE workout_id = ? AND idx = ?", workout_id, idx)

    async def last_session_sets(self, user_id: int, ex_key: str, exclude_workout: int | None = None) -> list[SetRow]:
        """Sets of the most recent finished workout where this exercise was done."""
        row = await self._one(
            "SELECT s.workout_id FROM sets s JOIN workouts w ON w.id = s.workout_id "
            "WHERE w.user_id = ? AND s.ex_key = ? AND w.status = 'done' AND w.id != ? "
            "ORDER BY s.workout_id DESC LIMIT 1",
            user_id, ex_key, exclude_workout or -1,
        )
        if not row:
            return []
        rows = await self._all(
            "SELECT * FROM sets WHERE workout_id = ? AND ex_key = ? ORDER BY set_no", row["workout_id"], ex_key
        )
        return [_set(r) for r in rows]

    async def history(self, user_id: int, ex_key: str, limit: int = 8) -> list[tuple[int, list[SetRow]]]:
        """Last N finished sessions of an exercise: [(workout started_at, sets)], newest first."""
        wrows = await self._all(
            "SELECT DISTINCT w.id, w.started_at FROM sets s JOIN workouts w ON w.id = s.workout_id "
            "WHERE w.user_id = ? AND s.ex_key = ? AND w.status = 'done' ORDER BY w.id DESC LIMIT ?",
            user_id, ex_key, limit,
        )
        result = []
        for w in wrows:
            rows = await self._all(
                "SELECT * FROM sets WHERE workout_id = ? AND ex_key = ? ORDER BY set_no", w["id"], ex_key
            )
            result.append((w["started_at"], [_set(r) for r in rows]))
        return result

    async def all_sets_for_exercise(self, user_id: int, ex_key: str, exclude_workout: int | None = None) -> list[SetRow]:
        rows = await self._all(
            "SELECT s.* FROM sets s JOIN workouts w ON w.id = s.workout_id "
            "WHERE w.user_id = ? AND s.ex_key = ? AND w.status = 'done' AND w.id != ?",
            user_id, ex_key, exclude_workout or -1,
        )
        return [_set(r) for r in rows]

    async def workout_stats(self, user_id: int, since: int) -> tuple[int, int]:
        total = await self._one("SELECT COUNT(*) c FROM workouts WHERE user_id = ? AND status = 'done'", user_id)
        recent = await self._one(
            "SELECT COUNT(*) c FROM workouts WHERE user_id = ? AND status = 'done' AND started_at >= ?",
            user_id, since,
        )
        return total["c"], recent["c"]

    async def trained_exercises(self, user_id: int) -> list[str]:
        rows = await self._all(
            "SELECT s.ex_key, MAX(s.id) last FROM sets s JOIN workouts w ON w.id = s.workout_id "
            "WHERE w.user_id = ? AND w.status = 'done' GROUP BY s.ex_key ORDER BY last DESC",
            user_id,
        )
        return [r["ex_key"] for r in rows]

    # --- telegram media cache ---

    async def get_media(self, ex_key: str) -> str | None:
        row = await self._one("SELECT file_id FROM media WHERE ex_key = ?", ex_key)
        return row["file_id"] if row else None

    async def set_media(self, ex_key: str, file_id: str | None) -> None:
        if file_id is None:
            await self._exec("DELETE FROM media WHERE ex_key = ?", ex_key)
        else:
            await self._exec(
                "INSERT INTO media (ex_key, file_id) VALUES (?, ?) "
                "ON CONFLICT(ex_key) DO UPDATE SET file_id = excluded.file_id",
                ex_key, file_id,
            )


def _user(r) -> User:
    return User(
        user_id=r["user_id"], name=r["name"], program=r["program"], day_idx=r["day_idx"],
        inc_scale=r["inc_scale"], rest_override=r["rest_override"],
    )


def _item(r) -> Item:
    d = dict(r)
    d["amrap"] = bool(d["amrap"])
    return Item(**d)


def _set(r) -> SetRow:
    return SetRow(
        workout_id=r["workout_id"], idx=r["idx"], ex_key=r["ex_key"], set_no=r["set_no"],
        weight=r["weight"], reps=r["reps"], created_at=r["created_at"],
    )
