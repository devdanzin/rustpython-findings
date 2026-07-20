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
