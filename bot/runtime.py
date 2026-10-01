"""In-memory per-user state that doesn't need to survive a restart: rest timers and the last set prompt."""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class RestTimer:
    task: asyncio.Task
    chat_id: int
    message_id: int
    workout_id: int
    ends_at: float
    total: int

    def left(self) -> int:
        return max(0, round(self.ends_at - time.monotonic()))


@dataclass
class Runtime:
    timers: dict[int, RestTimer] = field(default_factory=dict)
    # user_id -> (chat_id, message_id) of the set prompt that still has rep buttons
    prompts: dict[int, tuple[int, int]] = field(default_factory=dict)

    def pop_timer(self, user_id: int) -> RestTimer | None:
        timer = self.timers.pop(user_id, None)
        if timer and not timer.task.done() and timer.task is not asyncio.current_task():
            timer.task.cancel()
        return timer
