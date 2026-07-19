# RUSTPY-0001 — `RefCell already borrowed` in thread-local cleanup (`_thread.rs:977`)

**Dominant panic (48 vehicles).** A worker thread aborts with a Rust `BorrowMutError` while registering
or cleaning up `_thread._local` data. Triggered by the fuzzer's threaded scripts that store hostile
objects (with `__del__`) in thread-locals and let the threads exit.

## Symptom

```
thread '<unnamed>' panicked at crates/vm/src/stdlib/_thread.rs:977:28:
RefCell already borrowed
```

(All 48 are on worker threads — the message appears mid-stdout, since the main thread keeps running.)

## Root cause

`crates/vm/src/stdlib/_thread.rs` keeps per-thread cleanup guards in a `thread_local!` `RefCell<Vec<..>>`:

```rust
thread_local! {
    static LOCAL_GUARDS: RefCell<Vec<LocalGuard>> = const { RefCell::new(Vec::new()) };
}
```

Registration (`:972-977`) borrows it mutably to push a guard:

```rust
LOCAL_GUARDS.with(|guards| {
    guards.borrow_mut().push(guard);      // :977
});
```

Cleanup at thread exit holds the **same** borrow across `.clear()`, which *drops* every `LocalGuard`:

```rust
fn cleanup_thread_local_data() {
    LOCAL_GUARDS.with(|guards| {
        guards.borrow_mut().clear();      // :612  drops each LocalGuard while the borrow is held
    });
}
```

and `LocalGuard::drop` removes and drops the thread's stored dict:

```rust
impl Drop for LocalGuard {
    fn drop(&mut self) {
        if let Some(local_data) = self.local.upgrade() {
            let removed = local_data.data.lock().remove(&self.thread_id);
            drop(removed);                 // runs Python __del__ of the stored value
        }
    }
}
```

So: `cleanup_thread_local_data` holds `LOCAL_GUARDS.borrow_mut()` (via `.clear()`), `.clear()` drops a
`LocalGuard`, that drops a stored value whose Python **`__del__` re-enters `_thread._local`** (creating a
new local / accessing `__dict__`), and that re-entrant path borrows `LOCAL_GUARDS` again → **`BorrowMutError`**.
The `drop(removed)` comment even notes "drop the value outside the lock to prevent deadlock if `__del__`
accesses `_local`" — but the outer `LOCAL_GUARDS` `RefCell` borrow is still held across the drop.

## Fix sketch

Don't hold the `LOCAL_GUARDS` `RefCell` borrow across code that can run arbitrary Python (`Drop` →
`__del__`). Take the guards out first, release the borrow, then drop:

```rust
let guards: Vec<LocalGuard> = LOCAL_GUARDS.with(|g| std::mem::take(&mut *g.borrow_mut()));
drop(guards);   // __del__ can now re-borrow LOCAL_GUARDS safely
```

and likewise for the `push` path (don't run Python while the borrow is live).

## Reproducer

```python
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
```

```
thread '<unnamed>' panicked at crates/vm/src/stdlib/_thread.rs:977:28:
RefCell already borrowed
```

Reproduces the exact fleet panic (`_thread.rs:977`) **3/3**. The panic is on a worker thread, so the
process may hang afterward (the poisoned `RefCell` + dead worker) — the panic message is the finding; kill
the process once it prints.

**The key to the minimal repro** (why an obvious version doesn't trip): `LocalGuard` holds only a *weak*
ref to `LocalData`, so a *per-thread* `_local` (created inside `worker`) is dropped — and its stored
value's `__del__` runs — when `worker` returns, **before** `cleanup_thread_local_data`, so no borrow is
held. Using a **global** `_local` keeps `LocalData` alive, so the guard's `upgrade()` succeeds *inside*
`cleanup_thread_local_data`'s `borrow_mut().clear()`, running the stored value's `__del__` while the
borrow is held → the re-entrant `_local` access hits the `push` at `:977` → `BorrowMutError`.

## Impact

A hostile (or merely `__del__`-having) object stored in a `threading.local()` and dropped at thread exit
can abort a worker thread — reachable from ordinary threaded Python.
