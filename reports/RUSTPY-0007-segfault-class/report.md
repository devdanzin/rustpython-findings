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

## 7b — `re.Match` mapping protocol segfault (`_sre`)

gdb top frame:

```
#0 <rustpython_vm::stdlib::_sre::_sre::Match as ...AsMapping>::as_mapping::{closure}::{closure}
#1 <descriptor::SlotFunc>::call
...
```

Subscripting an `re.Match` object (`m[...]`) reaches the `_sre` `Match` `as_mapping` implementation and
segfaults (bad index/group handling, or an unchecked access on the match state). Reproduces from the
`re-sigsegv` vehicle (`source.py` → exit 139). Not yet minimized to a one-liner; the vehicle is small.

- **Fix:** bounds/None-check in the `Match` mapping getitem before indexing into the group/span state.

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
