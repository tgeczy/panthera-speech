#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <sys/mman.h>
#include <time.h>
#include "box64context.h"
#include "env.h"
#include "debug.h"
#include "emu/x64emu_private.h"

#include "box64cpu.h"
extern FILE *ftrace;
extern int box64_is32bits;
#include "x64emu.h"

#include "custommem.h"
#include "auxval.h"
#include "workload_abi.h"

extern char **environ;
extern void thread_set_emu(x64emu_t *);
static void *map_at(uintptr_t addr, size_t len) {
    void *p=mmap((void*)addr,len,PROT_READ|PROT_WRITE,
        MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    if(p==MAP_FAILED || p!=(void*)addr) {perror("mmap");exit(1);}
    setProtection(addr,len,PROT_READ|PROT_WRITE);
    return p;
}
static double now_s(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return t.tv_sec+t.tv_nsec/1e9;
}
int main(int argc, char **argv) {
    if(argc<2) return 2;
    init_auxval(argc,(const char**)argv,environ);
    ftrace=stderr;box64_pagesize=4096;LoadEnvVariables();DetectHostCpuFeatures();box64_is32bits=1;
    box64context_t *ctx=NewBox64Context(0);

    uint32_t steps=argc>2?strtoul(argv[2],NULL,10):40000000;
    int passes=argc>3?atoi(argv[3]):3;
    void *code=map_at(CODE_BASE,4096);
    FILE *f=fopen(argv[1],"rb"); if(!f) {perror("blob");return 1;}
    size_t len=fread(code,1,4096,f); fclose(f);
    mprotect(code,4096,PROT_READ|PROT_EXEC);
    setProtection(CODE_BASE,4096,PROT_READ|PROT_EXEC);
    map_at(0x80000,0x10000);
    uint32_t *tab=map_at(DATA_BASE,TABLE_WORDS*4);
    map_at(RESULT_ADDR,4096);
    for(uint32_t i=0;i<TABLE_WORDS;i++) tab[i]=(2654435761u*i+12345u)&(TABLE_WORDS-1);
    *(uint32_t*)STEPS_ADDR=steps;
    x64emu_t *emu=NewX64Emu(ctx,CODE_BASE,0x80000,0x10000,0);
    SetupX64Emu(emu,NULL); thread_set_emu(emu);
    printf("Box64 blob=%zu steps=%u\n",len,steps); fflush(stdout);
    for(int k=0;k<passes;k++) {
        R_RSP=STACK_TOP-4; *(uint32_t*)(uintptr_t)R_RSP=ctx->exit_bridge;
        R_CS=0x23;R_RIP=CODE_BASE;emu->quit=0;
        double t=now_s(); EmuRun(emu,1,0); double dt=now_s()-t;
        printf("pass %d: %.6f s result=0x%08x\n",k+1,dt,*(uint32_t*)RESULT_ADDR);
        fflush(stdout);
    }
    return 0;
}
