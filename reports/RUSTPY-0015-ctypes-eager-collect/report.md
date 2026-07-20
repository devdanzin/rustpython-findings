# RUSTPY-0015 — _ctypes Array slice-assign / argtypes eager-collect any iterable (CPython bounds/rejects) -> unbounded memory / OOM abort

**New in the fleet-07 eager-collect audit.** Instance of the systemic **unbounded eager-collect of an untrusted iterable** class — see [`notes/unbounded-eager-collect-parity-class.md`](../../notes/unbounded-eager-collect-parity-class.md) for the full pattern, the sibling instances (RUSTPY-0012..0016), the SAFE sites, and the shared fix.

RustPython collects this argument's iterable whole with no length/type check, so an infinite iterable balloons memory to an OOM abort; CPython rejects it in O(1).

## Reproducer

```python
import ctypes
import itertools

# Array slice-assign collects the RHS iterable whole (extract_elements_with) BEFORE the
# length check (array.rs:977); an infinite iterable balloons to OOM abort.
# CPython: ValueError: Can only assign sequence of same size.
a = (ctypes.c_int * 3)()
a[0:3] = itertools.count()

# Second face: argtypes collected at CALL time (function.rs:1012):
#   f = ctypes.CDLL(None).time; f.argtypes = itertools.count(); f(None)  # -> OOM abort
```

CPython: `ValueError: Can only assign sequence of same size (array slice); argtypes rejected at call`.

## Site

`crates/vm/src/stdlib/_ctypes/array.rs:977` — ctypes Array slice-assignment RHS; PyCFuncPtr.argtypes (collected at call time).

## Root cause & fix

Two faces: (1) Array.__setitem__ slice-assign collects the RHS iterable via extract_elements_with BEFORE the same-size length check (array.rs:977) -> infinite RHS balloons; CPython raises ValueError first. (2) argtypes is collected at call/callback-build time (function.rs:1012/1944), not at attribute-set, so f.argtypes=count(); f(None) balloons. Fix = length/type-check before collecting. Instance of the eager-collect class.

See the class note for the common fix (take a concrete-container type / validate before collecting) and the abort-vs-`MemoryError` distinction.

## Prior art

Unreported (no tracker hit for the pattern; the only near-matches are #8325, the maintainers' own fusil-fuzzing request, and #4210, a general untrusted-code sandboxing RFC). Report the whole class together per the class note.
