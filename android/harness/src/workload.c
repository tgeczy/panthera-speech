/* workload -- one representative i386 blob, run identically under Unicorn on
 * desktop and device so device_emu / desktop_native is a real multiplier.
 *
 * The engine's emulated slice (~28% of Alex; the rest is AAC+FFT that trap to
 * native) is branchy control/text code that misses cache -- NOT a register
 * loop and NOT FP.  So: a pointer-chase over a 4 MB table (dependent loads
 * defeat prefetch -> real memory latency, TCG's worst case) with data-dependent
 * mixing and branches.  Integer only, so no x87/SSE-in-TCG questions and no
 * libcalls; compiles to a flat .text blob with run() at offset 0.
 *
 * Contract in workload_abi.h: driver fills TABLE at DATA_BASE with a chase
 * permutation, sets ESP=STACK_TOP, pushes RET_MAGIC, sets EIP=CODE_BASE, and
 * emu_start(CODE_BASE, RET_MAGIC).  run() writes its accumulator to RESULT_ADDR.
 */
#include <stdint.h>
#include "workload_abi.h"

__attribute__((used, section(".text.run")))
void run(void)
{
    volatile uint32_t *tab = (volatile uint32_t *)DATA_BASE;
    uint32_t idx = 0, acc = 0x12345678u;
    uint32_t steps = *(volatile uint32_t *)STEPS_ADDR;
    for (uint32_t i = 0; i < steps; i++) {
        idx = tab[idx];                          /* dependent load: the cache miss */
        if (idx & 1u) acc += idx * 2654435761u;  /* Knuth mix (imul, no libcall)   */
        else          acc ^= (idx >> 3);
        if ((acc & 0x3fu) == 0u)                 /* data-dependent, unpredictable  */
            idx ^= acc & (TABLE_WORDS - 1u);     /* stays in range (power-of-two)  */
    }
    *(volatile uint32_t *)RESULT_ADDR = acc ^ idx;
}
