# RUSTPY-0012: _suggestions._generate_suggestions eagerly collects any iterable into a Vec
# (CPython requires a list: "candidates must be a list"). An infinite iterable then balloons
# memory unboundedly until the allocator aborts. No concurrency needed -- a direct call does it.
import _suggestions
import itertools

_suggestions._generate_suggestions(itertools.count(), "x")   # ~1 GiB/s -> OOM abort
