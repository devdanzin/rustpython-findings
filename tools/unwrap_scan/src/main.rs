//! unwrap_scan: static scan of RustPython source for Python-reachable panic sites.
//!
//! RustPython's dominant crash class is a native `.unwrap()` / `.expect()` / `panic!` /
//! unchecked index reached from Python (see the rustpython-findings catalog). This tool walks the
//! Rust-implemented stdlib + builtins with `syn`, attributes each risky line to the
//! `#[pyfunction]` / `#[pymethod]` / `#[pygetset]` it lives in (and the enclosing `#[pymodule]` /
//! `#[pyclass]`), and emits:
//!
//!   * `risky_sites.tsv`  — module \t python_name \t kind \t pattern \t file:line
//!   * `rust_modules.txt` — the sorted native-module names (feed to `fusil --modules-file`)
//!
//! Name rules (from RustPython's derive macros): the module/function/method Python name is the
//! attribute's `name = "..."` override if present, else the Rust ident; `#[pymethod(magic)]`
//! wraps the ident in dunders. v1 limitation: protocol slots implemented in trait impls named via
//! `#[pyclass(with(Trait))]` are attributed to the trait impl's own fns, not re-mapped to the
//! owning class's protocol names (that cross-file resolution is future work) -- but their risky
//! lines are still reported with the trait method name.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use syn::spanned::Spanned;

/// Directories under the RustPython root that hold Python-reachable native code where the
/// catalog's findings live.
const SCAN_DIRS: &[&str] = &[
    "crates/vm/src/stdlib",
    "crates/stdlib/src",
    "crates/vm/src/builtins",
    "crates/vm/src/types",
];

/// Risky patterns scanned for within a fn body (substring match on non-comment lines).
const PATTERNS: &[(&str, &str)] = &[
    (".unwrap()", "unwrap"),
    (".expect(", "expect"),
    ("panic!", "panic"),
    ("unreachable!", "unreachable"),
    ("unimplemented!", "unimplemented"),
    ("todo!", "todo"),
    (".args[", "args-index"), // arity OOB (the _typing._idfunc face)
];

/// RustPython protocol traits: a `impl <Trait> for <Type>` whose Trait is one of these implements a
/// Python protocol SLOT (repr/hash/subscript/iter/...). Its methods carry no `#[pymethod]` attr but
/// are absolutely Python-reachable -- this is the `#[pyclass(with(Trait))]` surface the catalog's
/// staticmethod-repr (Representable) and re.Match-subscript (AsMapping) findings live in.
const PROTOCOL_TRAITS: &[&str] = &[
    "Representable",
    "Hashable",
    "Comparable",
    "Iterable",
    "IterNext",
    "IterNextIterable",
    "AsMapping",
    "AsSequence",
    "AsNumber",
    "AsBuffer",
    "Callable",
    "Constructor",
    "Initializer",
    "GetDescriptor",
    "GetAttr",
    "SetAttr",
    "GetSet",
    "Destructor",
    "PyStructSequence",
    "DefaultConstructor",
    "Unconstructible",
];

#[derive(Clone)]
struct Ctx {
    module: String,
    class: Option<String>,
}

struct Row {
    module: String,
    /// Python-facing name when known (function / method / `Class.slot`), else the Rust fn ident.
    name: String,
    kind: &'static str,
    /// How the site is reached from Python: "py" (directly exposed via #[pyfunction]/#[pymethod]/
    /// ...), "protocol" (a protocol-trait slot -- the with(Trait) surface), or "internal" (a helper
    /// fn reached transitively from an exposed one).
    reachable: &'static str,
    pattern: &'static str,
    file: String,
    line: usize,
}

fn main() {
    let mut args = std::env::args().skip(1);
    let root = args
        .next()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var("RUSTPYTHON_ROOT").unwrap_or_default()));
    let out_dir = args.next().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    if root.as_os_str().is_empty() || !root.join("crates").is_dir() {
        eprintln!(
            "usage: unwrap_scan <RUSTPYTHON_ROOT> [OUT_DIR]\n\
             (RUSTPYTHON_ROOT must contain crates/; or set $RUSTPYTHON_ROOT)"
        );
        std::process::exit(2);
    }

    let mut rows: Vec<Row> = Vec::new();
    let mut modules: BTreeSet<String> = BTreeSet::new();
    let mut files_scanned = 0usize;
    let mut parse_failures = 0usize;

    for dir in SCAN_DIRS {
        let base = root.join(dir);
        if !base.is_dir() {
            continue;
        }
        for entry in walkdir::WalkDir::new(&base).into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("rs") {
                continue;
            }
            let text = match std::fs::read_to_string(path) {
                Ok(t) => t,
                Err(_) => continue,
            };
            let file = match syn::parse_file(&text) {
                Ok(f) => f,
                Err(_) => {
                    parse_failures += 1;
                    continue;
                }
            };
            files_scanned += 1;
            let lines: Vec<&str> = text.lines().collect();
            let rel = path.strip_prefix(&root).unwrap_or(path).to_string_lossy().to_string();
            // Default module for sites outside any #[pymodule] (builtins/ + types/ core types,
            // stdlib/ helpers): the parent directory name -- a meaningful grouping the file's own
            // #[pymodule], if any, overrides.
            let default_module = path
                .parent()
                .and_then(|p| p.file_name())
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            let ctx = Ctx { module: default_module, class: None };
            walk_items(&file.items, &ctx, &lines, &rel, &mut rows, &mut modules);
        }
    }

    write_outputs(&out_dir, &rows, &modules);
    eprintln!(
        "unwrap_scan: {} files parsed ({} unparseable), {} native modules, {} risky sites",
        files_scanned,
        parse_failures,
        modules.len(),
        rows.len()
    );
}

