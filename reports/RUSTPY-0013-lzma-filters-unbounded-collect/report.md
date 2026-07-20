# RUSTPY-0013 — lzma filters= eager-collects any iterable (CPython requires a sized sequence) -> unbounded memory / OOM abort

**New in the fleet-07 eager-collect audit.** Instance of the systemic **unbounded eager-collect of an untrusted iterable** class — see [`notes/unbounded-eager-collect-parity-class.md`](../../notes/unbounded-eager-collect-parity-class.md) for the full pattern, the sibling instances (RUSTPY-0012..0016), the SAFE sites, and the shared fix.

RustPython collects this argument's iterable whole with no length/type check, so an infinite iterable balloons memory to an OOM abort; CPython rejects it in O(1).

## Reproducer

```python
import lzma
import itertools

# filters= is collected into a Vec<PyObjectRef> (parse_filter_chain_spec, lzma.rs:340)
# before validation; an infinite generator balloons memory to OOM abort.
# CPython: TypeError: object of type 'generator' has no len().
lzma.LZMACompressor(format=lzma.FORMAT_RAW,
                    filters=({'id': lzma.FILTER_LZMA2} for _ in itertools.count()))
```

CPython: `TypeError: object of type 'generator' has no len()`.

## Site

`crates/stdlib/src/lzma.rs:340` — lzma.LZMACompressor / LZMADecompressor filters= argument.

## Root cause & fix

LZMACompressor/LZMADecompressor collect the user's filters= iterable whole (via parse_filter_chain_spec's filter_specs: Vec<PyObjectRef>) before validating; an infinite/huge iterable balloons to OOM abort. CPython calls len() first (requires a sized sequence). Fix = require a list/tuple. Instance of the eager-collect class.

See the class note for the common fix (take a concrete-container type / validate before collecting) and the abort-vs-`MemoryError` distinction.

## Prior art

Unreported (no tracker hit for the pattern; the only near-matches are #8325, the maintainers' own fusil-fuzzing request, and #4210, a general untrusted-code sandboxing RFC). Report the whole class together per the class note.
