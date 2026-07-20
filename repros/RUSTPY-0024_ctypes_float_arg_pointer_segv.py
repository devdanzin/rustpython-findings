# RUSTPY-0024 (SIGSEGV, single-threaded, deterministic 6/6). RustPython's ctypes accepts a Python
# FLOAT as an argument to a foreign function that has no `argtypes` set, and the no-argtypes FFI
# dispatch treats it as a pointer-sized value -> the callee dereferences a bad address -> SIGSEGV.
# CPython raises `ctypes.ArgumentError: argument 1: TypeError: Don't know how to convert parameter 1`
# (its implicit conversion accepts only None/int/bytes/str/ctypes-instances/_as_parameter_, never float).
#
# Root cause: crates/vm/src/stdlib/_ctypes/function.rs, conv_param() (the argtypes=None path) has a
# `// 11. Python float -> f64` branch (:182-188) that CPython has no equivalent of. Fix: drop it
# (raise "Don't know how to convert" for float when argtypes is None), matching CPython.
#
# Portable: libc.so.6 is always present; strlen dereferences its char* argument. objc_getClass(1.0)
# in the _ios_support fleet vehicle is the same bug (float 1.0 -> address ~0x1 -> deref -> segv;
# 0.0 -> NULL is safe). Small ints (e.g. 12345) segfault in BOTH interpreters -- that is NOT this
# bug (ctypes lets you pass an int as a pointer everywhere); the float path is RustPython-specific.
import ctypes

libc = ctypes.CDLL("libc.so.6")
libc.strlen(1.5)   # float -> pointer -> strlen derefs it -> SIGSEGV (CPython: ArgumentError)
