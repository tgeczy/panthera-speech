/* memprobe -- does a watch hold Alex's sample bank, and what does it cost?
 *
 * Panthera's engine memory-maps its voice data (tiger_host_files.c sh_mmap:
 * CreateFileMapping + MapViewOfFile, read-only, demand-paged; the malloc-read
 * path is only a fallback).  So "700 MB Alex" is 700 MB of *address space* --
 * resident pages arrive as the engine touches them, and clean file pages
 * evict under pressure without ever reaching zram.  The heap fallback is the
 * opposite: anonymous pages that DO compress into zram and then feed lmkd.
 *
 * This probe measures both so the watch's real number is grounded, not
 * guessed:
 *   mmap  <file> <size_mb> <touchall|sparse> [seconds]   -- the engine's case
 *   heap        <size_mb> <touchall|sparse> [seconds]     -- the fallback case
 *
 * touchall: read every page once.  Headline -- can the device even reach that
 *           resident size, and how fast does it page it in (flash bandwidth)?
 * sparse:   for N seconds, read random 32 KB runs (a diphone's worth) at random
 *           offsets.  Models sustained speech: major-fault RATE (flash latency
 *           in the speech path) and the resident plateau.
 *
 * Reports RSS / peak RSS, major+minor faults, swap and zram deltas, and the
 * memory pressure-stall line, before and after.
 *
 * CAVEAT built in on purpose: a shell-spawned process is not registered with
 * lmkd (oom_score_adj 0), so it survives pressure the real TTS service would
 * not -- these numbers are an upper bound on what the device tolerates.  If it
 * is killed anyway, `adb logcat -d -s lowmemorykiller lmkd` says so.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/resource.h>

#define PAGE 4096u
#define RUN  (32u * 1024u)   /* 32 KB: a contiguous diphone-sized read */

static double now_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

/* Pull "Key:   <number> kB" style values out of /proc files. */
static long proc_val(const char *path, const char *key) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char line[512];
    long v = -1;
    size_t klen = strlen(key);
    while (fgets(line, sizeof line, f)) {
        if (strncmp(line, key, klen) == 0) {
            v = strtol(line + klen, NULL, 10);
            break;
        }
    }
    fclose(f);
    return v;
}

static void dump_file(const char *label, const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { printf("  %s: (unavailable)\n", label); return; }
    char line[512];
    printf("  %s:\n", label);
    while (fgets(line, sizeof line, f)) printf("    %s", line);
    fclose(f);
}

/* xorshift so the walk is reproducible and needs no libc rng state. */
static uint64_t rng(uint64_t *s) {
    uint64_t x = *s; x ^= x << 13; x ^= x >> 7; x ^= x << 17; return *s = x;
}

struct snap { long rss, hwm, pswpin, pswpout, zram_used; struct rusage ru; double t; };

static long zram_mem_used(void) {
    /* /sys/block/zram0/mm_stat: orig compr mem_used_total ... (3rd field) */
    FILE *f = fopen("/sys/block/zram0/mm_stat", "r");
    if (!f) return -1;
    long a, b, c = -1;
    if (fscanf(f, "%ld %ld %ld", &a, &b, &c) != 3) c = -1;
    fclose(f);
    return c;
}

static void snap(struct snap *s) {
    s->rss = proc_val("/proc/self/status", "VmRSS:");
    s->hwm = proc_val("/proc/self/status", "VmHWM:");
    s->pswpin  = proc_val("/proc/vmstat", "pswpin ");
    s->pswpout = proc_val("/proc/vmstat", "pswpout ");
    s->zram_used = zram_mem_used();
    getrusage(RUSAGE_SELF, &s->ru);
    s->t = now_s();
}

