/* Shared by the two adapters. Keep native runtime handlers for
 * threads outside guest execution; Box's handlers own translated execution.
 * The context is process-global and initialized exactly once. */
#include <signal.h>
#include <ucontext.h>
#include <unistd.h>

static const int box_signals[] = {SIGSEGV, SIGBUS, SIGILL, SIGABRT};
static struct sigaction box_native_actions[4], box_guest_actions[4];
static unsigned box_native_reset[4], box_guest_reset[4];
static __thread volatile sig_atomic_t box_guest_running;

static void box_signal_dispatch(int signal, siginfo_t *info, void *context)
{
    unsigned i;
    for (i = 0; i < 4 && box_signals[i] != signal; ++i) {}
    if (i == 4) _exit(128 + signal);
    const struct sigaction *action = box_guest_running
        ? &box_guest_actions[i] : &box_native_actions[i];
    struct sigaction reset_action = {0};
    if (action->sa_flags & SA_RESETHAND) {
        unsigned *reset = box_guest_running ? &box_guest_reset[i] : &box_native_reset[i];
        if (__atomic_exchange_n(reset, 1, __ATOMIC_RELAXED)) {
            reset_action.sa_handler = SIG_DFL;
            sigemptyset(&reset_action.sa_mask);
            action = &reset_action;
        }
    }
    if (action->sa_handler == SIG_IGN) return;
    if (action->sa_handler == SIG_DFL) {
        sigaction(signal, action, NULL);
        /* A synchronous fault retries its original instruction, preserving
         * the fault location. Explicitly sent signals must be re-delivered. */
        if (!info || info->si_code <= 0) raise(signal);
        return;
    }
    /* The installed dispatcher cannot have two different kernel masks.
     * Start with the interrupted thread's mask, then apply the selected
     * handler's mask and deferral policy while it runs. */
    sigset_t mask = ((ucontext_t *)context)->uc_sigmask, previous;
    for (int s=1; s<NSIG; ++s)
        if (sigismember(&action->sa_mask, s) == 1) sigaddset(&mask, s);
    if (!(action->sa_flags & SA_NODEFER)) sigaddset(&mask, signal);
    sigprocmask(SIG_SETMASK, &mask, &previous);
    if (action->sa_flags & SA_SIGINFO) action->sa_sigaction(signal, info, context);
    else action->sa_handler(signal);
    sigprocmask(SIG_SETMASK, &previous, NULL);
}

static void box_capture_native_signals(void)
{
    for (unsigned i = 0; i < 4; ++i)
        sigaction(box_signals[i], NULL, &box_native_actions[i]);
}

static void box_share_signals(void)
{
    for (unsigned i = 0; i < 4; ++i) {
        sigaction(box_signals[i], NULL, &box_guest_actions[i]);
        struct sigaction action = box_guest_actions[i];
        action.sa_sigaction = box_signal_dispatch;
        /* Keep the native runtime's interrupted-syscall policy. Guest
         * translation faults retry or leave through Box's own context. */
        action.sa_flags = SA_SIGINFO | SA_NODEFER |
            (box_native_actions[i].sa_flags & SA_RESTART) |
            ((box_guest_actions[i].sa_flags | box_native_actions[i].sa_flags) & SA_ONSTACK);
        sigemptyset(&action.sa_mask);
        sigaction(box_signals[i], &action, NULL);
    }
}
