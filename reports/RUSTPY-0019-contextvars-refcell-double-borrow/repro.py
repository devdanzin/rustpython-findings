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
