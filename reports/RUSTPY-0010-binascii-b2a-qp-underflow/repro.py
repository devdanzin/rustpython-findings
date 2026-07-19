# RUSTPY-0010: binascii.b2a_qp panics with an index underflow when the input starts with '\n'.
# The newline-scan loop leaves in_idx == 0, then buf[in_idx - 1] underflows to usize::MAX (OOB).
# CPython returns b'\n'; RustPython panics at crates/stdlib/src/binascii.rs:507.
import binascii

binascii.b2a_qp(b'\n')
