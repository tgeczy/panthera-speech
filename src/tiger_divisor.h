/* Decode the memory operand of the small x86 div/idiv subset supported by
 * the host's divide-by-zero recovery. This is instruction-format handling,
 * independent of any engine address or function. Unsupported forms fail. */
#ifndef TIGER_DIVISOR_H
#define TIGER_DIVISOR_H
#include <stddef.h>
#include <stdint.h>

static uint32_t divisor_u32(const unsigned char *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int divisor_memory(const unsigned char *code, size_t size,
                          const uint32_t regs[8], uint32_t *address, unsigned *width)
{
    size_t i = 0;
    unsigned modrm, mod, rm;
    uint32_t base;
    *width = 4;
    /* Address-size and segment overrides need additional context. Do not
     * silently decode them as ordinary flat 32-bit addressing. */
    if (i < size && code[i] == 0x66) { *width = 2; ++i; }
    if (i >= size) return 0;
    if (code[i] == 0xf6) *width = 1;
    else if (code[i] != 0xf7) return 0;
    if (++i >= size) return 0;
    modrm = code[i++];
    if (((modrm >> 3) & 7) < 6) return 0;
    mod = modrm >> 6; rm = modrm & 7;
    if (mod == 3 || rm == 4) return 0; /* register and SIB not supported */
    if (mod == 0 && rm == 5) {
        if (size - i < 4) return 0;
        *address = divisor_u32(code + i);
        return 1;
    }
    base = regs[rm];
    if (mod == 1) {
        if (i >= size) return 0;
        base += (int8_t)code[i];
    } else if (mod == 2) {
        if (size - i < 4) return 0;
        base += divisor_u32(code + i);
    }
    *address = base;
    return 1;
}
#endif
