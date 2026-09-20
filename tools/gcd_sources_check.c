/* No Apple data: concurrent dispatch-source allocation must never hand the
 * same live source to two callers. Build as a native i686 host translation
 * unit (same compiler/link dependencies as tiger_host.c). */
#define TIGER_LIB 1
#define TIGER_SHARED 1
#ifndef TIGER_CHECK_HOST
#define TIGER_CHECK_HOST "../src/tiger_host.c"
#endif
#include TIGER_CHECK_HOST

#define CHECK_THREADS 8
#define CHECK_ROUNDS 1000
static void *check_sources[CHECK_THREADS];
static volatile LONG check_arrived, check_phase, check_failures;

typedef struct {
    HANDLE entered, leave, cleaned;
    void *source;
    volatile LONG active, calls, errors;
    DWORD event_thread;
    int self_cancel;
} cleanup_check;

static void __cdecl check_event(void *arg)
{
    cleanup_check *c = (cleanup_check *)arg;
    c->event_thread = GetCurrentThreadId();
    InterlockedExchange(&c->active, 1);
    if (c->self_cancel) sh_dispatch_source_cancel(c->source);
    SetEvent(c->entered);
    if (WaitForSingleObject(c->leave, 5000) != WAIT_OBJECT_0)
        InterlockedIncrement(&c->errors);
    InterlockedExchange(&c->active, 0);
}

static void __cdecl check_cleanup(void *arg)
{
    cleanup_check *c = (cleanup_check *)arg;
    if (InterlockedExchangeAdd(&c->active, 0)) InterlockedIncrement(&c->errors);
    InterlockedIncrement(&c->calls);
    /* Snow Leopard releases the source from its cleanup callback. */
    sh_dispatch_release(c->source);
    SetEvent(c->cleaned);
}

static int check_cancel_cleanup(void)
{
    typedef void (__cdecl *setter)(void *, void (__cdecl *)(void *));
    setter set_cleanup = (setter)lookup_shim("_dispatch_source_set_cancel_handler_f");
    int kind;
    if (!set_cleanup) {
        fprintf(stderr, "dispatch cleanup: cancellation-handler import is missing\n");
        return 1;
    }
    for (kind = 0; kind < 3; ++kind) {
        cleanup_check c = {0};
        dsource *s;
        HANDLE thread;
        int failed;
        c.entered = CreateEvent(NULL, TRUE, FALSE, NULL);
        c.leave = CreateEvent(NULL, TRUE, FALSE, NULL);
        c.cleaned = CreateEvent(NULL, TRUE, FALSE, NULL);
        c.self_cancel = kind == 1;
        c.source = sh_dispatch_source_create(NULL, 0, 0,
            kind == 2 ? NULL : sh_dispatch_queue_create("cleanup-check", NULL));
        s = as_source(c.source);
        if (!s || !c.entered || !c.leave || !c.cleaned) return 2;
        sh_dispatch_set_context(s, &c);
        sh_dispatch_source_set_event_handler_f(s, check_event);
        set_cleanup(s, check_cleanup);
        /* Third case cancels a live, disarmed source without an event. */
        if (kind != 2) { s->armed = 1; s->delay_ms = 0; }
        sh_dispatch_resume(s);
        thread = s->thread;
        if (kind != 2 && WaitForSingleObject(c.entered, 5000) != WAIT_OBJECT_0)
            return 3;
        sh_dispatch_source_cancel(s);
        sh_dispatch_source_cancel(s);
        if (kind != 2 && WaitForSingleObject(c.cleaned, 0) == WAIT_OBJECT_0)
            InterlockedIncrement(&c.errors);
        SetEvent(c.leave);
        if (WaitForSingleObject(thread, 5000) != WAIT_OBJECT_0) return 4;
        failed = c.errors || c.calls != 1 || WaitForSingleObject(c.cleaned, 0) != WAIT_OBJECT_0;
        fprintf(stderr, "dispatch cleanup: case=%d callbacks=%ld errors=%ld\n",
                kind, (long)c.calls, (long)c.errors);
        CloseHandle(c.entered); CloseHandle(c.leave); CloseHandle(c.cleaned);
        /* The source pool owns and reaps its thread handle. */
        if (failed) return 5;
    }
    return 0;
}

static volatile LONG check_settle_started, check_settle_result;

static DWORD WINAPI check_settle_worker(LPVOID arg)
{
    (void)arg;
    InterlockedExchange(&check_settle_started, 1);
    check_settle_result = settle_cancelled_audio();
    return 0;
}

