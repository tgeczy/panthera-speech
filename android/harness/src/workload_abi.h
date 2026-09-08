/* Shared contract between the i386 workload blob and every driver that runs it
 * (desktop Python-unicorn, device C-unicorn).  All addresses are guest-absolute;
 * the blob is compiled -fno-pic and loaded at these fixed bases. */
#ifndef WORKLOAD_ABI_H
#define WORKLOAD_ABI_H

#define CODE_BASE    0x00100000u   /* run() machine code loaded here; EIP starts here */
#define STACK_TOP    0x00090000u   /* ESP set just below; one guest stack page        */
#define RET_MAGIC    0x000a0000u   /* pushed as run()'s return addr; emu stops here    */
#define DATA_BASE    0x01000000u   /* pointer-chase table                             */
#define RESULT_ADDR  0x02000000u   /* run() writes its final accumulator here          */
#define STEPS_ADDR   0x02000004u   /* driver writes step count here (same page)        */

#define TABLE_WORDS  (1u << 20)    /* 1M uint32 = 4 MB: past L2, real cache misses     */
/* Driver fills TABLE with an affine full-period map tab[i]=(a*i+c)&(TABLE_WORDS-1),
 * a%4==1, c odd -> one cycle over all 4 MB, no fixed point.  Step count is set at
 * STEPS_ADDR so a slow device can run fewer steps without recompiling the blob. */

#endif
