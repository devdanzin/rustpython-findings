import _thread, threading
class Bad:
    def __del__(self):
        try:
            L = _thread._local()
            L.y = 1          # re-enter _local guard registration during teardown
            _ = L.__dict__
        except BaseException:
            pass
def worker():
    loc = _thread._local()
    loc.x = Bad()            # hostile value held in this thread's local
for _ in range(3000):
    t = threading.Thread(target=worker)
    t.start(); t.join()
print("done")