static int check_serial_delivery(void)
{
    cleanup_check c[3] = {0};
    HANDLE settling;
    void *queue = sh_dispatch_queue_create("serial-check", NULL);
    void *other = sh_dispatch_queue_create("independent-check", NULL);
    int i, failed = 0;
    /* Allocate before starting: no retired source can be reused while this
     * check still holds its pointer to inspect its thread's completion. */
    for (i = 0; i < 3; ++i) {
        dsource *s;
        c[i].entered = CreateEvent(NULL, TRUE, FALSE, NULL);
        c[i].leave = CreateEvent(NULL, TRUE, i != 0, NULL);
        c[i].self_cancel = 1;
        c[i].source = sh_dispatch_source_create(NULL, 0, 0, i == 2 ? other : queue);
        s = as_source(c[i].source);
        if (!s || !c[i].entered || !c[i].leave) return 1;
        sh_dispatch_set_context(s, &c[i]);
        sh_dispatch_source_set_event_handler_f(s, check_event);
        s->armed = 1; s->delay_ms = 0;
    }
    sh_dispatch_resume(c[0].source);
    if (WaitForSingleObject(c[0].entered, 5000) != WAIT_OBJECT_0) return 2;
    sh_dispatch_resume(c[1].source);
    sh_dispatch_resume(c[2].source);
    /* Another queue must advance while the first queue is blocked. */
    if (WaitForSingleObject(c[2].entered, 5000) != WAIT_OBJECT_0) failed = 1;
    if (WaitForSingleObject(c[1].entered, 50) != WAIT_TIMEOUT) failed = 1;
    /* No audio is queued, but A is still inside a guest callback and B is
     * waiting for its queue. Neither may be mistaken for a settled channel. */
    InitializeCriticalSection(&g_p_cs);
    settling = CreateThread(NULL, 0, check_settle_worker, NULL, 0, NULL);
    if (!settling) return 4;
    for (i = 0; !check_settle_started && i < 5000; ++i) Sleep(1);
    if (!check_settle_started || WaitForSingleObject(settling, 500) != WAIT_TIMEOUT)
        failed = 1;
    SetEvent(c[0].leave);
    for (i = 0; i < 3; ++i) {
        dsource *s = as_source(c[i].source);
        if (WaitForSingleObject(s->thread, 5000) != WAIT_OBJECT_0) return 3;
        if (c[i].errors) failed = 1;
        CloseHandle(c[i].entered); CloseHandle(c[i].leave);
    }
    if (WaitForSingleObject(settling, 5000) != WAIT_OBJECT_0) return 5;
    CloseHandle(settling);
    if (!check_settle_result || InterlockedExchangeAdd(&g_dispatch_inflight, 0))
        failed = 1;
    /* The guest context belongs to the queue, not to each short-lived timer. */
    if (!c[0].event_thread || c[0].event_thread != c[1].event_thread ||
        c[0].event_thread == c[2].event_thread) failed = 1;
    fprintf(stderr, "dispatch serial queues: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}

static void check_barrier(void)
{
    LONG phase = InterlockedExchangeAdd(&check_phase, 0);
    if (InterlockedIncrement(&check_arrived) == CHECK_THREADS) {
        InterlockedExchange(&check_arrived, 0);
        InterlockedIncrement(&check_phase);
    } else {
        while (InterlockedExchangeAdd(&check_phase, 0) == phase) Sleep(0);
    }
}

static DWORD WINAPI check_worker(LPVOID arg)
{
    int index = (int)(intptr_t)arg, round, i, j;
    for (round = 0; round < CHECK_ROUNDS; ++round) {
        check_barrier();
        check_sources[index] = sh_dispatch_source_create(NULL, 0, 0, NULL);
        check_barrier();
        if (!index) {
            for (i = 0; i < CHECK_THREADS; ++i) {
                if (!as_source(check_sources[i])) InterlockedIncrement(&check_failures);
                for (j = 0; j < i; ++j)
                    if (check_sources[i] == check_sources[j])
                        InterlockedIncrement(&check_failures);
            }
        }
        check_barrier();
        sh_dispatch_source_cancel(check_sources[index]);
        check_barrier();
    }
    return 0;
}

int main(void)
{
    HANDLE threads[CHECK_THREADS];
    int i;
    g_verbose = 0;
    for (i = 0; i < CHECK_THREADS; ++i) {
        threads[i] = CreateThread(NULL, 0, check_worker, (void *)(intptr_t)i, 0, NULL);
        if (!threads[i]) return 2;
    }
    for (i = 0; i < CHECK_THREADS; ++i) {
        if (WaitForSingleObject(threads[i], 60000) != WAIT_OBJECT_0) return 3;
        CloseHandle(threads[i]);
    }
    fprintf(stderr, "dispatch sources: %d concurrent allocations, %ld duplicate/invalid handles\n",
            CHECK_THREADS * CHECK_ROUNDS, (long)check_failures);
    if (check_failures) return 1;
    if (check_cancel_cleanup()) return 2;
    return check_serial_delivery();
}
