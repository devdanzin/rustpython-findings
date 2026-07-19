import _csv, io
_csv.writer(io.StringIO())   # panics: csv.rs:748 `get("excel").unwrap()` — default dialect not registered
