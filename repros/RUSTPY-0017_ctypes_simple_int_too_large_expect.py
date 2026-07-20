# RUSTPY-0017: ctypes simple int/pointer types panic on an out-of-range int via
# .to_i128()/.to_usize().expect("int too large...") in _ctypes/simple.rs. CPython MASKS the value
# (c_char_p(2**64) -> c_char_p(None), c_int(2**200) -> c_int(0)); RustPython aborts.
import ctypes

ctypes.c_char_p(2**64)   # simple.rs:908 ; also c_int(2**200)=:791, c_void_p(2**64)=:895, etc.
