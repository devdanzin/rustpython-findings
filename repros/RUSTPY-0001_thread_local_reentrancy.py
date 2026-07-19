import _thread, threading

loc = _thread._local()          # GLOBAL: survives thread exit, so the guard's weak-upgrade
                                # succeeds during cleanup and drops the stored value THEN.
class Bad:
    def __del__(self):
        try:
            L = _thread._local() # fresh _local -> Vacant entry -> registers a new guard (push @977)
            L.y = 1              # ...while cleanup_thread_local_data holds LOCAL_GUARDS.borrow_mut()
        except BaseException:
            pass

def worker():
    loc.x = Bad()               # per-thread dict entry on the GLOBAL loc, holding Bad()

for _ in range(500):
    t = threading.Thread(target=worker)
    t.start(); t.join()
print("done, no panic")
