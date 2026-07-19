# RustPython crash findings — index / bug sample

First fusil run against RustPython 0.5.0 (`fusil-rustpython_01`, 4 instances, no TSan/OOM). Every entry
is a Python-level program that makes the interpreter **abort or segfault** where CPython raises or works.
Dedup key = panic site (`file.rs:line`).

## Panics (6 distinct)

| id | panic site | one-line reproducer | veh | what's wrong |
|----|-----------|---------------------|-----|--------------|
| **RUSTPY-0001** | `stdlib/_thread.rs:977` `RefCell already borrowed` | `repro.py` (global `_local` + a stored value whose `__del__` re-enters `_local`; **reproduced 3/3**) | **48** | `cleanup_thread_local_data` holds `LOCAL_GUARDS.borrow_mut()` across `.clear()`, which drops guards; a dropped thread-local value whose `__del__` re-enters `_thread._local` borrows `LOCAL_GUARDS` again → `BorrowMutError`. **Dominant.** |
| **RUSTPY-0002** | `types/structseq.rs:311` `index out of bounds` | `import pwd; pwd.struct_passwd().pw_name` | **24** | A struct-sequence built with **fewer elements than its named fields** (the no-arg constructor makes an *empty* one) — the field getter does `zelf[i]` with **no bounds check**. CPython raises `TypeError` at construction. **Dominant.** |
| **RUSTPY-0003** | `class.rs:87` `static type has not been initialized` | `import _md5; _md5.md5()` | **15** | A native type is used before its static cell is initialized; the code `unwrap_or_else(fail)` **panics** instead of raising. |
| **RUSTPY-0004** | `stdlib/src/csv.rs:805` `Option::unwrap() on None` | `import _csv; _csv.reader([]).__next__()` | **7** | `get_lineterminator` does `GLOBAL_HASHMAP…get(name).unwrap()`-style access on a dialect name that isn't registered → `unwrap()` on `None`. |
| **RUSTPY-0005** | `stdlib/_typing.rs:43` `index out of bounds` | `import _typing; _typing._idfunc()` | 2 | `_idfunc` does `args.args[0]` with **no arity check** → OOB on a no-arg call. CPython raises `TypeError`. |
| **RUSTPY-0006** | `stdlib/builtins.rs:557/607` `PyStr contains surrogates` | `eval(chr(0xd800))` | 2 | `compile()`/`eval()` call `source.expect_str()`, which **panics** on a string containing lone surrogates. CPython raises `ValueError`/compiles. **Rare** (the panic prints mid-run). |

## Segfaults (memory-unsafety — one class, ≥3 sub-causes)

~14 dirs (9 SIGSEGV + 5 SIGABRT). Top Rust frames dedup them:

| sub-cause | top frame | modules | likely mechanism |
|-----------|-----------|---------|------------------|
| **RUSTPY-0007a** | `…::hash` / rich-compare | email, json, asyncio ×3, asyncio_tasks | **native stack overflow** — unbounded recursion hashing/comparing a deep/recursive object (fusil's recursive tricky objects); no recursion guard on the native path → SIGSEGV/SIGABRT. CPython raises `RecursionError`. |
| **RUSTPY-0008** | `_sre::Match … AsMapping::as_mapping` | re | **MINIMIZED to 3 lines, SIGSEGV 6/6** → own report. Subscripting an uninitialized `re.Match` (`type(re.match('a','a')).__new__(M)[0]`) reads garbage `regs`/`string`; the mapping subscript path lacks the init guard that `group()`/`repr` have. |
| **RUSTPY-0007c** | `object::core::PyInner` | selectors, asyncio_queues | object-core access on a freed/invalid object (also likely recursion-adjacent). |

**RUSTPY-0008** (promoted from 0007b): `import re; M=type(re.match('a','a')); M.__new__(M)[0]` → SIGSEGV,
deterministic. See `reports/RUSTPY-0008-sre-match-uninitialized-subscript/`.

## Severity note for maintainers

All of these are **reachable from pure Python** and turn a would-be exception into an interpreter
abort/segfault. The two dominant panics need no threads-of-fuzzer-scale to hit — `pwd.struct_passwd().pw_name`
and `_md5.md5()` are one-liners. The segfaults (esp. the recursion → stack-overflow class) are the most
serious since they're memory-unsafety, not a clean panic. Fixes are uniformly "bounds/arity-check and
return a Python error instead of `unwrap()`/`panic!`/indexing", plus a recursion guard on the hash/compare
native paths.

## Prior art (vs the RustPython tracker)

Checked each finding against RustPython/RustPython issues+PRs — see `notes/prior-art-rustpython-tracker.md`.
Summary: **RUSTPY-0003 = #5210** (open) and **RUSTPY-0007a = #2796** (open umbrella) are already tracked;
**RUSTPY-0001** is a distinct, unfixed member of the #7813/#7965 thread-teardown family; **RUSTPY-0004**
(csv) sits in an active-rework area (#8310); **RUSTPY-0002 / 0005 / 0006 / 0008 appear unreported**.
