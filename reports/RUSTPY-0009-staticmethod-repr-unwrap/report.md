# RUSTPY-0009 — `repr(staticmethod(obj))` panics when the wrapped object's `__repr__` raises (`staticmethod.rs:182`)

**New in fusil-rustpython_02.** `staticmethod`'s `__repr__` calls the wrapped callable's `repr()` and
`.unwrap()`s the result — so if the wrapped object's `__repr__` raises a Python exception, RustPython
`panic!`s instead of propagating it. CPython propagates the exception.

## Reproducer

```python
class R:
    def __repr__(self):
        raise ValueError("boom")
repr(staticmethod(R()))
```

```
thread 'main' panicked at crates/vm/src/builtins/staticmethod.rs:182:54:
called `Result::unwrap()` on an `Err` value: ...
```

CPython: `repr(staticmethod(R()))` raises `ValueError: boom` (the inner exception propagates).

## Root cause

`crates/vm/src/builtins/staticmethod.rs`, `Representable::repr_str`:

```rust
impl Representable for PyStaticMethod {
    fn repr_str(zelf: &Py<Self>, vm: &VirtualMachine) -> PyResult<String> {
        let callable = zelf.callable.lock().repr(vm).unwrap();   // :182  <-- unwraps an Err
        ...
    }
}
```

`repr(vm)` returns `PyResult<..>`; when the wrapped object's `__repr__` raises, it's `Err(exc)`, and
`.unwrap()` turns a normal Python exception into a Rust panic that aborts the interpreter.

## Fix sketch

Propagate the error with `?` instead of `.unwrap()`:

```rust
let callable = zelf.callable.lock().repr(vm)?;
```

so `repr(staticmethod(bad))` raises the inner exception (matching CPython) instead of panicking. Worth a
grep for other `.repr(vm).unwrap()` / `.unwrap()` on `PyResult` in the `Representable`/`__repr__` paths of
other builtins — same anti-pattern.

## Impact

Two-line pure-Python program aborts the interpreter. Any code that `repr()`s (or prints, or logs) a
`staticmethod` wrapping an object whose `__repr__` can raise is exposed — `__repr__` raising is normal
Python (lazy objects, proxies, mocks).

## Prior art

No hit in the RustPython tracker for a staticmethod-repr panic (#7697 "Defer staticmethod/classmethod
callable storage to __init__" is unrelated staticmethod work). Appears unreported. Not covered by a CPython
unittest exercising this exact input.
