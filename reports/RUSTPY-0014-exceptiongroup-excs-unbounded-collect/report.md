# RUSTPY-0014 — ExceptionGroup(msg, excs) eager-collects any iterable (CPython requires a sequence) -> unbounded memory / OOM abort

**New in the fleet-07 eager-collect audit.** Instance of the systemic **unbounded eager-collect of an untrusted iterable** class — see [`notes/unbounded-eager-collect-parity-class.md`](../../notes/unbounded-eager-collect-parity-class.md) for the full pattern, the sibling instances (RUSTPY-0012..0016), the SAFE sites, and the shared fix.

RustPython collects this argument's iterable whole with no length/type check, so an infinite iterable balloons memory to an OOM abort; CPython rejects it in O(1).

## Reproducer

```python
import itertools

# The (Base)ExceptionGroup constructor collects the second (exceptions) argument whole
# by iterating it; an infinite iterable balloons memory (~1.1 GiB/s) to OOM abort.
# CPython: TypeError: second argument (exceptions) must be a sequence.
ExceptionGroup('m', itertools.count())
```

CPython: `TypeError: second argument (exceptions) must be a sequence`.

## Site

`crates/vm/src/exception_group.rs:448` — ExceptionGroup / BaseExceptionGroup second (exceptions) argument.

## Root cause & fix

The (Base)ExceptionGroup Constructor eager-collects the excs argument (Vec<PyObjectRef>, helper derive_and_copy_attributes at :448); an infinite iterable balloons ~1.1 GiB/s to OOM abort (measured 5.6 GiB in 5s). CPython requires a sequence. Fix = require a list/tuple/sized sequence. Instance of the eager-collect class.

See the class note for the common fix (take a concrete-container type / validate before collecting) and the abort-vs-`MemoryError` distinction.

## Prior art

Unreported (no tracker hit for the pattern; the only near-matches are #8325, the maintainers' own fusil-fuzzing request, and #4210, a general untrusted-code sandboxing RFC). Report the whole class together per the class note.
