class R:
    def __repr__(self):
        raise ValueError("boom")
repr(staticmethod(R()))   # panics: staticmethod.rs:182 unwraps the inner repr's Err instead of propagating
