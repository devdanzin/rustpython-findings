# RUSTPY-0016 — posix ArgIterable args (posix_spawn argv/setsigdef, setgroups) eager-collect any iterable (CPython requires a sequence) -> unbounded memory

**New in the fleet-07 eager-collect audit.** Instance of the systemic **unbounded eager-collect of an untrusted iterable** class — see [`notes/unbounded-eager-collect-parity-class.md`](../../notes/unbounded-eager-collect-parity-class.md) for the full pattern, the sibling instances (RUSTPY-0012..0016), the SAFE sites, and the shared fix.

RustPython collects this argument's iterable whole with no length/type check, so an infinite iterable balloons memory to an OOM abort; CPython rejects it in O(1).

## Reproducer

```python
import os
import itertools

# posix_spawn argv is an ArgIterable<OsPath> collected in full BEFORE spawning (posix.rs:
# 1403/1525); an infinite generator balloons to OOM abort before any process starts.
# CPython: TypeError: posix_spawn: argv must be a tuple or list.
os.posix_spawn('/bin/true', ('/bin/true' for _ in itertools.count()), os.environ)

# Same class, 'hang' variant (small ints fill slower): os.setgroups(itertools.count())
# and os.posix_spawn('/bin/true', ['/bin/true'], os.environ, setsigdef=itertools.count())
```

CPython: `TypeError: posix_spawn: argv must be a tuple or list (argv); setgroups argument must be a sequence; signal number 0 out of range`.

## Site

`crates/vm/src/stdlib/posix.rs:1403` — os.posix_spawn argv/setsigdef/setsigmask; os.setgroups (all ArgIterable<T>).

## Root cause & fix

The ArgIterable<T> mechanism collects the iterable whole then validates: posix_spawn argv (ArgIterable<OsPath>, posix.rs:1403/1525) balloons to OOM abort BEFORE spawning; setgroups (ArgIterable<RawGid>, :1337), posix_spawn setsigdef/setsigmask (ArgIterable<i32>, :1408/1417) are the same class but fill slower (small ints -> hang/unbounded-consume). CPython requires a real tuple/list/sequence in O(1). Fix = require a concrete container. WARRANTS a follow-up sweep of ALL ArgIterable argument sites. Instance of the eager-collect class.

See the class note for the common fix (take a concrete-container type / validate before collecting) and the abort-vs-`MemoryError` distinction.

## Prior art

Unreported (no tracker hit for the pattern; the only near-matches are #8325, the maintainers' own fusil-fuzzing request, and #4210, a general untrusted-code sandboxing RFC). Report the whole class together per the class note.
