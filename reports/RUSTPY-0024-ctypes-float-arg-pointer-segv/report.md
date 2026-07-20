# RUSTPY-0024 — ctypes marshals a `float` argument to a pointer for a no-`argtypes` foreign call → SIGSEGV (CPython raises `ArgumentError`) (`_ctypes/function.rs:182`)

**New in fusil-rustpython_09** — gdb-resolved from the `_ios_support-sigsegv` bucket (8 dirs, reliable 6/6). **Single-threaded, deterministic 6/6, portable (libc).**

## Minimal reproducer (SIGSEGV)

```python
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
```

CPython: `ctypes.ArgumentError: argument 1: TypeError: Don't know how to convert parameter 1`.

## Root cause & fix

RustPython's ctypes marshals a Python FLOAT argument to a foreign function that has NO argtypes set, then the no-argtypes FFI dispatch treats it as a pointer-sized value and the callee dereferences a bad address -> SIGSEGV. Root cause: crates/vm/src/stdlib/_ctypes/function.rs conv_param() (the argtypes=None conversion path, :125) has a `// 11. Python float -> f64` branch (:182-188, CArgValue::Double) that CPython has no equivalent of -- CPython's implicit conversion accepts only None/int/bytes/str/ctypes-instances/_as_parameter_ and raises ArgumentError ('Don't know how to convert parameter 1') for a float. Observed mechanism (via libobjc objc_getClass): 0.0 -> NULL (safe), 1.0 -> deref ~0x1 (segv), 3.0 -> deref ~0x3 (segv), 1e300 -> 0 (safe) -- consistent with the float value becoming a small integer pointer address. Portable, single-threaded, deterministic 6/6: `ctypes.CDLL('libc.so.6').strlen(1.5)` segfaults (strlen derefs its char* arg); `libc.atoi(2.5)` too. Fix: remove the float branch from conv_param (raise 'Don't know how to convert' like CPython) so a Double is never dispatched as a pointer. DISTINCT from RUSTPY-0017 (that is a PANIC in ctypes simple-TYPE CONSTRUCTORS on huge ints, e.g. c_char_p(2**64) .expect('int too large'); this is a SEGV in foreign-function ARG MARSHALLING on a float). NOTE: passing a small int (e.g. objc_getClass(12345)) segfaults in BOTH CPython and RustPython -- that is the generic 'int-as-pointer' behavior, NOT this bug. Found by gdb-resolving fleet-09's _ios_support-sigsegv bucket (8 dirs, reliable 6/6): the concurrency-stress op-mix called objc.objc_getClass(<hostile arg>) on the shared libobjc CDLL; single-threaded reduction isolated the float-arg divergence (no concurrency required).

## Evidence table (arg → outcome)

| arg to a `char*` foreign func (no argtypes) | CPython | RustPython |
|---|---|---|
| `b'UIDevice'` (bytes) | ok | ok |
| `'UIDevice'` (str) | ok | ok |
| `None` | ok (NULL) | ok (NULL) |
| `12345` (small int) | **SEGV** | **SEGV** (shared int-as-pointer behavior — not this bug) |
| `2**60` (big int) | ok | ok |
| `bytearray`/`list`/`dict` | ArgumentError | TypeError (both reject) |
| **`1.5` (float)** | **ArgumentError** | **SEGV** ← the divergence |

## gdb backtrace (fleet vehicle, `_ios_support`)

```
Thread N received signal SIGSEGV
#0  objc_getClass ()                         from libobjc.so.4
#1-3 ffi_call ...                            from libffi.so.8
#4  rustpython_host_env::ctypes::call::{closure#3}
#5  rustpython_host_env::ctypes::call
#6  <_ctypes::function::PyCFuncPtr as Callable>::call
```

## Prior art

_To check vs the RustPython tracker (ctypes float argument / conv_param / argtypes=None)._ Appears unreported. Distinct from RUSTPY-0017.
