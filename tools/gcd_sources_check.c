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
    return check_failures ? 1 : 0;
}
