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
- **`reports/RUSTPY-00NN-<slug>/`** — per-crash `report.md` (root cause + fix sketch), `repro.py`,
  and `meta.json` (structured, canonical: `id`, `kind`, `signatures`, `one_line_repro`, …).
- **`repros/`** — the minimal reproducers, one file each.
- **`catalog/known_panics.tsv`** — flat dedupe snapshot (`<bug_id>\t<signature>`) generated from the
  `meta.json` files; consumed by fusil's in-loop deduper.
- **`scripts/gen_known_panics.py`** — regenerates the catalog from `reports/*/meta.json`.

## Dedup catalog (in-loop, for fleets)

`meta.json` is the canonical per-finding data; `catalog/known_panics.tsv` is the flat snapshot fusil
loads to dedupe crashes as they happen. Signatures are fusil `rustpython_dedup` panic-site keys —
`crates/<path>.rs:<line>` (column dropped, absolute checkout paths normalised to the `crates/` tail).
Segfault findings (`kind: "segv"`, e.g. RUSTPY-0007/-0008) carry no panic signature — they surface as
`rustpySEGV` and need gdb resolution — so they contribute no rows.

```sh
python3 scripts/gen_known_panics.py     # reports/*/meta.json -> catalog/known_panics.tsv
```

Point a fleet at the snapshot so it labels each crash dir with its bug id (`RUSTPY-00NN`) and keeps
only new panic sites (`rustpyNEW`) / segfaults (`rustpySEGV`):

```sh
fusil-python-threaded --python /path/to/rustpython \
    --rustpython-dedup-catalog catalog/known_panics.tsv --rustpython-dedup-prune ...
```

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
