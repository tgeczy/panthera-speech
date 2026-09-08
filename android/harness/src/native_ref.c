/* native_ref -- the SAME workload loop as workload.c, but built native i386
 * (MSVC Hostx64/x86) and run on a real host table.  This is the denominator:
 * device_emulated / desktop_native = the multiplier to apply to the engine's
 * measured 27 ms native slice.  Loop body mirrors src/workload.c exactly. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <windows.h>

#define TABLE_WORDS (1u << 20)
#define MASK (TABLE_WORDS - 1u)

static uint32_t run(volatile uint32_t *tab, uint32_t steps)
{
    uint32_t idx = 0, acc = 0x12345678u;
    for (uint32_t i = 0; i < steps; i++) {
        idx = tab[idx];
        if (idx & 1u) acc += idx * 2654435761u;
        else          acc ^= (idx >> 3);
        if ((acc & 0x3fu) == 0u)
            idx ^= acc & (TABLE_WORDS - 1u);
    }
    return acc ^ idx;
}

int main(int argc, char **argv)
{
    uint32_t steps  = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 10) : 40000000u;
    int      passes = argc > 2 ? atoi(argv[2]) : 3;
    uint32_t a = 2654435761u, c = 12345u;
    volatile uint32_t *tab = malloc(TABLE_WORDS * 4);
    for (uint32_t i = 0; i < TABLE_WORDS; i++) tab[i] = (a * i + c) & MASK;

    LARGE_INTEGER freq, t0, t1; QueryPerformanceFrequency(&freq);
    double best = 1e9; uint32_t res = 0;
    for (int k = 0; k < passes; k++) {
        QueryPerformanceCounter(&t0);
        res = run(tab, steps);
        QueryPerformanceCounter(&t1);
        double dt = (double)(t1.QuadPart - t0.QuadPart) / freq.QuadPart;
        printf("pass %d: %.3f s  (result=0x%08x)\n", k + 1, dt, res);
        if (dt < best) best = dt;
    }
    printf("DESKTOP-NATIVE best one-pass: %.3f s over %u steps  (%.1f Msteps/s)\n",
           best, steps, steps / 1e6 / best);
    return 0;
}
