# RUSTPY-0023 — concurrent generator resumption underflows the frame value stack (`tried to pop from empty stack`, frame.rs:10092 via Coro::send) [rare race, no reliable repro]

**New in fusil-rustpython_09** (the `--concurrency-stress` fleet). **concurrency-triggered**. Reliability: RARE race -- vehicle reproduces 0/5 on replay; ~7 synthetic shapes did not reproduce.

## Reproducer

```python
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
```

CPython: generators are guarded against concurrent resumption ('ValueError: generator already executing'); no crash.

## Root cause & fix

gdb backtrace (unambiguous): builtins.next -> PyIter::next -> PyGenerator::slot_iternext -> Coro::send -> resume_gen_frame -> Py<Frame>::resume -> ExecutingFrame::run -> ExecutingFrame::pop_value -> ExecutingFrame::fatal 'tried to pop from empty stack' (frame.rs:10092). A Python generator's frame value stack underflowed while being resumed under the --concurrency-stress op-mix (module http.cookiejar). Coro::run_with_context (coroutine.rs:110) guards *simultaneous* resume with a compare_exchange on `running`, but send/send_none first do a NON-atomic `running.load()` fast-path check and read `self.frame.lasti()` BEFORE the CAS (coroutine.rs:178-186 / :199-212); the crash is consistent with a resume/finalization race on the frame's non-atomic value stack that the CAS does not fully cover (candidate: gc/close finalization or torn frame state). The exact trigger is unpinned: the crashing generator is transient (created inside concurrent module calls), NOT one of the shared iterators, and the vehicle reproduces 0/5 -- a sub-1% window. SIGNATURE frame.rs:10092 is ExecutingFrame::fatal, a GENERIC stack-underflow sink many unrelated bugs funnel through, so it is deliberately NOT added to known_panics.tsv (would over-match). CPython raises 'ValueError: generator already executing' instead of crashing. Recorded as a confirmed-but-rare VM crash; reproducer is the fleet vehicle /home/fusil/runs/fusil-rustpython_09/inst-02/python/http_cookiejar-panicked-rustpyNEW/.

## gdb backtrace

```
thread '<unnamed>' panicked at crates/vm/src/frame.rs:10092:18:
tried to pop from empty stack
  2: <ExecutingFrame>::fatal
  3: <ExecutingFrame>::pop_value
  4: <ExecutingFrame>::run
  5: <Py<Frame>>::resume
  6: <VirtualMachine>::resume_gen_frame
  7: <Coro>::send
  8: <PyGenerator as IterNext>::slot_iternext
  9: <PyIter>::next
 10: builtins::next
```
