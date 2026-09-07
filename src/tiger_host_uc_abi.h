#ifndef PANTHERA_UC_ABI_H
#define PANTHERA_UC_ABI_H
#include <stdint.h>

/* Each guest word must occupy a complete host argument slot. On AArch64,
 * arguments after x7 live in eight-byte stack slots: forwarding unsigned
 * writes only their low half, but a pointer parameter reads all eight bytes.
 * Alex's vDSP_vmma output pointer is argument nine; stale high bits made its
 * cross-fade write through a random host address. uintptr_t zero-extends the
 * guest word for both register and stack arguments (unchanged on ARMv7).
 * Mixed-width and floating-point signatures remain explicitly marshalled. */
typedef unsigned (__cdecl *fn_i)(uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,
    uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t);
typedef void     (__cdecl *fn_v)(uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,
    uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t);
typedef unsigned long long (__cdecl *fn_q)(uintptr_t,uintptr_t,uintptr_t,uintptr_t,
    uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t);
typedef double   (__cdecl *fn_d)(uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,
    uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t);
typedef float    (__cdecl *fn_f)(uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,
    uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t,uintptr_t);

#endif
