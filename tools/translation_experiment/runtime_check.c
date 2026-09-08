/* Synthetic adapter contract checks. No engine, voice, or vendor guest code. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include "tiger_box_api.h"

static volatile sig_atomic_t received, blocked, value;
static void prior(int signum, siginfo_t *info, void *context)
{
    sigset_t mask;
    (void)signum; (void)context;
    sigprocmask(SIG_SETMASK, NULL, &mask);
    blocked = sigismember(&mask, SIGUSR2);
    value = info->si_value.sival_int;
    ++received;
}
static void require(int ok, const char *what)
{
    if (!ok) { fprintf(stderr, "FAIL: %s\n", what); exit(1); }
}
static uint32_t execute(uc_engine *u, uintptr_t code, uintptr_t stack)
{
    uint32_t sp = stack + 2048, ret = 0x76543210, got = 0;
    memcpy((void *)(uintptr_t)sp, &ret, 4);
    require(uc_reg_write(u, UC_X86_REG_ESP, &sp) == UC_ERR_OK, "set stack");
    require(uc_emu_start(u, code, ret, 0, 0) == UC_ERR_OK, "execute");
    require(uc_reg_read(u, UC_X86_REG_EAX, &got) == UC_ERR_OK, "read result");
    return got;
}
int main(int argc, char **argv)
{
    require(argc == 2, "choose rewrite, remap, or signals");
    if (!strcmp(argv[1], "signals")) {
        struct sigaction action = {0};
        action.sa_sigaction = prior;
        action.sa_flags = SA_SIGINFO | SA_RESETHAND;
        sigemptyset(&action.sa_mask);
        sigaddset(&action.sa_mask, SIGUSR2);
        require(!sigaction(SIGILL, &action, NULL), "install native action");
        uc_engine *u;
        require(uc_open(UC_ARCH_X86, UC_MODE_32, &u) == UC_ERR_OK, "open");
        union sigval payload; payload.sival_int = 123;
        require(!sigqueue(getpid(), SIGILL, payload), "queue native signal");
        require(received == 1 && value == 123, "original siginfo payload");
        require(blocked, "native handler's signal mask");
        pid_t child = fork();
        require(child >= 0, "fork reset test");
        if (!child) { raise(SIGILL); _exit(71); }
        int status = 0;
        require(waitpid(child, &status, 0) == child, "wait reset test");
        require(WIFSIGNALED(status) && WTERMSIG(status) == SIGILL,
                "native SA_RESETHAND disposition");
        puts("PASS native signal payload, mask and reset disposition");
        return 0;
    }
    uc_engine *u;
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uintptr_t base = 0x30000000;
    void *mem = mmap((void *)base, 2*page, PROT_READ|PROT_WRITE|PROT_EXEC,
                     MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (mem != (void *)base)
        fprintf(stderr, "mapping requested=%p actual=%p page=%zu\n", (void *)base, mem, page);
    require(mem == (void *)base, "identity mapping at an unused low address");
    unsigned char code[] = {0xb8, 42, 0, 0, 0, 0xc3}; /* mov eax,42; ret */
    memcpy(mem, code, sizeof code);
    require(uc_open(UC_ARCH_X86, UC_MODE_32, &u) == UC_ERR_OK, "open");
    require(uc_mem_map_ptr(u, base, 2*page, UC_PROT_ALL, mem) == UC_ERR_OK, "map");
    for (int i=0; i<16; ++i) require(execute(u, base, base+page) == 42, "initial code");
    /* A new host thread replays mappings already known to Box. The replay
     * must retain the protection flags of previously translated pages. */
    require(uc_mem_map_ptr(u, base, 2*page, UC_PROT_ALL, mem) == UC_ERR_OK, "replay mapping");
    if (!strcmp(argv[1], "rewrite")) {
        uint32_t next = 7;
        require(uc_mem_write(u, base+1, &next, sizeof next) == UC_ERR_OK, "rewrite code");
    } else {
        require(!strcmp(argv[1], "remap"), "valid mode");
        require(uc_mem_unmap(u, base, page) == UC_ERR_OK, "unmap code");
        require(!munmap(mem, page), "release native mapping");
        mem = mmap((void *)base, page, PROT_READ|PROT_WRITE|PROT_EXEC,
                   MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
        require(mem == (void *)base, "reuse released address");
        code[1] = 7; memcpy(mem, code, sizeof code);
        require(uc_mem_map_ptr(u, base, page, UC_PROT_ALL, mem) == UC_ERR_OK, "remap");
    }
    for (int i=0; i<16; ++i) require(execute(u, base, base+page) == 7, "new code replaces cached translation");
    puts("PASS changed code at a previously translated address");
    return 0;
}
