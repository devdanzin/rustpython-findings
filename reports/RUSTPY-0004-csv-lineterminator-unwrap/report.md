# RUSTPY-0004 — `csv` `get_lineterminator` unwraps `None` (`csv.rs:805`)

**7 vehicles.** Creating/using a `_csv.reader` whose dialect name isn't in the global dialect map panics.

## Reproducer
```python
import _csv
_csv.reader([]).__next__()     # panics
# also: import _csv, io; _csv.reader(io.StringIO('a')).dialect
```
```
thread '<unnamed>' panicked at crates/stdlib/src/csv.rs:805:42:
called `Option::unwrap()` on a `None` value
```

## Root cause
`crates/stdlib/src/csv.rs` `get_lineterminator` looks the dialect up in a global map by name and later
`unwrap()`s an `Option` that is `None` when the dialect isn't registered / the reader has no resolved
dialect (`GLOBAL_HASHMAP.lock(); g.get(name)` path around `:798-805`). The `unwrap()` at `:805:42` aborts.

## Fix sketch
Replace the `unwrap()` with a fallback (`Terminator::CRLF`, which the `else` branch already uses) or raise
`_csv.Error`, so a missing/unknown dialect is a Python error rather than a panic.

## Impact
Iterating a freshly-made `_csv.reader` (or reading `.dialect`) can abort the interpreter.

## More faces (fusil-rustpython_02)

The same "csv `unwrap()`s a missing dialect" pattern has other unguarded sites:

- **`csv.rs:748`** — `import _csv, io; _csv.writer(io.StringIO())` panics on `*g.get("excel").unwrap()`
  (the default `DialectItem::None` branch looks up the "excel" dialect in the global map and unwraps).
  This is the **writer-creation** face: making a basic csv writer with no explicit dialect aborts the
  interpreter (5 vehicles). `repro_writer_excel.py`.
- **`csv.rs:1070`** — a reader `__next__` path (1 vehicle).

Same fix class: fall back to a default or raise `_csv.Error` instead of `unwrap()`. The csv module is
under active rework upstream (RustPython #8310), so these may be addressed there.
