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
| **RUSTPY-0009** | `builtins/staticmethod.rs:182` `unwrap()` on `Err` | `repro.py` — `repr(staticmethod(obj))` where `obj.__repr__` raises | 1 | `staticmethod.__repr__` calls the wrapped object's `repr()` and **`.unwrap()`s it** — a raising `__repr__` panics instead of propagating. CPython raises the inner exception. (fleet_02) |
| **RUSTPY-0010** | `stdlib/src/binascii.rs:507` `index out of bounds` | `import binascii; binascii.b2a_qp(b'\n')` | 1 | `b2a_qp`'s newline-scan loop leaves `in_idx == 0` when the first byte is `\n`, then `buf[in_idx - 1]` underflows to `usize::MAX` (OOB). Guard needs `in_idx > 0`. CPython returns `b'\n'`. Also via `quopri.encodestring`. (fleet_03) |
| **RUSTPY-0011** | `builtins/classmethod.rs:198` `unwrap()` on `Err` | `repro.py` — `repr(classmethod(obj))` where `obj.__repr__` raises | 1 | **Exact sibling of RUSTPY-0009**, one type over: `classmethod.__repr__` does `.repr(vm).unwrap()` — a raising `__repr__` panics instead of propagating. CPython raises the inner exception. Report both as one `.repr(vm).unwrap()` fix. (fleet_05) |

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

## fusil-rustpython_02 (second fleet)

213 kept dirs. Mostly re-finds of RUSTPY-0001..0006 (structseq ×56, static-type ×34, _thread ×10,
_typing ×7, csv ×6, surrogates ×1). **New: RUSTPY-0009** (staticmethod repr unwrap). **New csv faces**
folded into RUSTPY-0004: `csv.rs:748` (`_csv.writer(io.StringIO())` → excel-dialect unwrap, 5 veh) and
`csv.rs:1070` (1 veh). **Many more memory crashes** (42 SIGABRT + 22 SIGSEGV) — all the **RUSTPY-0007a
recursion → native stack-overflow class** (#2796); specific native sites seen include
`genericalias::make_parameters_from_slice` and hash/compare (fusil's cyclic/recursive object graphs slip
past RustPython's recursion guards).

## fusil-rustpython_03 (third fleet)

93 crash dirs across 4 instances (no dedup catalog — triaged with `fusil.python.rustpython_dedup`).
**56 panics: all known except one NEW — RUSTPY-0010** (`binascii.b2a_qp` underflow). Known-panic
tally: RUSTPY-0002 ×30 (structseq OOB — **new module faces**: `grp.struct_group()`,
`resource.struct_rusage()`, `posix`/`os` struct-seqs, plus `pwd`), RUSTPY-0003 ×15 (static-type
not initialized — the hash modules `_md5`/`_sha1`/`_sha3`/`_blake2`/`_sha2` all fold here),
RUSTPY-0005 ×3, RUSTPY-0001 ×3, RUSTPY-0004 ×2 (`csv.rs:748`+`:805`), RUSTPY-0006 ×1, RUSTPY-0009 ×1.

**37 no-panic (segv/abort):** RUSTPY-0008 ×5 (`re.Match` subscript), RUSTPY-0007a recursion →
SIGSEGV ×11 (asyncio/importlib/_pyio object graphs), a **huge-allocation abort class** ×11
(`memory allocation of N bytes failed` → Rust `handle_alloc_error` abort on an unchecked
allocation size from arithmetic on fuzzer values; CPython raises `MemoryError`/`OverflowError` —
a robustness gap, uncatchable abort vs catchable exception, not yet minted), 5 fuzzer artifacts
(SIGINT via the `signal` module), 1 SIGKILL timeout, and 4 unlabeled `session-NNN` (hang/timeout
artifacts: `pty.open_terminal`, GC-teardown on email/json/encodings — no distinct crash).

Takeaways: the dedup catalog + `--modules-file` targeting from the new tooling would collapse this
to "RUSTPY-0010 + huge-alloc-abort class" instead of 93 raw dirs. The structseq (0002) and
static-type (0003) bugs are confirmed to span many more modules than the original repro suggested.

## fusil-rustpython_05 + _06 (fleets 4–5, WITH dedup catalog + --modules-file)

First fleets run with the new tooling (in-loop dedup + native-module targeting). Dedup collapsed
them hard: **fleet_05 (85 dirs) = 1 NEW panic (RUSTPY-0011, classmethod repr), rest known;
fleet_06 (124 dirs) = 0 new panics** (every panic was RUSTPY-0001..0005). Known-panic mix (both
fleets): structseq 0002, static-type 0003 (hash modules), csv 0004 (`:748`+`:805`), _thread 0001,
_typing 0005. Segfaults: re.Match 0008 (4+10), recursion 0007a SIGSEGV (2+7).

**Memory-balloon signal:** 14 (_05) + 24 (_06) = **38 `sigterm` dirs** — RustPython ballooning
~400 MiB/s to 15+ GiB on hostile input (a runaway thread's fuzzed call allocating unboundedly,
abandoned by the generated `join(timeout=1)`), killed by the cgroup cap or a peer `killall`.
**Fixed** by fusil `--child-memory-limit-mb 2048` (PR #228, merged): a real `RLIMIT_AS` makes the
child abort at ~1.15 GiB in ~6 s (`memory allocation of N bytes failed`) instead of swap-filling.
A few genuine `huge-alloc abort` dirs (a single op computing a giant size where CPython raises
`MemoryError`) hide in that class — not yet minted.

Takeaway: the campaign has converged. New panic sites are now rare (1 in 209 dirs across both
fleets); the remaining yield is the segfault classes (0007a recursion, 0008 uninitialized objects)
and the un-minted huge-alloc-abort robustness class. The `--new-uninit` / `--concurrency-stress`
variant fleets target the segfault/threading surface the primary mode under-exercises.

## fusil-rustpython_07 (memory-balloon class isolated)

**New: RUSTPY-0012** — `_suggestions._generate_suggestions` eager-collects any iterable into a `Vec`
(CPython requires a `list`: `TypeError: candidates must be a list`), so an **infinite iterable**
(`itertools.count()`, or an object with a non-terminating `__getitem__` and no `__iter__`) balloons
memory unboundedly (~1 GiB/s, measured 5.5 GiB in 5 s) until an OOM abort — **no concurrency needed**,
a single direct call does it. This is a **distinct memory class**: not the recursion→stack-overflow
(0007a), not a single huge allocation, and not the runaway-abandoned-thread balloon (fixed by fusil
`--child-memory-limit-mb`). It surfaces as `rustpySEGV` / `memory allocation of N bytes failed` (no
panic line). Root: `candidates: Vec<PyObjectRef>` (`suggestions.rs:11`) — the `: Vec<PyObjectRef>`
eager-collect-untrusted-iterable pattern recurs at ~60 sites and is worth an audit. Fix = take
`PyListRef` (CPython parity + bounded).
