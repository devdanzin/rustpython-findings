# RUSTPY-0018: _asyncio._enter_task formats the task with Rust {:?} Debug (not repr) in its
# "cannot enter" RuntimeError (_asyncio.rs:2492). This demonstrates the root cause -- the message
# is a multi-KB dump of the task's INTERNAL RUST STRUCT instead of the Python <Task ...> repr.
# The SIGSEGV face (gdb: CodeObject::Debug::fmt via _enter_task) needs a hostile entered task;
# reproduced 2/2 by the fleet vehicle. CPython formats with %R (repr): a bounded, safe message.
import _asyncio
import asyncio

loop = asyncio.new_event_loop()


async def coro():
    pass


t1 = loop.create_task(coro())
t2 = loop.create_task(coro())
_asyncio._enter_task(loop, t1)
_asyncio._enter_task(loop, t2)  # RuntimeError whose message is a Rust struct dump
