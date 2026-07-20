# RUSTPY-0022 — itertools.cycle indexes its cache out of bounds under concurrency (`saved[index.fetch_add(1)]` races a non-atomic reset, itertools.rs:282)

**New in fusil-rustpython_09** (the `--concurrency-stress` fleet). **concurrency-triggered**. Reliability: reliable 8/8 (8 threads).

## Reproducer

```python
# RUSTPY-0022 (panic, concurrency). itertools.cycle's IterNext (itertools.rs:282) indexes its
# cached `saved` vec with `saved[index.fetch_add(1)]`, resetting `index` to 0 in a SEPARATE,
# non-atomic step (`if last_index >= saved.len()-1 { index.store(0) }`). Two threads racing the
# fetch_add past the reset boundary read out of bounds: with a 1-element cycle, one thread gets
# last_index==1 into a len-1 vec -> `index out of bounds: the len is 1 but the index is 1` -> panic.
# A shared-iterator TOCTOU, sibling of RUSTPY-0020. CPython's cycle never crashes (thread-safe).
#
# Reliable: 8/8 with 8 threads.
import itertools
import threading

c = itertools.cycle([0])   # single element -> saved.len() == 1, reset boundary is index 0
next(c)                    # first pass: fill `saved` and exhaust the underlying iterator


def worker():
    for _ in range(200000):
        try:
            next(c)
        except Exception:
            pass


ts = [threading.Thread(target=worker) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
```

CPython: never crashes -- cycle keeps returning the cached items (thread-safe under the GIL, and guarded under free-threading).

## Root cause & fix

PyItertoolsCycle::next (itertools.rs:266-286): once the underlying iterator is exhausted it cycles the cached `saved` vec via `let last_index = zelf.index.fetch_add(1); if last_index >= saved.len()-1 { zelf.index.store(0); } saved[last_index].clone()` (:278-282). The fetch_add and the reset store are separate, non-atomic steps, so concurrent next() calls race: two threads both fetch_add before either resets, and one gets a last_index past the end -> `saved[last_index]` panics 'index out of bounds'. With a 1-element cycle (saved.len()==1, reset boundary 0) it is trivially hit (len is 1 but the index is 1). CPython's cycle never crashes. Read-while-advance TOCTOU on a shared iterator -- SIBLING of RUSTPY-0020 (collection_repr) and the shared-iterator class generally; distinct site/crash (OOB index vs empty .expect()). Fix: make the index-advance-and-wrap a single atomic step (e.g. compute `(i+1) % len` and CAS), or bounds-check before indexing. Found in fusil-rustpython_09 (--concurrency-stress).