/// Recursively walk items, tracking the enclosing `#[pymodule]` module and `#[pyclass]` type.
fn walk_items(
    items: &[syn::Item],
    ctx: &Ctx,
    lines: &[&str],
    file: &str,
    rows: &mut Vec<Row>,
    modules: &mut BTreeSet<String>,
) {
    for item in items {
        match item {
            syn::Item::Mod(m) => {
                if let Some(name) = pymodule_name(&m.attrs, &m.ident.to_string()) {
                    modules.insert(name.clone());
                    let inner = Ctx { module: name, class: None };
                    if let Some((_, inner_items)) = &m.content {
                        walk_items(inner_items, &inner, lines, file, rows, modules);
                    }
                } else if let Some((_, inner_items)) = &m.content {
                    // A plain (non-pymodule) nested module: keep the current context.
                    walk_items(inner_items, ctx, lines, file, rows, modules);
                }
            }
            syn::Item::Fn(f) => {
                // A free fn: #[pyfunction] is directly exposed; any other free fn in these files is
                // an internal helper reachable transitively (e.g. csv's get_lineterminator).
                if has_attr(&f.attrs, "pyfunction") {
                    let name = fn_python_name(&f.attrs, &f.sig.ident.to_string(), "pyfunction");
                    scan_fn(&f.block.span(), lines, file, ctx, &name, "fn", "py", rows);
                } else {
                    scan_fn(
                        &f.block.span(),
                        lines,
                        file,
                        ctx,
                        &f.sig.ident.to_string(),
                        "fn",
                        "internal",
                        rows,
                    );
                }
            }
            syn::Item::Impl(im) => {
                let class = impl_self_ident(&im.self_ty).or_else(|| ctx.class.clone());
                let inner = Ctx { module: ctx.module.clone(), class };
                // A protocol-trait impl (`impl Representable for X`, `impl AsMapping for X`, ...) is
                // the with(Trait) surface -- its methods are Python slots even without a #[pymethod].
                let protocol_trait = im
                    .trait_
                    .as_ref()
                    .and_then(|(_, path, _)| path.segments.last())
                    .map(|s| s.ident.to_string())
                    .filter(|t| PROTOCOL_TRAITS.contains(&t.as_str()));
                for ii in &im.items {
                    if let syn::ImplItem::Fn(mf) = ii {
                        let (kind, reachable, name) = classify_method(&mf.attrs, mf, &protocol_trait);
                        let label = match &inner.class {
                            Some(c) => format!("{c}.{name}"),
                            None => name,
                        };
                        scan_fn(&mf.block.span(), lines, file, &inner, &label, kind, reachable, rows);
                    }
                }
            }
            _ => {}
        }
    }
}

/// Classify an impl method: (kind, reachable, python-or-rust name).
fn classify_method(
    attrs: &[syn::Attribute],
    mf: &syn::ImplItemFn,
    protocol_trait: &Option<String>,
) -> (&'static str, &'static str, String) {
    let ident = mf.sig.ident.to_string();
    if has_attr(attrs, "pymethod") {
        return ("method", "py", fn_python_name(attrs, &ident, "pymethod"));
    }
    if has_attr(attrs, "pygetset") {
        let kind = if attr_has_flag(attrs, "pygetset", "setter") {
            "getset-setter"
        } else {
            "getset"
        };
        return (kind, "py", fn_python_name(attrs, &ident, "pygetset"));
    }
    if has_attr(attrs, "pyslot") {
        return ("slot", "py", ident);
    }
    if has_attr(attrs, "pystaticmethod") {
        return ("staticmethod", "py", fn_python_name(attrs, &ident, "pystaticmethod"));
    }
    if has_attr(attrs, "pyclassmethod") {
        return ("classmethod", "py", fn_python_name(attrs, &ident, "pyclassmethod"));
    }
    if protocol_trait.is_some() {
        // A slot method of a protocol-trait impl (repr_str/as_mapping/hash/...): the with(Trait)
        // surface -- Python-reachable, just not via a #[pymethod] attr.
        return ("protocol", "protocol", ident);
    }
    ("helper", "internal", ident)
}

