# RUSTPY-0002 — struct-sequence field getter panics with `index out of bounds` (`structseq.rs:311`)

**Dominant panic (24 vehicles).** A struct-sequence (`os.stat_result`, `pwd.struct_passwd`,
`time.struct_time`, …) whose backing tuple has **fewer elements than its named fields** panics when a
field is read, because the auto-generated getter indexes the tuple with no bounds check.

## Reproducer

```python
import pwd
pwd.struct_passwd().pw_name        # panics
```

```
thread 'main' panicked at crates/vm/src/types/structseq.rs:311:21:
index out of bounds: the len is 0 but the index is 0
```

`pwd.struct_passwd()` **with no arguments constructs an empty struct-sequence** (0 elements); reading any
named field then indexes past the end. (CPython: `pwd.struct_passwd()` raises
`TypeError: struct_passwd() takes ... arguments`.)

## Root cause

`crates/vm/src/types/structseq.rs`, `extend_pyclass` generates a getter per named field:

```rust
class.set_attr(
    ctx.intern_str(name),
    ctx.new_readonly_getset(name, class, move |zelf: &PyTuple| {
        zelf[i as usize].to_owned()          // :311  <-- unchecked index
    })
    .into(),
);
```

`zelf[i]` (`Index` on the backing tuple) panics if the instance's length `< i`. Nothing guarantees the
instance is at least `REQUIRED_FIELD_NAMES.len()` long — the no-argument constructor path produces an
empty tuple, and any shorter-than-fields instance has the same problem.

## Fix sketch

Either (a) reject construction that yields fewer than the required number of fields on **every**
constructor path (the length check at `structseq.rs:149` isn't reached for the no-arg case), or (b) make
the getter bounds-check and return `None`/raise (`zelf.get(i)` → `AttributeError`/`IndexError`) instead of
indexing. CPython guarantees the tuple length equals the field count at construction, so (a) matches its
semantics.

## Impact

Interpreter abort from a one-line pure-Python program. Any code that constructs a struct-sequence type
with the wrong number of elements (or the no-arg form) and reads a field crashes the process instead of
getting a `TypeError`.
