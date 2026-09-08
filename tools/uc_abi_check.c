/* Standalone check of the production guest-word forwarding signature.
 * No engine data or Unicorn library needed. Build with Android NDK clang.
 * AArch64 deliberately poisons outgoing stack slots before the compiler
 * writes the arguments: the old unsigned prototype leaves high bits intact.
 */
#include <stdio.h>
#include <stdint.h>
#ifndef __cdecl
#define __cdecl
#endif
#include "../src/tiger_host_uc_abi.h"

static unsigned expected[12];

static __attribute__((noinline)) unsigned receive(
    uintptr_t a, uintptr_t b, uintptr_t c, uintptr_t d,
    uintptr_t e, uintptr_t f, uintptr_t g, uintptr_t h,
    uintptr_t i, uintptr_t j, uintptr_t k, uintptr_t l)
{
    uintptr_t values[12] = {a,b,c,d,e,f,g,h,i,j,k,l};
    unsigned failures = 0;
    for (unsigned n = 0; n < 12; n++)
        if (values[n] != expected[n]) failures |= 1u << n;
    return failures;
}

static __attribute__((noinline)) unsigned forward(fn_i callee, const unsigned *a)
{
#ifdef __aarch64__
    /* This non-tail call reserves four eight-byte outgoing argument slots.
     * We test their padding, without relying on previous stack contents. */
    __asm__ volatile(
        "mov x9, #-1\n"
        "stp x9, x9, [sp]\n"
        "stp x9, x9, [sp, #16]\n" ::: "x9", "memory");
#endif
    unsigned result = callee(a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7],a[8],a[9],a[10],a[11]);
    /* Keep the outgoing slots in this frame; prevent a tail call. */
    __asm__ volatile("" : "+r"(result) :: "memory");
    return result;
}

int main(void)
{
    for (unsigned n = 0; n < 12; n++) expected[n] = 0x81000000u + n;
    unsigned failures = forward((fn_i)receive, expected);
    printf("guest word forwarding: %s (argument failure mask 0x%x)\n",
           failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
