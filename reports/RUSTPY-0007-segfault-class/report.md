# RUSTPY-0007 — segfault class (memory-unsafety): recursion→stack-overflow, `_sre` Match mapping, object-core

~14 crash dirs (9 SIGSEGV + 5 SIGABRT). These are the most serious findings — the interpreter dies with a
**native segfault/abort**, not a clean Rust panic, so they are genuine memory-unsafety. gdb backtraces of
the reproducing vehicles dedup them into (at least) three sub-causes.

## 7a — native stack overflow from unbounded recursion (majority)

Top frames are `…::hash` / rich-compare, across `email`, `json`, `asyncio` (×3), `asyncio_tasks`.
fusil injects **deeply recursive / cyclic tricky objects**; hashing or comparing one recurses on the
native Rust stack with no depth guard → the stack overflows → SIGSEGV (or SIGABRT from the guard page).
CPython raises `RecursionError` (it checks `Py_EnterRecursiveCall` on these paths).

- **Fix:** add a recursion-depth check (equivalent to CPython's recursion guard) on the native
  hash/compare/repr paths, converting overflow into a `RecursionError`.

## 7b — `re.Match` mapping protocol segfault → **promoted to RUSTPY-0008**

Minimized to a deterministic 3-line reproducer — subscripting an **uninitialized** `re.Match`
(`type(re.match('a','a')).__new__(M)[0]`) reads garbage `regs`/`string`. Full write-up + repro in
**`reports/RUSTPY-0008-sre-match-uninitialized-subscript/`**.

## 7c — object-core access (`object::core::PyInner`)

Top frame `rustpython_vm::object::core::PyInner`, in `selectors` / `asyncio_queues`. An object-core
access on an invalid/freed object (possibly recursion-adjacent). Fewer vehicles; needs a per-dir gdb pass
to separate from 7a.

## Triage note

7a (recursion → stack overflow) is the dominant and most reproducible segfault cause and is the cleanest
to fix (a recursion guard closes the whole family). 7b (`_sre` Match) is a distinct, concrete
memory-unsafety bug worth its own fix. Each vehicle's `source.py` reproduces its crash directly
(`rustpython source.py` → SIGSEGV); minimal reproducers are the natural next step once the maintainers
confirm interest.