/// Scan a fn body's line range for risky patterns; push a Row per matching line.
#[allow(clippy::too_many_arguments)]
fn scan_fn(
    span: &proc_macro2::Span,
    lines: &[&str],
    file: &str,
    ctx: &Ctx,
    python_name: &str,
    kind: &'static str,
    reachable: &'static str,
    rows: &mut Vec<Row>,
) {
    let start = span.start().line;
    let end = span.end().line;
    if start == 0 || start > lines.len() {
        return;
    }
    let hi = end.min(lines.len());
    for ln in start..=hi {
        let raw = lines[ln - 1];
        let trimmed = raw.trim_start();
        if trimmed.starts_with("//") {
            continue;
        }
        for (needle, pattern) in PATTERNS {
            if raw.contains(needle) {
                rows.push(Row {
                    module: ctx.module.clone(),
                    name: python_name.to_string(),
                    kind,
                    reachable,
                    pattern,
                    file: file.to_string(),
                    line: ln,
                });
            }
        }
    }
}

/// `#[pymodule]` (with optional `name = "..."`) -> the module's Python name, else None.
fn pymodule_name(attrs: &[syn::Attribute], mod_ident: &str) -> Option<String> {
    let attr = attrs.iter().find(|a| a.path().is_ident("pymodule"))?;
    Some(attr_name_override(attr).unwrap_or_else(|| mod_ident.to_string()))
}

/// Python name of a py-fn/method: `name = "..."` override, else `__ident__` under `magic`, else ident.
fn fn_python_name(attrs: &[syn::Attribute], ident: &str, attr_ident: &str) -> String {
    let attr = attrs.iter().find(|a| a.path().is_ident(attr_ident));
    if let Some(attr) = attr {
        if let Some(name) = attr_name_override(attr) {
            return name;
        }
        if attr_ident == "pymethod" && attr_flag(attr, "magic") {
            return format!("__{ident}__");
        }
    }
    ident.to_string()
}

fn has_attr(attrs: &[syn::Attribute], ident: &str) -> bool {
    attrs.iter().any(|a| a.path().is_ident(ident))
}

fn attr_has_flag(attrs: &[syn::Attribute], attr_ident: &str, flag: &str) -> bool {
    attrs
        .iter()
        .find(|a| a.path().is_ident(attr_ident))
        .map(|a| attr_flag(a, flag))
        .unwrap_or(false)
}

/// True if the attribute's meta list contains a bare `flag` path, e.g. `#[pymethod(magic)]`.
fn attr_flag(attr: &syn::Attribute, flag: &str) -> bool {
    let mut found = false;
    let _ = attr.parse_nested_meta(|meta| {
        if meta.path.is_ident(flag) {
            found = true;
        }
        // Consume a value if present so parsing doesn't error on `name = "..."` siblings.
        if meta.input.peek(syn::Token![=]) {
            let _ = meta.value().and_then(|v| v.parse::<syn::Lit>());
        }
        Ok(())
    });
    found
}

/// Extract `name = "..."` from an attribute's meta list, if present.
fn attr_name_override(attr: &syn::Attribute) -> Option<String> {
    let mut found = None;
    let _ = attr.parse_nested_meta(|meta| {
        if meta.path.is_ident("name") {
            if let Ok(v) = meta.value().and_then(|v| v.parse::<syn::LitStr>()) {
                found = Some(v.value());
            }
        } else if meta.input.peek(syn::Token![=]) {
            let _ = meta.value().and_then(|v| v.parse::<syn::Lit>());
        }
        Ok(())
    });
    found
}

/// The bare type ident of an `impl <Type>` self type (e.g. `PyDialect`), else None.
fn impl_self_ident(ty: &syn::Type) -> Option<String> {
    if let syn::Type::Path(tp) = ty {
        return tp.path.segments.last().map(|s| s.ident.to_string());
    }
    None
}

fn write_outputs(out_dir: &Path, rows: &[Row], modules: &BTreeSet<String>) {
    let _ = std::fs::create_dir_all(out_dir);

    let mut tsv = String::from("# module\tname\tkind\treachable\tpattern\tfile:line\n");
    // Stable sort: reachable (py first), then module, name, file:line.
    let rank = |r: &&Row| match r.reachable {
        "py" => 0,
        "protocol" => 1,
        _ => 2,
    };
    let mut sorted: Vec<&Row> = rows.iter().collect();
    sorted.sort_by(|a, b| {
        (rank(&a), &a.module, &a.name, &a.file, a.line)
            .cmp(&(rank(&b), &b.module, &b.name, &b.file, b.line))
    });
    for r in sorted {
        tsv.push_str(&format!(
            "{}\t{}\t{}\t{}\t{}\t{}:{}\n",
            r.module, r.name, r.kind, r.reachable, r.pattern, r.file, r.line
        ));
    }
    let tsv_path = out_dir.join("risky_sites.tsv");
    if let Err(e) = std::fs::write(&tsv_path, tsv) {
        eprintln!("failed to write {}: {e}", tsv_path.display());
    } else {
        eprintln!("wrote {}", tsv_path.display());
    }

    let mut mods = String::from("# RustPython native (Rust-implemented) modules -- feed to fusil --modules-file\n");
    for m in modules {
        mods.push_str(m);
        mods.push('\n');
    }
    let mods_path = out_dir.join("rust_modules.txt");
    if let Err(e) = std::fs::write(&mods_path, mods) {
        eprintln!("failed to write {}: {e}", mods_path.display());
    } else {
        eprintln!("wrote {}", mods_path.display());
    }
}
