# RUSTPY-0017 — `ctypes` simple int/pointer types panic on an out-of-range int (`.expect("int too large…")` in `_ctypes/simple.rs`)

**New in fusil-rustpython_08** (the first pure-Python-modules fleet — reached ctypes via the
`c_char_p` wrapper). Constructing a ctypes simple numeric or pointer type with an integer that
doesn't fit its Rust backing type panics: the conversion `.to_i128()` / `.to_usize()` returns
`None` and the code `.expect("int too large…")`s it. CPython **masks** the value to the type's
width (no error).

## Reproducer

```python
import ctypes
ctypes.c_char_p(2**64)     # panics at _ctypes/simple.rs:908
```

```
thread 'main' panicked at crates/vm/src/stdlib/_ctypes/simple.rs:908:22:
int too large for pointer
```

CPython: `ctypes.c_char_p(2**64)` → `c_char_p(None)` (the address masks to 0). Deterministic (5/5).

## The class (multiple sibling faces, same root)

Every simple int / pointer type has its own `.expect(...)` on the width conversion — all panic on an
out-of-range int, all masked by CPython:

| Python | RustPython site | conversion | CPython |
|--------|-----------------|------------|---------|
| `ctypes.c_byte(2**200)`  | `simple.rs:759` | `to_i128().expect("int too large")` | `c_byte(0)` |
| `ctypes.c_short(2**200)` | `simple.rs:775` | `to_i128().expect(...)` | `c_short(0)` |
| `ctypes.c_int(2**200)`   | `simple.rs:791` | `to_i128().expect(...)` | `c_int(0)` |
| `ctypes.c_long/c_longlong(2**200)` | `simple.rs:807` | `to_i128().expect(...)` | `c_long(0)` |
| `ctypes.c_void_p(2**64)`  | `simple.rs:895` | `to_usize().expect("int too large for pointer")` | `c_void_p(None)` |
| `ctypes.c_char_p(2**64)`  | `simple.rs:908` | `to_usize().expect(...)` | `c_char_p(None)` |
| `ctypes.c_wchar_p(2**64)` | `simple.rs:921` | `to_usize().expect(...)` | `c_wchar_p(None)` |

(the unsigned int types have their own adjacent `to_i128().expect("int too large")` sites at
`:767/783/799` — same pattern.)

## Root cause & fix

`crates/vm/src/stdlib/_ctypes/simple.rs`, e.g. the `"z"` (c_char_p) arm:

```rust
if let Ok(int_val) = value.try_index(vm) {
    let v = int_val.as_bigint().to_usize()
        .expect("int too large for pointer");   // :908  <-- panics on > usize::MAX
    SimpleStorageValue::Pointer(v)
}
```

The int is already accepted (`try_index` succeeds); only the width conversion fails, and `.expect()`
turns that into an interpreter abort. CPython instead **truncates/masks** the value to the C type's
width (`c_int(2**200)` → `0`, `c_char_p(2**64)` → `None`). Fix: mask (`as` / `& mask` /
`to_usize().unwrap_or_else(|| wrap...)`) to match CPython, or at worst raise `OverflowError` — never
`.expect()`. A single sweep of the `.to_i128()/.to_usize().expect(...)` sites in this file fixes the
whole class.

## Impact

A one-line pure-Python `ctypes.c_int(2**200)` (or any simple type with an out-of-range int) aborts
the interpreter — trivially reachable wherever user data flows into a `ctypes` scalar.

## Prior art

No tracker hit for a ctypes int-too-large / `c_char_p` panic (the ctypes issues #6450 "ctypes
overhaul", #8235 hostenv, etc. are closed refactors). Appears unreported.
