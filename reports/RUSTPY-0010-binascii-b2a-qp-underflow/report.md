# RUSTPY-0010 — `binascii.b2a_qp` panics with an index underflow on input starting with `\n` (`binascii.rs:507`)

**New in fusil-rustpython_03.** `binascii.b2a_qp` scans for the first newline; when the input's
first byte **is** `\n` the scan index stays `0`, and the very next check reads `buf[in_idx - 1]`
with `in_idx == 0` — a `usize` underflow to `usize::MAX` → out-of-bounds index panic. CPython
returns the input unchanged.

## Reproducer

```python
import binascii
binascii.b2a_qp(b'\n')
```

```
thread 'main' panicked at crates/stdlib/src/binascii.rs:507:49:
index out of bounds: the len is 8 but the index is 18446744073709551615
```

`18446744073709551615` is `usize::MAX` (= `0 - 1`). CPython: `binascii.b2a_qp(b'\n')` → `b'\n'`.
Also reachable through the stdlib wrapper `quopri.encodestring(b'\n...')` and thus the
`quopri_codec` text encoding (the fleet vehicle was `encodings.quopri_codec.quopri_encode()`).

## Root cause

`crates/stdlib/src/binascii.rs`, `b2a_qp` (the `#[pyfunction]` at `:484`):

```rust
in_idx = 0;
while in_idx < buflen {
    if buf[in_idx] == b'\n' {
        break;                     // first byte is '\n' -> break with in_idx == 0
    }
    in_idx += 1;
}
if buflen > 0 && in_idx < buflen && buf[in_idx - 1] == b'\r' {   // :507  buf[0 - 1] = buf[usize::MAX]
    crlf = true;
}
```

The guard checks `in_idx < buflen` (upper bound) but not `in_idx > 0` (lower bound). When the
buffer begins with `\n`, the loop breaks immediately with `in_idx == 0`, and `buf[in_idx - 1]`
underflows. This "detect a trailing `\r` before the newline" check simply has no meaning when the
newline is the first byte.

## Fix sketch

Guard the underflow:

```rust
if buflen > 0 && in_idx > 0 && in_idx < buflen && buf[in_idx - 1] == b'\r' {
```

(equivalently `in_idx != 0`). With `in_idx == 0` there is no preceding byte, so `crlf` stays
false — matching CPython, which treats a leading `\n` as a bare LF.

## Impact

A two-token pure-Python program (`binascii.b2a_qp(b'\n')`) aborts the interpreter. The trigger is
common: **any** input to `b2a_qp` (or `quopri.encodestring`) whose first byte is a newline — e.g.
encoding text that starts with a blank line. Uncatchable (a Rust panic → abort), where CPython
raises nothing at all.

## Prior art

_To check against the RustPython tracker (binascii / b2a_qp / quopri index panic)._ Appears
unreported. Not covered by a CPython unittest exercising this exact input (leading-`\n` b2a_qp).
