/* Experimental adapter for Panthera's existing emulator seam. Not a general
 * Unicorn implementation: identity mappings and synchronous calls only. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unicorn/unicorn.h>
#include "box86context.h"
#include "box86lib.h"
#include "x86emu.h"
#include "dynarec.h"
#include "threads.h"
#include "x86run.h"
#include "wrapper.h"
#include "custommem.h"
#include "emu/x86emu_private.h"
#include "emu/x87emu_private.h"
#include "tools/bridge_private.h"
#include "box_signals.h"
extern void thread_set_emu(x86emu_t *);
struct uc_struct {
    x86emu_t *emu;
    void (*dispatch)(uc_engine*, uint64_t, uint32_t, void*);
    void *user;
};
struct uc_context { x86emu_t state; };
static box86context_t *ctx;
static pthread_once_t once=PTHREAD_ONCE_INIT;
static __thread uc_engine *active;
static void init(void) {
    box_capture_native_signals();
    LoadLogEnv(); ctx=NewBox86Context(0); LoadEnvVars(ctx);
    box_share_signals();
}
uc_err uc_open(uc_arch arch, uc_mode mode, uc_engine **out) {
    if(arch!=UC_ARCH_X86 || mode!=UC_MODE_32) return UC_ERR_ARCH;
    pthread_once(&once,init);
    uc_engine *u=calloc(1,sizeof(*u));
    u->emu=NewX86Emu(ctx,0,0,0,0); SetupX86Emu(u->emu);
    thread_set_emu(u->emu);*out=u; return UC_ERR_OK;
}
uc_err uc_close(uc_engine *u) {
    clean_current_emuthread(); free(u); return UC_ERR_OK;
}
const char *uc_strerror(uc_err e) {return e==UC_ERR_OK?"OK":"Box86 experiment fault";}
static uint32_t *reg(uc_engine *u,int id) {
    x86emu_t *emu=u->emu;
    switch(id) {
    case UC_X86_REG_EAX:return &R_EAX; case UC_X86_REG_EBX:return &R_EBX;
    case UC_X86_REG_ECX:return &R_ECX; case UC_X86_REG_EDX:return &R_EDX;
    case UC_X86_REG_ESI:return &R_ESI; case UC_X86_REG_EDI:return &R_EDI;
    case UC_X86_REG_EBP:return &R_EBP; case UC_X86_REG_ESP:return &R_ESP;
    case UC_X86_REG_EIP:return &R_EIP;
    default:return NULL;
    }
}
uc_err uc_reg_read(uc_engine *u,int id,void *v) {
    uint32_t *r=reg(u,id); if(!r)return UC_ERR_ARG; memcpy(v,r,4);return UC_ERR_OK;
}
uc_err uc_reg_write(uc_engine *u,int id,const void *v) {
    uint32_t *r=reg(u,id); if(!r)return UC_ERR_ARG; memcpy(r,v,4);return UC_ERR_OK;
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
static void bridge_dispatch(x86emu_t *emu,uintptr_t slot) {
    uc_engine *u=active;
    uint32_t sp=R_ESP;
    u->dispatch(u,slot&~1u,1,u->user);
    if(slot&1u) {double d;memcpy(&d,(void*)(sp-8),8);fpu_do_push(emu);ST0.d=d;}
}
void box86_write_slot(uc_engine *u,unsigned at,int fp) {
    onebridge_t b={0}; b.CC=0xcc;b.S='S';b.C='C';
    b.w=bridge_dispatch;b.f=at|(fp?1u:0u);b.C3=0xc3;
    memcpy((void*)at,&b,sizeof(b));
}
uc_err uc_emu_start(uc_engine *u,uint64_t begin,uint64_t end,uint64_t timeout,size_t count) {
    if(timeout || count)return UC_ERR_ARG;
    x86emu_t *emu=u->emu;
    if(*(uint32_t*)R_ESP!=end)return UC_ERR_ARG;
    *(uint32_t*)R_ESP=ctx->exit_bridge;
    R_EIP=begin;emu->quit=0;emu->error=0;
    uc_engine *previous=active;active=u;thread_set_emu(emu);
    ++box_guest_running;
    DynaRun(emu);
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
