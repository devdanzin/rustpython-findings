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
