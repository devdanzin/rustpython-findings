# RUSTPY-0011 — `repr(classmethod(obj))` panics when the wrapped object's `__repr__` raises (`classmethod.rs:198`)

**New in fusil-rustpython_05.** The exact sibling of RUSTPY-0009 (staticmethod), one type over:
`classmethod`'s `__repr__` calls the wrapped callable's `repr()` and `.unwrap()`s the result, so a
wrapped object whose `__repr__` raises makes RustPython `panic!` instead of propagating the
exception. CPython propagates it.

## Reproducer

```python
class R:
    def __repr__(self):
        raise ValueError("boom")
repr(classmethod(R()))
```

```
thread 'main' panicked at crates/vm/src/builtins/classmethod.rs:198:54:
called `Result::unwrap()` on an `Err` value: ...
```

CPython: `repr(classmethod(R()))` raises `ValueError: boom` (the inner exception propagates).
Deterministic (5/5).

## Root cause

`crates/vm/src/builtins/classmethod.rs`, `Representable::repr_str`:

```rust
impl Representable for PyClassMethod {
    fn repr_str(zelf: &Py<Self>, vm: &VirtualMachine) -> PyResult<String> {
        let callable = zelf.callable.lock().repr(vm).unwrap();   // :198  <-- unwraps an Err
        ...
    }
}
```

`repr(vm)` returns `PyResult<..>`; when the wrapped object's `__repr__` raises it is `Err(exc)`, and
`.unwrap()` turns a normal Python exception into a Rust panic that aborts the interpreter. This is
byte-for-byte the RUSTPY-0009 anti-pattern in `staticmethod.rs:182`.

## Fix sketch

Propagate with `?` instead of `.unwrap()`:

```rust
let callable = zelf.callable.lock().repr(vm)?;
```

so `repr(classmethod(bad))` raises the inner exception (matching CPython). Both this and RUSTPY-0009
are the same bug; a grep for `.repr(vm).unwrap()` (and `.repr(vm)` followed by `.unwrap()`) across the
`Representable` / `__repr__` impls of other builtins would likely surface more of the same.

## Impact

A two-line pure-Python program aborts the interpreter. Any code that `repr()`s (prints, logs,
f-strings) a `classmethod` wrapping an object whose `__repr__` can raise is exposed — a raising
`__repr__` is ordinary Python (lazy objects, proxies, mocks).

## Prior art

No hit in the RustPython tracker for a classmethod-repr panic (#7697 "Defer staticmethod/classmethod
callable storage to __init__" is unrelated storage work — the same false lead noted for RUSTPY-0009).
Appears unreported. Not covered by a CPython unittest exercising this exact input. **Report it
together with RUSTPY-0009** as one `.repr(vm).unwrap()` fix across `staticmethod.rs` + `classmethod.rs`.
