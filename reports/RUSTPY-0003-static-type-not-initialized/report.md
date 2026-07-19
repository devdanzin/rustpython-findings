# RUSTPY-0003 — `static type has not been initialized` panic (`class.rs:87`)

**15 vehicles.** Calling into a native module whose static type cell isn't initialized panics.

## Reproducer
```python
import _md5
_md5.md5()      # panics
```
```
thread 'main' panicked at crates/vm/src/class.rs:87:13:
static type has not been initialized. e.g. the native types defined in different module may be used before importing library.
```

## Root cause
`class.rs` `static_type()` resolves a type's static cell and, when unset, calls a `#[cold] fn fail() -> !`
that **`panic!`s** instead of raising:
```rust
fn static_type() -> &'static Py<PyType> {
    fn fail() -> ! { panic!("static type has not been initialized. ...") }
    Self::static_cell().get().unwrap_or_else(|| fail())   // :87
}
```
`_md5.md5()` reaches a native type (the md5 hash object type) whose static cell was never initialized
(the `_md5` module is importable but doesn't wire up its type), so `static_type()` panics.

## Fix sketch
Ensure `_md5` (and peers) initialize their native types at module init, **and** make `static_type()`'s
failure a Python exception (`RuntimeError`/`SystemError`) rather than a `panic!`, so a missing-init bug
degrades to a catchable error instead of aborting the interpreter.

## Impact
`import _md5; _md5.md5()` — a two-line program — aborts the interpreter. CPython returns an md5 object.
