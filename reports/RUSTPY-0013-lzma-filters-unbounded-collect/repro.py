# RUSTPY-0013: lzma.LZMACompressor / LZMADecompressor filters= argument
import lzma
import itertools

# filters= is collected into a Vec<PyObjectRef> (parse_filter_chain_spec, lzma.rs:340)
# before validation; an infinite generator balloons memory to OOM abort.
# CPython: TypeError: object of type 'generator' has no len().
lzma.LZMACompressor(format=lzma.FORMAT_RAW,
                    filters=({'id': lzma.FILTER_LZMA2} for _ in itertools.count()))
