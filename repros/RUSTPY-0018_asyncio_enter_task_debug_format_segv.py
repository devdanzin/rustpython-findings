# RUSTPY-0018 (SIGSEGV, minimal). Root cause: PyAtomicRef<T>'s Debug impl is unsound
# (crates/vm/src/object/ext.rs:272) -- it casts the stored Py<T> pointer to bare T,
# skipping the object header, so formatting it reads misaligned fields. A PyFunction holds
# `code: PyAtomicRef<PyCode>`; `{:?}` on the function walks into CodeObject::Debug, which then
# reads garbage heap pointers (obj_name/source_path) -> SIGSEGV. _asyncio._enter_task uses
# `{:?}` (Debug, not Python repr) to format the tasks in its "Cannot enter" RuntimeError
# (_asyncio.rs:2492), so a second _enter_task with any *Python* function as a task crashes.
#
# Deterministic (5/5). gdb: CodeObject::Debug::fmt <- PyAtomicRef<PyCode>::Debug <-
# PyFunction::Debug <- _enter_task -- identical to the fleet vehicle. A builtin (len; no
# PyCode) does NOT crash -- it prints the RuntimeError -- which isolates the PyCode path.
# CPython: raises `RuntimeError: Cannot enter into task <Task ...> ...` (Python repr, bounded).
import _asyncio


def f():
    pass


_asyncio._enter_task(0, f)   # asyncio_running_task is None -> set to Some(f), returns
_asyncio._enter_task(0, f)   # now Some -> "Cannot enter" formats f with {:?} -> SIGSEGV
