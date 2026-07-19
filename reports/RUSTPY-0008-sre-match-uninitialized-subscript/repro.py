import re
M = type(re.match('a', 'a'))   # the Match type
M.__new__(M)[0]                # SIGSEGV: uninitialized Match, mapping subscript reads garbage regs/string
