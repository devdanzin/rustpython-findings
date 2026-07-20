# RUSTPY-0020 — `collection_repr` `.expect("this is not called for empty collection")` panics when a shared collection is emptied mid-repr (`utils.rs:61`)

**New in fusil-rustpython_09** (the `--concurrency-stress` fleet — a worker-thread panic, reached only under concurrency).

## Reproducer

```python
import threading

# collection_repr (repr of a set/dict) does iter.next().expect('this is not called for
# empty collection') after checking non-empty. A concurrent thread emptying the shared
# collection between the check and the iteration makes next() -> None -> panic.
# CPython raises RuntimeError (changed size) at worst -- never crashes.
shared = {1, 2, 3, 4, 5}


def mutate():
    for _ in range(100000):
        try:
            shared.clear(); shared.update({1, 2, 3})
        except Exception:
            pass


def reader():
    for _ in range(100000):
        try:
            repr(shared)
        except Exception:
            pass


ts = [threading.Thread(target=mutate) for _ in range(4)] + \
     [threading.Thread(target=reader) for _ in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
```

CPython: runs clean (raises RuntimeError 'changed size during iteration' at worst, no crash).

## Root cause & fix

collection_repr (vm/src/utils.rs) formats a collection's items: after establishing the collection is non-empty it does `iter.next()...expect("this is not called for empty collection")`. A concurrent thread clearing the shared set/dict between the non-empty check and the iteration makes next() return None -> the .expect() panics (read-while-mutate TOCTOU). Reproduced 3/3 with 4 mutator + 4 reader threads on a shared set. CPython raises RuntimeError ('Set changed size during iteration') at worst -- never crashes. Fix = handle the empty/None case instead of .expect() (fall back to the empty-collection repr). Found by fusil --concurrency-stress (fleet 09).

## Prior art

No tracker hit; appears unreported.
