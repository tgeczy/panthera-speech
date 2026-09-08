#!/usr/bin/env python3
# Desktop reference: run the workload blob under Unicorn (Python binding 2.1.4,
# same 2.1.4 the device lib is built from). Prints one-pass wall time -- the
# desktop-emulated number that the device-emulated number is divided by.
import sys, time, struct
from unicorn import *
from unicorn.x86_const import *

# must match workload_abi.h
CODE_BASE, STACK_TOP, RET_MAGIC = 0x00100000, 0x00090000, 0x000a0000
DATA_BASE, RESULT_ADDR, STEPS_ADDR = 0x01000000, 0x02000000, 0x02000004
TABLE_WORDS = 1 << 20
MASK = TABLE_WORDS - 1

blob = open(sys.argv[1], "rb").read()
steps = int(sys.argv[2]) if len(sys.argv) > 2 else 40000000
passes = int(sys.argv[3]) if len(sys.argv) > 3 else 3

mu = Uc(UC_ARCH_X86, UC_MODE_32)
mu.mem_map(0x00080000, 0x00030000)              # stack + RET_MAGIC land here
mu.mem_map(CODE_BASE, 0x1000)
mu.mem_map(DATA_BASE, TABLE_WORDS * 4)
mu.mem_map(RESULT_ADDR & ~0xfff, 0x1000)
mu.mem_write(CODE_BASE, blob)

# chase table: affine full-period map tab[i] = (a*i + c) & MASK, a%4==1, c odd
# -> one cycle over all 2^20 indices, no fixed point (the i*odd version had
# tab[0]=0, a fixed point that trapped the chase at a single cached line).
tab = bytearray(TABLE_WORDS * 4)
a, c = 2654435761, 12345
for i in range(TABLE_WORDS):
    struct.pack_into("<I", tab, i * 4, (a * i + c) & MASK)
mu.mem_write(DATA_BASE, bytes(tab))
mu.mem_write(STEPS_ADDR, struct.pack("<I", steps))

def one_pass():
    sp = STACK_TOP - 4
    mu.mem_write(sp, struct.pack("<I", RET_MAGIC))   # run()'s return address
    mu.reg_write(UC_X86_REG_ESP, sp)
    mu.reg_write(UC_X86_REG_EIP, CODE_BASE)
    t = time.perf_counter()
    mu.emu_start(CODE_BASE, RET_MAGIC, timeout=180 * 1000000)
    dt = time.perf_counter() - t
    res = struct.unpack("<I", mu.mem_read(RESULT_ADDR, 4))[0]
    return dt, res

best = None
for k in range(passes):
    dt, res = one_pass()
    print("pass %d: %.3f s  (result=0x%08x)" % (k + 1, dt, res))
    best = dt if best is None else min(best, dt)
print("DESKTOP-UNICORN best one-pass: %.3f s over %d steps  (%.1f Msteps/s)"
      % (best, steps, steps / 1e6 / best))
