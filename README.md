# rustpython-findings

Crashes in [RustPython](https://github.com/RustPython/RustPython) found by fuzzing it with
[**fusil**](https://github.com/devdanzin/fusil) (a Python fuzzer that generates hostile scripts —
tricky/weird objects, wrong-arity and wrong-type calls, surrogate strings, deep/recursive structures,
threads — and runs them under a target interpreter, watching for crashes).

These are **plain crashes** (Rust `panic!` / `unwrap` / bounds-check failures, and native segfaults) —
**no ThreadSanitizer, no allocation-failure injection**. Every one is a case where a Python-level
program makes the interpreter **abort or segfault** instead of raising a Python exception. In CPython
each of these raises (`TypeError`, `ValueError`, `SyntaxError`, `RecursionError`) or works.

Found in the first fusil run against RustPython (`fusil-rustpython_01`, 4 instances). fusil drives
RustPython `0.5.0` (reports `Python 3.14.0alpha`, `sys.implementation.name == 'rustpython'`) via
`--python`; the fuzzer itself runs under CPython.

## Layout

- **`INDEX.md`** — the status board / bug sample: every distinct crash, its panic site, a one-line
  reproducer, vehicle count, and severity.
- **`reports/RUSTPY-00NN-<slug>/`** — per-crash `report.md` (root cause + fix sketch) + `repro.py`.
- **`repros/`** — the minimal reproducers, one file each.

## How to reproduce

```sh
rustpython repros/RUSTPY-0002_structseq_oob.py     # each is a one- or few-line script
```

Panics print `thread '…' panicked at crates/…/<file>.rs:<line>:<col>: <message>` and exit non-zero;
segfaults dump core. Deduplication key = the panic **site** (`file.rs:line`), or for segfaults the top
non-libc Rust frame.

## Counting

**6 distinct panics** + a **segfault class** across ~226 kept crash dirs. The two dominant panics
(`_thread` RefCell double-borrow, `structseq` field-getter OOB) account for the bulk; one is rare
(surrogate strings). Every panic here has a minimal reproducer that fits on one line, except the
`_thread` one (a thread-teardown re-entrancy, reproduced by the fuzzer vehicle + root-caused from source).
