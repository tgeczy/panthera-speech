/* embench_driver -- run the workload blob under Unicorn ON THE DEVICE.
 * Mirrors uc_ref.py exactly (same memory map, same affine chase table, same
 * RET_MAGIC return trick) so the only difference from the desktop number is the
 * CPU.  Links the NDK-cross-built libunicorn statically; one pushable binary.
 *
 *   embench_driver <blob> [steps] [passes]
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <unicorn/unicorn.h>
#include "workload_abi.h"

static double now_s(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: embench_driver <blob> [steps] [passes]\n"); return 2; }
    uint32_t steps  = argc > 2 ? (uint32_t)strtoul(argv[2], NULL, 10) : 40000000u;
    int      passes = argc > 3 ? atoi(argv[3]) : 3;

    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("blob"); return 1; }
    uint8_t blob[4096]; size_t blen = fread(blob, 1, sizeof blob, f); fclose(f);

    uc_engine *uc; uc_err e;
    if ((e = uc_open(UC_ARCH_X86, UC_MODE_32, &uc))) {
        fprintf(stderr, "uc_open: %s\n", uc_strerror(e)); return 1; }
    uc_mem_map(uc, 0x00080000, 0x00030000, UC_PROT_ALL);        /* stack + RET_MAGIC */
    uc_mem_map(uc, CODE_BASE, 0x1000, UC_PROT_ALL);
    uc_mem_map(uc, DATA_BASE, TABLE_WORDS * 4, UC_PROT_ALL);
    uc_mem_map(uc, RESULT_ADDR & ~0xfffu, 0x1000, UC_PROT_ALL);
    uc_mem_write(uc, CODE_BASE, blob, blen);

    uint32_t *tab = malloc(TABLE_WORDS * 4);
    uint32_t a = 2654435761u, c = 12345u, mask = TABLE_WORDS - 1u;
    for (uint32_t i = 0; i < TABLE_WORDS; i++) tab[i] = (a * i + c) & mask;
    uc_mem_write(uc, DATA_BASE, tab, TABLE_WORDS * 4);
    uc_mem_write(uc, STEPS_ADDR, &steps, 4);

    printf("== embench (Unicorn on device) blob=%zuB steps=%u ==\n", blen, steps);
    double best = 1e9; uint32_t res = 0;
    for (int k = 0; k < passes; k++) {
        uint32_t sp = STACK_TOP - 4, magic = RET_MAGIC, eip = CODE_BASE;
        uc_mem_write(uc, sp, &magic, 4);
        uc_reg_write(uc, UC_X86_REG_ESP, &sp);
        uc_reg_write(uc, UC_X86_REG_EIP, &eip);
        double t = now_s();
        e = uc_emu_start(uc, CODE_BASE, RET_MAGIC, 180 * 1000000, 0);
        double dt = now_s() - t;
        if (e) { fprintf(stderr, "emu_start: %s\n", uc_strerror(e)); return 1; }
        uc_mem_read(uc, RESULT_ADDR, &res, 4);
        printf("pass %d: %.3f s  (result=0x%08x)\n", k + 1, dt, res);
        if (dt < best) best = dt;
    }
    printf("DEVICE-UNICORN best one-pass: %.3f s over %u steps  (%.1f Msteps/s)\n",
           best, steps, steps / 1e6 / best);
    uc_close(uc); free(tab);
    return 0;
}
