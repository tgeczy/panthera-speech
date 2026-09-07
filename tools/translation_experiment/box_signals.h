/* Shared by the two experimental adapters. Keep native runtime handlers for
 * threads outside guest execution; Box's handlers own translated execution.
 * The context is process-global and initialized exactly once. */
#include <signal.h>
#include <unistd.h>

static const int box_signals[] = {SIGSEGV, SIGBUS, SIGILL, SIGABRT};
static struct sigaction box_native_actions[4], box_guest_actions[4];
static __thread volatile sig_atomic_t box_guest_running;

static void box_signal_dispatch(int signal, siginfo_t *info, void *context)
{
    unsigned i;
    for (i = 0; i < 4 && box_signals[i] != signal; ++i) {}
    if (i == 4) _exit(128 + signal);
    const struct sigaction *action = box_guest_running
        ? &box_guest_actions[i] : &box_native_actions[i];
    if (action->sa_handler == SIG_IGN) return;
    if (action->sa_handler == SIG_DFL) {
        sigaction(signal, action, NULL);
        raise(signal);
        return;
    }
    if (action->sa_flags & SA_SIGINFO) action->sa_sigaction(signal, info, context);
    else action->sa_handler(signal);
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
        action.sa_flags |= SA_SIGINFO | (box_native_actions[i].sa_flags & SA_ONSTACK);
        sigaction(box_signals[i], &action, NULL);
    }
}
