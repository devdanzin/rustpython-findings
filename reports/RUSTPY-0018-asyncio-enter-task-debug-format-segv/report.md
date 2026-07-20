# RUSTPY-0018 — `_asyncio._enter_task` formats the task with Rust `{:?}` Debug in its error message → garbage messages + SIGSEGV on a hostile task (`_asyncio.rs:2492`)

**New in fusil-rustpython_08** — found hiding in the "recursion SIGSEGV" bucket (46 of 57 segv dirs
were actually an `_asyncio` cluster, not recursion). When `_enter_task(loop, task)` is called while
another task is already current, the "cannot enter" `RuntimeError` formats **both tasks with Rust's
`{:?}` (Debug)** instead of the Python `repr()`. Two consequences:

1. **Garbage error messages** — `{:?}` on a `PyObjectRef` recursively dumps the whole internal Rust
   struct (`PyStr { value: …, kind: Ascii, hash: … }`, `Mutex`, `RwLock`, `AtomicCell`,
   `CodeObject { … }`, `ContextInner { … }`, …) rather than the Python `<Task …>` repr CPython emits.
2. **SIGSEGV** — when the entered task wraps a hostile/unusual object, formatting its code object
   crashes: the fuzzer vehicle segfaults **2/2**, with gdb showing the fault inside
   `CodeObject::Debug::fmt` reached from `_enter_task`.

## Reproducer (root cause — the Rust-Debug dump)

```python
import _asyncio, asyncio
loop = asyncio.new_event_loop()
async def coro(): pass
t1 = loop.create_task(coro())
t2 = loop.create_task(coro())
_asyncio._enter_task(loop, t1)
_asyncio._enter_task(loop, t2)   # RuntimeError -- but the message is a Rust struct dump
```

RustPython raises a `RuntimeError` whose message is a multi-KB dump of `t1`'s internal Rust
representation (`… Mutex { data: PyStr { value: "coro", kind: Ascii, hash: … } }, … CodeObject { … },
task_context: RwLock { data: Some([PyObject PyContext { inner: ContextInner { idx: Cell { value:
18446744073709551615 }, … } }]) }, … ] is being executed.`). CPython:
`RuntimeError: Cannot enter into task <Task pending name='Task-2' …> while another task <Task
pending name='Task-1' …> is being executed.` (Python `repr`, bounded).

The **SIGSEGV** face needs a hostile entered task (stateful, like RUSTPY-0001); it is reproduced by
the fleet vehicle `inst-01/python/_asyncio-sigsegv-rustpySEGV/source.py` (2/2), gdb backtrace below.
A minimal segfault trigger is still being reduced.

## gdb backtrace (the segfault)

```
#0  <CodeObject<…> as core::fmt::Debug>::fmt
#1  core::fmt::write
#2  <&&PyCode as core::fmt::Debug>::fmt
#3  <PyAtomicRef<PyCode> as core::fmt::Debug>::fmt
#5  <PyFunction as core::fmt::Debug>::fmt
#10 rustpython_stdlib::_asyncio::_asyncio::_enter_task
```

## Root cause & fix

`crates/stdlib/src/_asyncio.rs`, `_enter_task`:

```rust
return Err(vm.new_runtime_error(format!(
    "Cannot enter into task {:?} while another task {:?} is being executed.",   // :2492
    task, current_task,
)));
```

`{:?}` is the Rust `Debug` impl for a Python object — it walks the object's entire internal Rust
structure (never intended for user-facing output), which is both wrong (garbage message) and unsafe
(the `CodeObject`/`PyFunction` Debug path segfaults on some objects). CPython formats the tasks with
`%R` (Python `repr`). Fix: use the Python repr, e.g.

```rust
task.repr(vm)?  // and current_task.repr(vm)?  -- bounded, safe, matches CPython
```

Audit the other `_asyncio` task-management error paths (`_swap_current_task`, `_leave_task`,
`_register*`/`_unregister*` — all appeared in the fuzzer's crashing sequence) for the same `{:?}`
anti-pattern, and more broadly grep `_asyncio.rs` (and other stdlib) for `format!(…{:?}…, <pyobject>)`.

## Impact

Any `_asyncio` "cannot enter into task" error (reachable from real asyncio misuse, not just the
low-level `_enter_task`) emits an internal-struct dump instead of a Python message, and can segfault
the interpreter when the task wraps an object whose Rust `Debug` misbehaves — a memory-unsafety
crash, not a clean error.

## Prior art

_To check vs the RustPython tracker (`_asyncio` `_enter_task` / Debug-format / task error)._ Appears
unreported. Distinct from the recursion→stack-overflow class (RUSTPY-0007a): gdb shows a linear
`Debug::fmt` chain from `_enter_task`, not unbounded self-recursion.
