# RUSTPY-0019 — contextvars `RefCell<Hamt>` double-borrows under concurrency (`already mutably borrowed`, `contextvars.rs:82`/`:86`)

**New in fusil-rustpython_09** (the `--concurrency-stress` fleet — a worker-thread panic, reached only under concurrency).

## Reproducer

```python
import contextvars
import threading

# The context-variable map (Hamt) is a RefCell, which is NOT thread-safe. A Context/
# ContextVar shared across threads trips overlapping borrow()/borrow_mut() -> panic.
# CPython's contextvars is thread-safe; RustPython panics in a worker thread.
ctx = contextvars.Context()
var = contextvars.ContextVar('v', default=0)


def worker():
    for i in range(5000):
        try:
            ctx.run(var.set, i)
        except Exception:
            pass
        try:
            var.set(i); var.get()
        except Exception:
            pass


ts = [threading.Thread(target=worker) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
```

CPython: runs clean (contextvars is thread-safe).

## Root cause & fix

The contextvars Hamt (context-variable map) is stored in a RefCell (contextvars.rs), which is !Sync. A Context/ContextVar shared across threads trips overlapping borrow() (:82, borrow_vars) / borrow_mut() (:86, borrow_vars_mut) -- 'RefCell already mutably borrowed' -- and the copy path borrow().clone() (:173) is a third face. Reproduced (worker-thread panic; hit :82 and :86 across runs). CPython's contextvars is thread-safe (runs the same workload clean). SIBLING of RUSTPY-0001 (_thread _local): both are RefCell-used-for-cross-thread-shared-state classes -- RefCell is the wrong primitive for a thread-shareable Python object. Fix = a thread-safe cell (Mutex/RwLock/atomic Hamt). Found by fusil --concurrency-stress (fleet 09), which the single-threaded fleets never hit.

## Prior art

No tracker hit; appears unreported.
