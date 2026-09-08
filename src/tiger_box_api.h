/* Panthera's narrow identity-mapped guest interface for the Box adapters.
 * These declarations describe our caller/adapter contract. The familiar uc_
 * names let the existing host dispatch remain shared; the identifiers below
 * are deliberately local and are NOT Unicorn's binary ABI. Box builds need
 * neither Unicorn headers nor its library. */
#ifndef TIGER_BOX_API_H
#define TIGER_BOX_API_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

typedef struct uc_struct uc_engine;
typedef struct uc_context uc_context;
typedef uintptr_t uc_hook;
typedef enum { UC_ARCH_X86 = 1 } uc_arch;
typedef enum { UC_MODE_32 = 1 } uc_mode;
typedef enum {
    UC_ERR_OK, UC_ERR_ARCH, UC_ERR_ARG, UC_ERR_NOMEM, UC_ERR_MAP, UC_ERR_EXCEPTION
} uc_err;
typedef enum {
    UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED, UC_MEM_FETCH_UNMAPPED
} uc_mem_type;
enum {
    UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
    UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EIP
};
enum {
    UC_HOOK_CODE = 1, UC_HOOK_BLOCK = 2, UC_HOOK_MEM_READ_UNMAPPED = 4,
    UC_HOOK_MEM_WRITE_UNMAPPED = 8, UC_HOOK_MEM_FETCH_UNMAPPED = 16,
    UC_PROT_ALL = 7
};

uc_err uc_open(uc_arch, uc_mode, uc_engine **);
uc_err uc_close(uc_engine *);
const char *uc_strerror(uc_err);
uc_err uc_reg_read(uc_engine *, int, void *);
uc_err uc_reg_write(uc_engine *, int, const void *);
uc_err uc_mem_read(uc_engine *, uint64_t, void *, uint64_t);
uc_err uc_mem_write(uc_engine *, uint64_t, const void *, uint64_t);
uc_err uc_mem_map_ptr(uc_engine *, uint64_t, uint64_t, uint32_t, void *);
uc_err uc_mem_unmap(uc_engine *, uint64_t, uint64_t);
uc_err uc_hook_add(uc_engine *, uc_hook *, int, void *, void *, uint64_t, uint64_t, ...);
uc_err uc_emu_start(uc_engine *, uint64_t, uint64_t, uint64_t, size_t);
uc_err uc_context_alloc(uc_engine *, uc_context **);
uc_err uc_context_save(uc_engine *, uc_context *);
uc_err uc_context_restore(uc_engine *, uc_context *);
uc_err uc_context_free(uc_context *);
#endif
