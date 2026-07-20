# RUSTPY-0016: os.posix_spawn argv/setsigdef/setsigmask; os.setgroups (all ArgIterable<T>)
import os
import itertools

# posix_spawn argv is an ArgIterable<OsPath> collected in full BEFORE spawning (posix.rs:
# 1403/1525); an infinite generator balloons to OOM abort before any process starts.
# CPython: TypeError: posix_spawn: argv must be a tuple or list.
os.posix_spawn('/bin/true', ('/bin/true' for _ in itertools.count()), os.environ)

# Same class, 'hang' variant (small ints fill slower): os.setgroups(itertools.count())
# and os.posix_spawn('/bin/true', ['/bin/true'], os.environ, setsigdef=itertools.count())
