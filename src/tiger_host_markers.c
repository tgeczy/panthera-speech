/* Optional, request-scoped PCM positions for the TGR5 protocol.
 *
 * NotifySync runs at the sound output's QueueSamples frontier. Public speech
 * callbacks run later on a dispatch queue, where the collected PCM count no
 * longer describes their position. Count accepted output here instead. The
 * single silent frame queued from Wakeup starts an audio epoch; collect_slice
 * replaces it, so it is not part of the utterance's PCM. See speech-markers.md.
 * No object offsets or instruction patches: resolve methods in bounded,
 * named i386 virtual tables. Older protocols leave every method unchanged.
 */
#include <limits.h>
typedef struct { unsigned id, frame; } speech_marker;
static struct {
    CRITICAL_SECTION lock;
    int installed, active, failed;
    unsigned epoch, frontier, count, capacity, consumed;
    speech_marker *items;
    void *queue, *wakeup, *want, *notify;
} g_markers;
static __declspec(thread) unsigned marker_wakeup_depth;

/* These wrappers run INSIDE a guest call on emulated hosts. Ordinary
 * call_aligned would attempt to enter the already-running CPU again. */
static int marker_guest_call(void *fn, int argc, const unsigned *args)
{
#ifdef TIGER_UC
    return uc_call_nested_args(fn, argc, args);
#else
    void *a = (void *)(uintptr_t)args[0];
    if (argc == 1) return call_aligned1(fn, a);
    if (argc == 2) return call_aligned2(fn, a, (void *)(uintptr_t)args[1]);
    if (argc == 3) return call_aligned3(fn, a, (void *)(uintptr_t)args[1],
                                     (void *)(uintptr_t)args[2]);
    return call_aligned4(fn, a, (void *)(uintptr_t)args[1],
                         (void *)(uintptr_t)args[2], (void *)(uintptr_t)args[3]);
#endif
}

static int __cdecl marker_queue(void *self, const float *samples,
                                unsigned n, unsigned flags)
{
    unsigned epoch, args[4] = {(unsigned)(uintptr_t)self,
        (unsigned)(uintptr_t)samples, n, flags};
    int active, result, wake = marker_wakeup_depth != 0;
    EnterCriticalSection(&g_markers.lock);
    active = g_markers.active;
    epoch = g_markers.epoch;
    if (active && wake && (n != 1 || !samples || samples[0] != 0.0f || flags))
        g_markers.failed = 1;
    LeaveCriticalSection(&g_markers.lock);
    result = marker_guest_call(g_markers.queue, 4, args);
    EnterCriticalSection(&g_markers.lock);
    if (active && g_markers.active && epoch == g_markers.epoch && !wake) {
        if (n > UINT_MAX - g_markers.frontier) g_markers.failed = 1;
        else g_markers.frontier += n;
    }
    LeaveCriticalSection(&g_markers.lock);
    /* The Boolean result means queue capacity, not the number accepted. */
    return result;
}

static void __cdecl marker_wakeup(void *self, void *callback)
{
    unsigned args[2] = {(unsigned)(uintptr_t)self, (unsigned)(uintptr_t)callback};
    ++marker_wakeup_depth;
    marker_guest_call(g_markers.wakeup, 2, args);
    --marker_wakeup_depth;
}

static int __cdecl marker_want(void *self)
{
    unsigned args[1] = {(unsigned)(uintptr_t)self};
    int active;
    EnterCriticalSection(&g_markers.lock);
    active = g_markers.active;
    LeaveCriticalSection(&g_markers.lock);
    return active ? 1 : marker_guest_call(g_markers.want, 1, args);
}

static void __cdecl marker_notify(void *self, unsigned id, int time)
{
    unsigned args[3] = {(unsigned)(uintptr_t)self, id, (unsigned)time};
    int active;
    EnterCriticalSection(&g_markers.lock);
    active = g_markers.active;
    if (active) {
        if (time || g_markers.count == g_markers.capacity)
            g_markers.failed = 1;
        else {
            speech_marker *m = &g_markers.items[g_markers.count++];
            m->id = id;
            m->frame = g_markers.frontier;
        }
    }
    LeaveCriticalSection(&g_markers.lock);
    if (!active) marker_guest_call(g_markers.notify, 3, args);
}

/* Bound a table by both its section and the next symbol in that section.
 * Slots are guest words even when the host's pointers are eight bytes wide. */
