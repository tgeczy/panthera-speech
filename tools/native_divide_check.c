/* No engine/data required: exercise x86 operand parsing and Linux signal
 * ownership with synthetic instruction sequences in an isolated child. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/tiger_divisor.h"
#if defined(__linux__) && defined(__i386__)
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include "../src/tiger_native_divzero.h"
static uintptr_t code_start, code_end;
static int in_guest(uintptr_t pc) { return pc >= code_start && pc < code_end; }
static void previous(int sig, siginfo_t *info, void *ctx)
{ (void)ctx; _exit(sig == SIGFPE && info->si_signo == SIGFPE ? 37 : 38); }
static volatile sig_atomic_t queued_received;
static void previous_returning(int sig, siginfo_t *info, void *ctx)
{
    if (sig != SIGFPE || !ctx || info->si_code != SI_QUEUE || info->si_value.sival_int != 123)
        _exit(39);
    ++queued_received;
}

static int signal_case(int kind)
{
    pid_t pid = fork();
    int status;
    if (pid < 0) return 1;
    if (!pid) {
        const struct rlimit no_core = {0,0};
        const unsigned char normal[] = {0x8b,0x4c,0x24,0x04, /* mov arg,%ecx */
            0xb8,42,0,0,0,0x99,0xf7,0x39,0xc3}; /* eax=42; cdq; idiv (%ecx); ret */
        unsigned char bytes[sizeof normal + 1];
        size_t len = sizeof normal, page = (size_t)sysconf(_SC_PAGESIZE);
        unsigned char *mapping = mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        unsigned *operand = mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        unsigned char *code;
        int result, expected;
        struct sigaction action;
        setrlimit(RLIMIT_CORE,&no_core);
        if (mapping == MAP_FAILED || operand == MAP_FAILED) _exit(50);
        memset(&action,0,sizeof action); sigemptyset(&action.sa_mask);
        action.sa_sigaction=previous; action.sa_flags=SA_SIGINFO|SA_RESETHAND;
        if (kind == 9) { action.sa_sigaction=previous_returning; action.sa_flags=SA_SIGINFO; }
        if (kind != 7 && sigaction(SIGFPE,&action,NULL)) _exit(51);
        memcpy(bytes,normal,sizeof normal);
        if (kind == 2) bytes[10]=0xf6; /* 8-bit divisor */
        if (kind == 3) { /* 16-bit divisor */
            memmove(bytes+11,bytes+10,3); bytes[10]=0x66; ++len;
        }
        if (kind == 5) { /* signed quotient overflow, not a zero divisor */
            bytes[5]=bytes[6]=bytes[7]=0; bytes[8]=0x80; *operand=~0u;
        }
        code=mapping+page-len; memcpy(code,bytes,len);
        if (mprotect(mapping,page,PROT_READ|PROT_EXEC) || mprotect(mapping+page,page,PROT_NONE)) _exit(52);
        code_start=(uintptr_t)code; code_end=code_start+len;
        if (kind == 4 || kind == 7) code_start=code_end; /* native caller, not guest */
        if (kind == 6 && mprotect(operand,page,PROT_READ)) _exit(53);
        if (!install_native_divzero(in_guest)) _exit(54);
        if (kind == 8) { raise(SIGFPE); _exit(55); }
        if (kind == 9) {
            union sigval value; value.sival_int=123;
            if (sigqueue(getpid(),SIGFPE,value) || sigqueue(getpid(),SIGFPE,value) ||
                queued_received!=2) _exit(58);
        }
        errno=EDOM;
        result=((int (*)(unsigned *))code)(operand);
        expected=42;
        if (kind >= 4 && kind != 9) _exit(56);
        _exit(result==expected && *operand==1 && errno==EDOM ? 0 : 57);
    }
    if (waitpid(pid,&status,0)!=pid) return 1;
    if (kind==7) return !(WIFSIGNALED(status)&&WTERMSIG(status)==SIGFPE);
    return !(WIFEXITED(status)&&WEXITSTATUS(status)==(kind>=4&&kind!=9?37:0));
}
#endif

int main(void)
{
    const uint32_t regs[8]={0x1000,0x2000,0x3000,0x4000,0x5000,0x6000,0x7000,0x8000};
    static const struct {
        unsigned char code[8]; unsigned size, width; uint32_t address;
    } cases[]={
        {{0xf7,0x7d,0xd4},3,4,0x5fd4}, /* signed disp8 */
        {{0xf7,0xb9,0x34,0x12,0,0},6,4,0x3234}, /* unsigned divide, disp32 */
        {{0xf6,0x38},2,1,0x1000},
        {{0x66,0xf7,0x3a},3,2,0x3000},
        {{0xf7,0x3d,0x78,0x56,0x34,0x12},6,4,0x12345678},
        {{0xf7,0xbb,0xff,0xff,0xff,0xff},6,4,0x3fff}
    };
    static const unsigned char rejected[][4]={
        {0xf7,0xf9}, {0xf7,0x3c}, {0x67,0xf7,0x39}, {0x64,0xf7,0x39},
        {0xf7,0x29}, {0x90}, {0x66,0x66,0xf7,0x39}
    };
    unsigned i,width; uint32_t address; size_t n;
    for(i=0;i<sizeof cases/sizeof cases[0];++i) {
        if(!divisor_memory(cases[i].code,cases[i].size,regs,&address,&width) ||
           width!=cases[i].width || address!=cases[i].address) return 1;
        for(n=0;n<cases[i].size;++n)
            if(divisor_memory(cases[i].code,n,regs,&address,&width)) return 2;
    }
    for(i=0;i<sizeof rejected/sizeof rejected[0];++i)
        if(divisor_memory(rejected[i],sizeof rejected[i],regs,&address,&width)) return 3;
#if defined(__linux__) && defined(__i386__)
    for(i=1;i<=9;++i) if(signal_case(i)) { fprintf(stderr,"signal case %u failed\n",i);return 4; }
    puts("PASS native divide: widths, guarded code boundary, errno, overflow/read-only rejection, prior/default signal ownership, queued payloads");
#else
    puts("PASS x86 divisor operand parsing and truncated/unsupported instruction rejection");
#endif
    return 0;
}
