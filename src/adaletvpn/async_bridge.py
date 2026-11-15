"""Cooperative GTK/asyncio bridge using only public asyncio APIs."""

import asyncio
from weakref import WeakKeyDictionary

from adaletvpn.diagnostics import Stage, report_failure


_ACTIVE_TASKS = WeakKeyDictionary()


def _tracked_task(loop, coroutine, **kwargs):
    task = asyncio.Task(coroutine, loop=loop, **kwargs)
    active = _ACTIVE_TASKS.setdefault(loop, set())
    active.add(task)

    def finished(done):
        loop.call_soon(active.discard, done)

    task.add_done_callback(finished)
    return task


def pump_once(loop: asyncio.AbstractEventLoop) -> None:
    if loop.is_running() or loop.is_closed():
        return
    # The second turn runs completion callbacks queued by the first turn.
    # stop() is queued before each turn so neither turn blocks GTK.
    for _ in range(2):
        loop.call_soon(loop.stop)
        loop.run_forever()


def has_pending_tasks(loop: asyncio.AbstractEventLoop) -> bool:
    return bool(asyncio.all_tasks(loop) or _ACTIVE_TASKS.get(loop))


def create_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    loop.set_task_factory(_tracked_task)
    loop.set_exception_handler(
        lambda _loop, context: report_failure(Stage.EVENT_LOOP, context.get("exception"))
    )
    return loop
