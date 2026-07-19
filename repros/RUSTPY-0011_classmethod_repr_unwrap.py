# RUSTPY-0011: repr(classmethod(obj)) panics when obj.__repr__ raises.
# classmethod.__repr__ does `zelf.callable.lock().repr(vm).unwrap()` (classmethod.rs:198) --
# the classmethod sibling of RUSTPY-0009 (staticmethod.rs:182). CPython propagates the exception.
class R:
    def __repr__(self):
        raise ValueError("boom")


repr(classmethod(R()))
