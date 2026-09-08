/* jitprobe -- can a native process on this device JIT?
 *
 * The whole hybrid architecture rests on Unicorn, and Unicorn is QEMU TCG: it
 * writes ARM code into memory and runs it.  Since Android 10 the platform
 * enforces W^X on app memory, so the question splits in two, and which one
 * works tells us which mode Unicorn must run in:
 *
 *   RWX      : map writable+executable at once, write, run.   (simplest; often
 *              denied on modern Android / never on iOS)
 *   RW->RX   : map writable, write code, mprotect to read+exec, run.  (the
 *              portable W^X dance TCG already supports)
 *
 * We emit a tiny native stub that returns 42 and try to call it both ways.
 *
 * CAVEAT: a process launched from `adb shell` runs in the permissive `shell`
 * SELinux domain, NOT the `untrusted_app` domain the real TTS service lives in.
 * A pass here proves the CPU/kernel allow it; the app-sandbox verdict needs an
 * actual APK.  A *fail* here, though, is decisive -- if even shell can't, the
 * app never will.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
#include <unistd.h>

typedef int (*fn_t)(void);

/* A leaf function returning 42, in the target ABI's encoding. */
#if defined(__aarch64__)
static const uint32_t STUB[] = { 0x52800540u /* mov w0,#42 */, 0xd65f03c0u /* ret */ };
#elif defined(__arm__)
static const uint32_t STUB[] = { 0xe3a0002au /* mov r0,#42 */, 0xe12fff1eu /* bx lr  */ };
#elif defined(__i386__) || defined(__x86_64__)
static const uint8_t  STUB[] = { 0xb8, 42,0,0,0, 0xc3 };   /* mov eax,42 ; ret */
#else
# error "unknown ABI"
#endif

static void fill(void *p) {
    memcpy(p, STUB, sizeof STUB);
    __builtin___clear_cache((char *)p, (char *)p + sizeof STUB);
}

static int try_rwx(void) {
    void *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) { printf("  RWX map:      DENIED (mmap failed)\n"); return 0; }
    fill(p);
    fn_t f = (fn_t)p;
    int r = f();
    printf("  RWX map:      %s (returned %d)\n", r == 42 ? "OK" : "RAN-WRONG", r);
    munmap(p, 4096);
    return r == 42;
}

static int try_rw_then_rx(void) {
    void *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) { printf("  RW->RX map:   DENIED (mmap failed)\n"); return 0; }
    fill(p);
    if (mprotect(p, 4096, PROT_READ | PROT_EXEC) != 0) {
        printf("  RW->RX map:   DENIED (mprotect to R+X failed)\n");
        munmap(p, 4096); return 0;
    }
    fn_t f = (fn_t)p;
    int r = f();
    printf("  RW->RX map:   %s (returned %d)\n", r == 42 ? "OK" : "RAN-WRONG", r);
    munmap(p, 4096);
    return r == 42;
}

int main(void) {
    printf("== jitprobe ==\n");
    int rwx = try_rwx();
    int wx  = try_rw_then_rx();
    printf("---- verdict ----\n");
    if (rwx || wx)
        printf("  JIT is POSSIBLE here (%s). Unicorn/TCG can run%s.\n",
               rwx ? (wx ? "both modes" : "RWX only")
                   : "W^X / RW->RX only",
               (!rwx && wx) ? " in its W^X mode" : "");
    else
        printf("  JIT BLOCKED. Unicorn cannot run; the port would need an\n"
               "  interpreter fallback (far slower) on this device.\n");
    return (rwx || wx) ? 0 : 1;
}
