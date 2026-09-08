/* Native Linux i386 counterpart of Windows' guest divide-by-zero recovery.
 * Never install this in an emulated host: the translator owns its faults. */
#ifndef TIGER_NATIVE_DIVZERO_H
#define TIGER_NATIVE_DIVZERO_H
#if defined(__linux__) && defined(__i386__) && !defined(TIGER_UC)
#include <errno.h>
#include <signal.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <ucontext.h>
#include <unistd.h>
#include "tiger_divisor.h"

static struct sigaction native_divzero_previous;
static int (*native_divzero_guest_pc)(uintptr_t);

/* Kernel-checked access avoids faulting inside the handler on a read-only
 * divisor or a code instruction at a mapping boundary. No allocation, stdio,
 * locks, or /proc parsing is allowed in this path. Check every transfer size.
 * See https://man7.org/linux/man-pages/man2/process_vm_readv.2.html. */
static ssize_t native_divzero_copy(void *local, uintptr_t remote, size_t size, int writing)
{
    struct iovec src = {local, size}, dst = {(void *)remote, size};
    return syscall(writing ? SYS_process_vm_writev : SYS_process_vm_readv,
                   getpid(), &src, 1ul, &dst, 1ul, 0ul);
}

static int native_divzero_recover(siginfo_t *info, ucontext_t *context)
{
    unsigned char code[15];
    greg_t *r = context->uc_mcontext.gregs;
    uint32_t regs[8], address, value = 0, one = 1;
    uintptr_t pc = (uint32_t)r[REG_EIP];
    ssize_t count;
    unsigned width;
    if (info->si_code != FPE_INTDIV || !native_divzero_guest_pc(pc)) return 0;
    count = native_divzero_copy(code, pc, sizeof code, 0);
    if (count <= 0) return 0;
    regs[0] = r[REG_EAX]; regs[1] = r[REG_ECX];
    regs[2] = r[REG_EDX]; regs[3] = r[REG_EBX];
    regs[4] = r[REG_ESP]; regs[5] = r[REG_EBP];
    regs[6] = r[REG_ESI]; regs[7] = r[REG_EDI];
    if (!divisor_memory(code, (size_t)count, regs, &address, &width)) return 0;
    if (native_divzero_copy(&value, address, width, 0) != width || value != 0) return 0;
    return native_divzero_copy(&one, address, width, 1) == width;
}

static void native_divzero_signal(int signo, siginfo_t *info, void *opaque)
{
    int saved_errno = errno;
    struct sigaction previous;
    if (native_divzero_recover(info, (ucontext_t *)opaque)) {
        errno = saved_errno;
        return; /* retry the original instruction with divisor 1, as Windows does */
    }
    previous = native_divzero_previous;
    /* The installed wrapper uses the owner's mask and stack/defer flags.
     * Forward the original siginfo/context, including queued-signal payloads,
     * and retain recovery when an ordinary user handler returns. */
    if (previous.sa_handler != SIG_DFL && previous.sa_handler != SIG_IGN) {
        if (previous.sa_flags & SA_RESETHAND) {
            struct sigaction reset = previous;
            reset.sa_handler = SIG_DFL; reset.sa_flags = 0;
            sigaction(SIGFPE, &reset, NULL);
        }
        errno = saved_errno;
        if (previous.sa_flags & SA_SIGINFO) previous.sa_sigaction(signo, info, opaque);
        else previous.sa_handler(signo);
        errno = saved_errno;
        return;
    }
    if (previous.sa_handler == SIG_IGN && info->si_code <= 0) {
        errno = saved_errno;
        return;
    }
    /* For a default action, a hardware fault recurs at the unchanged PC;
     * an explicitly sent signal must be queued again. Unsupported operands,
     * overflow and host arithmetic faults are never silently recovered. */
    sigaction(SIGFPE, &previous, NULL);
    if (info->si_code <= 0) raise(signo);
    errno = saved_errno;
}

static int install_native_divzero(int (*guest_pc)(uintptr_t))
{
    struct sigaction action;
    if (native_divzero_guest_pc) return 1;
    if (sigaction(SIGFPE, NULL, &native_divzero_previous)) return 0;
    action = native_divzero_previous;
    action.sa_sigaction = native_divzero_signal;
    action.sa_flags = SA_SIGINFO | (action.sa_flags & (SA_ONSTACK | SA_RESTART | SA_NODEFER));
    native_divzero_guest_pc = guest_pc;
    if (sigaction(SIGFPE, &action, NULL)) { native_divzero_guest_pc = NULL; return 0; }
    return 1;
}
#endif
#endif
