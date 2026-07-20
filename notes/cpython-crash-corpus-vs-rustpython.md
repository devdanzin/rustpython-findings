# Does CPython's crash corpus transfer to RustPython? (negative result)

**Question:** are the reproducers from devdanzin's 138 filed CPython crash issues (`python/cpython`,
`label:type-crash` — the fusil/OOM/TSan/JIT campaign output) a useful seed corpus for finding
RustPython crashes?

**Method:** fetched all 138 issue bodies, extracted every fenced code block that wasn't a C
traceback / crash dump (~210 blocks total across two extraction passes), ran each under RustPython
0.5.0 (`~/.cargo/bin/rustpython`) with guards (`ulimit -v 1500MB`, `-u 200`, `-f 50MB`, `timeout 12`),
and classified the outcome.

**Result — 0 RustPython-native crashes.**

| outcome | count | meaning |
|---|---|---|
| clean-run | 33 | RustPython ran it, exit 0 (CPython crashed, RustPython didn't) |
| py-error | 33 | RustPython raised a clean Python exception (handled the goofy input) |
| import-fail | 24 | needs a CPython-only module RustPython lacks (`_testcapi`, `_opcode`, …) |
| no-repro-block | 4 | umbrella / multi-finding issues with no single repro |
| **abort-oom** | **6** | ballooned to OOM abort — the **already-known** abort-vs-`MemoryError` class |
| hang | 1 | infinite consume (JIT bytecode repro) |
| **PANIC / SEGV** | **0** | — |

The 6 abort-oom are all the known memory class, not new bugs: a huge int
(`10**default_max_str_digits`), a giant `Union` via `reduce(or_, hundreds_of_types)`, thread
accumulation (`_start_joinable_thread` ×200). See
[`unbounded-eager-collect-parity-class.md`](unbounded-eager-collect-parity-class.md) (the
abort-vs-`MemoryError` section, upstream #3493/#1779).

## Why it doesn't transfer (the useful part)

CPython's crash classes and RustPython's are **orthogonal** — they fail in different layers:

- **OOM crashers** (14+) — need `_testcapi.set_nomemory`; RustPython has no such hook (import-fail),
  and its OOM is an abort, not a continuable error path.
- **Free-threading / data-race crashers** (34) — CPython's races live in its C-level *non-atomic*
  refcounting/GC. RustPython uses `PyRc = Arc` (atomic refcounts) + Rust's `Send`/`Sync`, so that
  race class is prevented by construction; the repros ran clean or raised.
- **JIT crashers** (33) — RustPython has no Tier-2 JIT, so the bytecode/uop-specific triggers are
  inert; the Python just executes.
- **Plain C-internal crashers** (NULL deref / C assertion / OOB in C code) — RustPython's internals
  are memory-safe Rust (bounds-checked); the input that corrupts a CPython C struct raises a clean
  Rust-level error or works.

So the goofy input that breaks CPython's **C** layer does not break RustPython's **Rust** layer.
RustPython's crashes come from an unrelated source — `.unwrap()`/`panic!`, unbounded eager-collect,
uninitialized-native-object protocol slots, unguarded native recursion (everything the fusil
campaign actually found, RUSTPY-0001..0016) — none of which the CPython corpus exercises.

## Takeaway

The CPython crash corpus is **not** a productive seed source for RustPython (0/138 native crashes);
the only overlap is the known OOM-balloon class. Fusil's direct hostile-input generation
(`--rustpython` wrong-type bombs, `--new-uninit`, `--concurrency-stress`, native-module targeting)
is the right approach and is where every RustPython finding has come from. *Possible* narrow
exception worth a later try: hand-adapting the CPython **free-threaded re-entrancy** repros
(`__del__` re-entering thread-locals, teardown ordering) toward RustPython's `RefCell`/`BorrowMutError`
surface (the RUSTPY-0001 class) — those share a *logic* shape even though the memory model differs.
