# RUSTPY-0023 (panic, RARE race -- NO reliable reproducer). A Python generator's frame value
# stack underflows under concurrent resumption: builtins.next -> PyGenerator::slot_iternext ->
# Coro::send -> resume_gen_frame -> ExecutingFrame::run -> pop_value -> ExecutingFrame::fatal
# 'tried to pop from empty stack' (frame.rs:10092). Observed once in fusil-rustpython_09
# (--concurrency-stress, module http.cookiejar). The generator involved is transient (created
# inside concurrent module calls), NOT one of the shared iterators, so it can't be pinned to a
# small input; the original vehicle reproduces 0/5 on replay -- this is a sub-1% window.
#
# The script below exercises the class (many threads resuming shared generators) but did NOT
# reproduce across ~7 shapes/thousands of iterations -- kept as documentation of the attempt.
# The actual observed crash: see the vehicle
#   /home/fusil/runs/fusil-rustpython_09/inst-02/python/http_cookiejar-panicked-rustpyNEW/
# (source.py + stdout + gdb backtrace in report.md).
import threading


def gen():
    while True:
        try:
            yield sum([1, 2, 3, 4])
        except BaseException:
            pass


g = gen()
next(g)


def worker():
    for _ in range(300000):
        try:
            next(g)
        except BaseException:
            pass


ts = [threading.Thread(target=worker) for _ in range(16)]
[t.start() for t in ts]
[t.join() for t in ts]
