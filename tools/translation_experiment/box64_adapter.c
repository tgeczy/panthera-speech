/* Experimental adapter for Panthera's existing emulator seam. Not a general
 * Unicorn implementation: identity mappings and synchronous calls only. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <signal.h>
#include <ucontext.h>
#include <dlfcn.h>
#include <unistd.h>
#include <unicorn/unicorn.h>
#include "box64context.h"
#include "env.h"
#include "debug.h"
#include "auxval.h"
#include "x64emu.h"
#include "box64cpu.h"
#include "threads.h"

#include "wrapper.h"
#include "custommem.h"
#include "emu/x64emu_private.h"
#include "emu/x87emu_private.h"
#include "tools/bridge_private.h"
#include "box_signals.h"
extern void thread_set_emu(x64emu_t *);
struct uc_struct {
    x64emu_t *emu;
    void (*dispatch)(uc_engine*, uint64_t, uint32_t, void*);
    void *user;
};
struct uc_context { x64emu_t state; };
extern FILE *ftrace;
static box64context_t *ctx;
static pthread_once_t once=PTHREAD_ONCE_INIT;
static __thread uc_engine *active;
static void init(void) {
    box_capture_native_signals();
    ftrace=stderr;box64_pagesize=4096;LoadEnvVariables();DetectHostCpuFeatures();
    // Initialize native memory bookkeeping without reserving every address
    // above 4 GB. Only guest-visible allocations need the i386 address limit;
    // bionic and the Android runtime must retain their normal address space.
    box64_is32bits=0;init_custommem_helper(NULL);
    box64_is32bits=1;ctx=NewBox64Context(0);
    box_share_signals();
}
uc_err uc_open(uc_arch arch, uc_mode mode, uc_engine **out) {
    if(arch!=UC_ARCH_X86 || mode!=UC_MODE_32) return UC_ERR_ARCH;
    pthread_once(&once,init);
    uc_engine *u=calloc(1,sizeof(*u));
    u->emu=NewX64Emu(ctx,0,0,0,0); SetupX64Emu(u->emu,NULL);
    thread_set_emu(u->emu);*out=u; return UC_ERR_OK;
}
uc_err uc_close(uc_engine *u) {
    clean_current_emuthread(); free(u); return UC_ERR_OK;
}
const char *uc_strerror(uc_err e) {return e==UC_ERR_OK?"OK":"Box64 experiment fault";}
static uint64_t *reg(uc_engine *u,int id) {
    x64emu_t *emu=u->emu;
    switch(id) {
    case UC_X86_REG_EAX:return &R_RAX; case UC_X86_REG_EBX:return &R_RBX;
    case UC_X86_REG_ECX:return &R_RCX; case UC_X86_REG_EDX:return &R_RDX;
    case UC_X86_REG_ESI:return &R_RSI; case UC_X86_REG_EDI:return &R_RDI;
    case UC_X86_REG_EBP:return &R_RBP; case UC_X86_REG_ESP:return &R_RSP;
    case UC_X86_REG_EIP:return &R_RIP;
    default:return NULL;
    }
}
uc_err uc_reg_read(uc_engine *u,int id,void *v) {
    uint64_t *r=reg(u,id); if(!r)return UC_ERR_ARG; memcpy(v,r,4);return UC_ERR_OK;
}
uc_err uc_reg_write(uc_engine *u,int id,const void *v) {
    uint64_t *r=reg(u,id); if(!r)return UC_ERR_ARG; *r=*(const uint32_t*)v;return UC_ERR_OK;
}
uc_err uc_mem_read(uc_engine *u,uint64_t a,void *p,uint64_t n) {
    memcpy(p,(void*)(uintptr_t)a,n);return UC_ERR_OK;
}
uc_err uc_mem_write(uc_engine *u,uint64_t a,const void *p,uint64_t n) {
    memcpy((void*)(uintptr_t)a,p,n);return UC_ERR_OK;
}
uc_err uc_mem_map_ptr(uc_engine *u,uint64_t a,uint64_t n,uint32_t prot,void *p) {
    if(a!=(uintptr_t)p)return UC_ERR_ARG;
    setProtection(a,n,prot);return UC_ERR_OK;
}
uc_err uc_mem_unmap(uc_engine *u,uint64_t a,uint64_t n) {
    setProtection(a,n,0);return UC_ERR_OK;
}
uc_err uc_hook_add(uc_engine *u,uc_hook *h,int type,void *cb,void *user,uint64_t b,uint64_t e,...) {
    if(type==UC_HOOK_CODE) {u->dispatch=cb;u->user=user;}
    *h=1;return UC_ERR_OK;
}
static void bridge_dispatch(x64emu_t *emu,uintptr_t slot) {
    uc_engine *u=active;
    uint32_t sp=R_RSP;
    u->dispatch(u,slot&~1u,1,u->user);
    if(slot&1u) {double d;memcpy(&d,(void*)(sp-8),8);fpu_do_push(emu);ST0.d=d;}
}
void box64_write_slot(uc_engine *u,unsigned at,int fp) {
    onebridge_t b={0}; b.CC=0xcc;b.S='S';b.C='C';
    b.w=bridge_dispatch;b.f=at|(fp?1u:0u);b.C3=0xc3;
    memcpy((void*)at,&b,sizeof(b));
}
uc_err uc_emu_start(uc_engine *u,uint64_t begin,uint64_t end,uint64_t timeout,size_t count) {
    if(timeout || count)return UC_ERR_ARG;
    x64emu_t *emu=u->emu;
    if(*(uint32_t*)R_RSP!=end)return UC_ERR_ARG;
    *(uint32_t*)R_RSP=ctx->exit_bridge;
    R_RIP=begin;R_CS=0x23;emu->quit=0;emu->error=0;
    uc_engine *previous=active;active=u;thread_set_emu(emu);
    ++box_guest_running;
    EmuRun(emu,1,0);
    --box_guest_running;
    active=previous;
    int error=emu->error;emu->quit=0;
    return error?UC_ERR_EXCEPTION:UC_ERR_OK;
}
uc_err uc_context_alloc(uc_engine *u,uc_context **p) {
    *p=malloc(sizeof(**p));return *p?UC_ERR_OK:UC_ERR_NOMEM;
}
uc_err uc_context_save(uc_engine *u,uc_context *p) {
    memcpy(&p->state,u->emu,sizeof(p->state));return UC_ERR_OK;
}
uc_err uc_context_restore(uc_engine *u,uc_context *p) {
    memcpy(u->emu,&p->state,sizeof(p->state));return UC_ERR_OK;
}
uc_err uc_context_free(uc_context *p) {free(p);return UC_ERR_OK;}

/* Only Panthera-owned bridge records are supported by this experiment.
 * Linux i386 wrappers (the separate BOX32 build option) are not used. */
void x86Int3(x64emu_t *emu,uintptr_t *addr) {
    onebridge_t *b=(onebridge_t*)(*addr-1);
    if(b->CC!=0xcc || b->S!='S' || b->C!='C')abort();
    if(!b->w) {emu->quit=1;*addr+=10;R_RIP=*addr;return;}
    *addr=(uintptr_t)&b->C3;R_RIP=*addr;
    emu->df=d_none;
    b->w(emu,b->f);
}