static void report(const struct snap *a, const struct snap *b, uint64_t touched) {
    printf("---- result ----\n");
    printf("  elapsed:        %.3f s\n", b->t - a->t);
    printf("  bytes touched:  %.1f MB  (%.0f MB/s)\n",
           touched / 1048576.0, (touched / 1048576.0) / (b->t - a->t + 1e-9));
    printf("  VmRSS:          %ld -> %ld kB\n", a->rss, b->rss);
    printf("  VmHWM (peak):   %ld kB  (%.0f MB)\n", b->hwm, b->hwm / 1024.0);
    printf("  major faults:   %ld  (flash reads in the speech path)\n",
           b->ru.ru_majflt - a->ru.ru_majflt);
    printf("  minor faults:   %ld\n", b->ru.ru_minflt - a->ru.ru_minflt);
    if (a->pswpin  >= 0) printf("  zram swap-in:   %ld pages\n", b->pswpin  - a->pswpin);
    if (a->pswpout >= 0) printf("  zram swap-out:  %ld pages  (anon pages compressed away)\n",
                                b->pswpout - a->pswpout);
    if (a->zram_used >= 0) printf("  zram mem_used:  %ld -> %ld bytes\n", a->zram_used, b->zram_used);
    dump_file("pressure/memory (after)", "/proc/pressure/memory");
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr,
            "usage: memprobe mmap <file> <size_mb> <touchall|sparse> [seconds]\n"
            "       memprobe heap        <size_mb> <touchall|sparse> [seconds]\n");
        return 2;
    }

    int is_mmap = strcmp(argv[1], "mmap") == 0;
    const char *file = NULL;
    int ai = 2;
    if (is_mmap) file = argv[ai++];
    size_t size = (size_t)strtoul(argv[ai++], NULL, 10) * 1024u * 1024u;
    const char *pat = argv[ai++];
    double secs = (ai < argc) ? strtod(argv[ai++], NULL) : 20.0;
    int sparse = strcmp(pat, "sparse") == 0;

    printf("== memprobe %s  size=%zu MB  pattern=%s  %s ==\n",
           argv[1], size / 1048576, pat, sparse ? "" : "(single pass)");
    dump_file("meminfo", "/proc/meminfo");

    unsigned char *base;
    if (is_mmap) {
        int fd = open(file, O_RDONLY);
        if (fd < 0) { perror("open backing file"); return 1; }
        struct stat st;
        if (fstat(fd, &st) == 0 && (size_t)st.st_size < size) {
            fprintf(stderr, "backing file is %lld bytes, need %zu -- make it bigger\n",
                    (long long)st.st_size, size);
            return 1;
        }
        /* Drop this file from the page cache first (no root needed) so the
         * touches below are genuine flash major-faults, not cache hits. */
        posix_fadvise(fd, 0, size, POSIX_FADV_DONTNEED);
        base = mmap(NULL, size, PROT_READ, MAP_PRIVATE, fd, 0);
        if (base == MAP_FAILED) { perror("mmap"); return 1; }
        posix_madvise(base, size, POSIX_MADV_RANDOM);
    } else {
        base = mmap(NULL, size, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (base == MAP_FAILED) { perror("mmap anon"); return 1; }
        /* Prime every page so they are real anonymous pages zram can take. */
        for (size_t o = 0; o < size; o += PAGE) base[o] = 1;
    }

    struct snap a, b;
    snap(&a);
    volatile uint64_t sink = 0;
    uint64_t touched = 0, seed = 0x9e3779b97f4a7c15ULL;

    if (!sparse) {
        for (size_t o = 0; o < size; o += PAGE) { sink += base[o]; touched += PAGE; }
    } else {
        size_t npages = size / PAGE;
        double stop = now_s() + secs;
        while (now_s() < stop) {
            for (int i = 0; i < 4096; i++) {
                size_t p = (rng(&seed) % npages) * PAGE;
                size_t end = p + RUN; if (end > size) end = size;
                for (size_t o = p; o < end; o += PAGE) sink += base[o];
                touched += end - p;
            }
        }
    }
    snap(&b);
    (void)sink;
    report(&a, &b, touched);
    return 0;
}