static unsigned *marker_slot(image *im, const char *table, void *method)
{
    const nlist *sym = NULL;
    unsigned i, end, *found = NULL, *slots, words;
    if (!method) return NULL;
    for (i = 0; i < im->nsyms; ++i) {
        const nlist *s = &im->syms[i];
        if (!(s->n_type & N_STAB) && (s->n_type & N_TYPE) == 0x0e &&
            s->n_strx && !strcmp(im->strs + s->n_strx, table)) { sym = s; break; }
    }
    if (!sym || !sym->n_sect || sym->n_sect > im->nsects) return NULL;
    {
        const section *sec = &im->sects[sym->n_sect - 1];
        if (sec->size > UINT_MAX - sec->addr) return NULL;
        end = sec->addr + sec->size;
        if (sym->n_value < sec->addr || sym->n_value >= end) return NULL;
    }
    for (i = 0; i < im->nsyms; ++i) {
        const nlist *s = &im->syms[i];
        if (!(s->n_type & N_STAB) && (s->n_type & N_TYPE) == 0x0e &&
            s->n_sect == sym->n_sect && s->n_value > sym->n_value && s->n_value < end)
            end = s->n_value;
    }
    slots = (unsigned *)(uintptr_t)(sym->n_value + im->slide);
    words = (end - sym->n_value) / 4;
    for (i = 2; i < words; ++i) {
        if (slots[i] == (unsigned)(uintptr_t)method) {
            if (found) return NULL; /* ambiguous layout */
            found = &slots[i];
        }
    }
    return found;
}

static int markers_install(image *im)
{
    static const char *tables[] = {"__ZTV24MTBEAudioUnitSoundOutput",
        "__ZTV24MTBEAudioUnitSoundOutput", "__ZTV12MT3BNotifier",
        "__ZTV12MT3BNotifier", "__ZTV12MTPBNotifier", "__ZTV12MTPBNotifier"};
    static const char *methods[] = {
        "__ZN24MTBEAudioUnitSoundOutput12QueueSamplesEPKfmm",
        "__ZN24MTBEAudioUnitSoundOutput6WakeupEP23MTBESoundOutputCallback",
        "__ZN12MT3BNotifier8WantSyncEv", "__ZN12MT3BNotifier10NotifySyncEml"};
    void *replacements[] = {marker_queue, marker_wakeup, marker_want, marker_notify};
    void *originals[4];
    unsigned *slots[6], targets[4], n = 4, i;
    if (g_markers.installed) return 1;
    for (i = 0; i < 4; ++i) originals[i] = find_export(im, methods[i]);
    if (find_export(im, tables[4])) n = 6;
    for (i = 0; i < n; ++i) {
        slots[i] = marker_slot(im, tables[i], originals[i < 4 ? i : i - 2]);
        if (!slots[i]) return 0;
    }
    for (i = 0; i < 4; ++i) {
#ifdef TIGER_UC
        targets[i] = (unsigned)(uintptr_t)uc_bind_target(methods[i], replacements[i]);
#else
        targets[i] = (unsigned)(uintptr_t)replacements[i];
#endif
        if (!targets[i]) return 0;
    }
    /* The loader owns these pages and maps every image segment writable for
     * relocations (load_image, including the identity-mapped guest). All
     * table validation precedes mutation; no platform protection API needed. */
    InitializeCriticalSection(&g_markers.lock);
    g_markers.queue = originals[0]; g_markers.wakeup = originals[1];
    g_markers.want = originals[2]; g_markers.notify = originals[3];
    for (i = 0; i < n; ++i) *slots[i] = targets[i < 4 ? i : i - 2];
    g_markers.installed = 1;
    return 1;
}

static int markers_begin(image *im, unsigned textlen)
{
#ifdef TIGER_UC
    /* Native PCM equivalence is tested. Under Unicorn, the Leopard Alex
     * paragraph control differed despite a consistent producer timeline.
     * Until that is understood, refuse before synthesis and let clients use
     * boundary marks. Android's JNI path does not request this protocol. */
    (void)im; (void)textlen;
    return 0;
#else
    speech_marker *items;
    unsigned capacity = textlen / 8 + 1; /* even the shortest sync needs this */
    if (!markers_install(im)) return 0;
    items = (speech_marker *)calloc(capacity, sizeof(*items));
    if (!items) return 0;
    EnterCriticalSection(&g_markers.lock);
    free(g_markers.items);
    g_markers.items = items; g_markers.capacity = capacity;
    g_markers.count = g_markers.consumed = g_markers.frontier = 0;
    g_markers.failed = 0; ++g_markers.epoch; g_markers.active = 1;
    LeaveCriticalSection(&g_markers.lock);
    return 1;
#endif
}

static void markers_end(void)
{
    if (!g_markers.installed) return;
    EnterCriticalSection(&g_markers.lock);
    g_markers.active = 0;
    ++g_markers.epoch;
    LeaveCriticalSection(&g_markers.lock);
}
