eval(chr(0xd800))  # -> builtins.rs:607 'PyStr contains surrogates' (expect_str on a surrogate string)
