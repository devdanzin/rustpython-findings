# RUSTPY-0018 — `PyAtomicRef<T>`'s `Debug` is unsound (type-confuses `Py<T>` as `T`); `_asyncio._enter_task`'s `{:?}` error message reaches it and SIGSEGVs on any Python-function task (`ext.rs:272` via `_asyncio.rs:2492`)

**New in fusil-rustpython_08** — found hiding in the "recursion SIGSEGV" bucket (46 of 57 segv dirs
were actually an `_asyncio` cluster, not recursion). **Minimal SIGSEGV repro achieved (5 lines,
deterministic 5/5, gdb-confirmed identical to the vehicle).** A plain Python function is enough — no
hostile object needed. Two distinct bugs are in play; the second is the deeper root cause.

## Minimal reproducer (SIGSEGV)

```python
import _asyncio


def f():
    pass


_asyncio._enter_task(0, f)   # asyncio_running_task is None -> set to Some(f), returns
_asyncio._enter_task(0, f)   # now Some -> "Cannot enter" formats f with {:?} -> SIGSEGV
```

`_enter_task(loop, task)` checks the per-VM `vm.asyncio_running_task` (`_asyncio.rs:2489`): the first
call sets it to `Some(f)`; the **second** call finds it non-`None` and builds the "Cannot enter into
task {:?} while another task {:?}" `RuntimeError` (`_asyncio.rs:2492`), formatting both tasks with Rust
`{:?}` (Debug). `f` is a `PyFunction`; formatting it walks into its `code` field and crashes (below).
`def`, `lambda`, and plain methods all trigger it; a **builtin** (`len`, which has no `PyCode`) does
**not** — it prints the `RuntimeError` — which isolates the crash to the `PyAtomicRef<PyCode>` path.

CPython: `RuntimeError: Cannot enter into task <Task pending name='Task-2' ...> while another task
<Task pending ...> is being executed.` — Python `repr` (`%R`), bounded and safe.

## Root cause — two bugs

### Bug 1 (deeper, general): `PyAtomicRef<T>::Debug` is unsound — `ext.rs:272`

```rust
impl<T: fmt::Debug> fmt::Debug for PyAtomicRef<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "PyAtomicRef(")?;
        unsafe {
            self.inner.load(Ordering::Relaxed)
                .cast::<T>()          // <-- BUG: stored pointer is *Py<T>, not *T
                .as_ref().fmt(f)
        }?;
        write!(f, ")")
    }
}
```

`PyAtomicRef<T>::inner` stores a pointer to a **`Py<T>`** (the full object, header + payload) — set via
`PyRef::leak(pyref) as *const Py<T>` in `From` (`ext.rs:288`), and read back as `Py<T>` by **every
other** method: `Deref` (`.cast::<Py<T>>()`, `:301`), `load_raw` (`:324`), `swap` (`:331`). The `Debug`
impl is the lone outlier: it `.cast::<T>()`s, reinterpreting the `Py<T>` as a bare payload `T` and
skipping the object header. It then formats the misaligned bytes.

For `PyFunction`, `code: PyAtomicRef<PyCode>`. `CodeObject::Debug` (`bytecode.rs:1308`) reads
`self.obj_name` and `self.source_path` — heap pointers. Under the type confusion those "pointers" are
object-header bytes (refcount/typeid), so dereferencing them **segfaults**. It fires for *any* Python
function, deterministically.

**Fix (one char):** `.cast::<T>()` → `.cast::<Py<T>>()` (`Py<T>: Debug` exists at `core.rs:2261`), so
`Debug` matches `Deref`/`load_raw`/`swap`. That removes the whole class of segfaults reachable by
`{:?}`-formatting *any* object holding a `PyAtomicRef` whose payload has a pointer-chasing `Debug`.

Other `{:?}`-of-PyObject sites that can independently reach this same unsoundness (audit alongside):
`typevar.rs:933`/`:996` (`format!("{:?}...", zelf.__origin__)`), `os.rs:946`/`:1069`
(`{:?}` on `zelf.as_object()`).

### Bug 2 (the trigger): `_asyncio._enter_task` uses `{:?}` for a user-facing message — `_asyncio.rs:2492`

```rust
return Err(vm.new_runtime_error(format!(
    "Cannot enter into task {:?} while another task {:?} is being executed.",   // :2492
    task, running_task.as_ref().unwrap(),
)));
```

`{:?}` on a Python object is wrong even when it doesn't crash: it dumps the object's entire internal
Rust struct (`PyStr { value: …, kind: Ascii, hash: … }`, `Mutex`, `RwLock`, `CodeObject { … }`,
`ContextInner { … }`) instead of the Python `<Task …>` repr. CPython formats with `%R`.

**Fix:** use the Python repr — `task.repr(vm)?` / `current_task.repr(vm)?` (bounded, safe, matches
CPython). Audit the sibling task paths that appeared in the fuzzer's crashing sequence
(`_swap_current_task`, `_leave_task`, `_register*`/`_unregister*`) and grep `_asyncio.rs` for
`format!(…{:?}…, <pyobject>)`.

Fixing Bug 2 removes *this* crash trigger and the garbage message; fixing Bug 1 removes the underlying
memory-unsafety for every `{:?}` path. Both are worth doing.

## gdb backtrace (minimal repro — identical to the fleet vehicle)

```
#0  <CodeObject<Literal> as core::fmt::Debug>::fmt          bytecode.rs:1308
#1  core::fmt::write
#2  <&&PyCode as core::fmt::Debug>::fmt
#3  <PyAtomicRef<PyCode> as core::fmt::Debug>::fmt          ext.rs:272   <-- the type confusion
#5  <PyFunction as core::fmt::Debug>::fmt                   function.rs (derive)
#7  rustpython_vm::object::core::debug_obj::<PyFunction>
#10 rustpython_stdlib::_asyncio::_asyncio::_enter_task      _asyncio.rs:2492
```

## Impact

Any `_asyncio` "cannot enter into task" error (reachable from real asyncio misuse, not just low-level
`_enter_task`) emits an internal-struct dump instead of a Python message, and **segfaults the
interpreter whenever a task is an ordinary Python function** — a memory-unsafety crash from a plain,
non-hostile input. The underlying `PyAtomicRef<T>::Debug` unsoundness is broader than `_asyncio`.

## Prior art

_To check vs the RustPython tracker (`_asyncio` `_enter_task` / `PyAtomicRef` Debug / `{:?}` in error
messages)._ Appears unreported. Distinct from the recursion→stack-overflow class (RUSTPY-0007a): gdb
shows a short linear `Debug::fmt` chain from `_enter_task`, not unbounded self-recursion.
