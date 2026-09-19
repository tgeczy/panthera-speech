package com.pantheraspeech.tts

import java.util.concurrent.CountDownLatch
import java.util.concurrent.RejectedExecutionException

/** Cancellation belongs to this utterance, including time spent opening its
 * worker. Stop detaches it immediately; a deferred reuse decision must settle
 * before complete releases synthesis ownership to another request. A late
 * stop or deferred retirement can therefore never kill a reused worker. */
internal class PantheraRequest<T>(private val retire: (T) -> Unit,
                                  private val canReuse: (T, Boolean) -> Boolean = { _, _ -> false },
                                  private val dispatchCancel: (() -> Unit) -> Unit = { it() }) {
    private var cancelled = false
    private var completed = false
    private var resource: T? = null
    private var started = false
    private var allowGrace = false
    private var cancellation: CountDownLatch? = null

    @Synchronized fun attach(value: T): Boolean {
        if (cancelled || completed) return false
        resource = value
        started = false
        return true
    }
    @Synchronized fun started(allowGrace: Boolean = false) {
        if (!cancelled && !completed) {
            started = true
            this.allowGrace = allowGrace
        }
    }
    fun cancel() {
        val work = synchronized(this) {
            if (cancelled || completed) return
            cancelled = true
            val value = resource ?: return
            resource = null
            val acknowledged = started
            val waitForCompletion = allowGrace
            val settled = CountDownLatch(1)
            cancellation = settled
            val action: () -> Unit = {
                try {
                    val reusable = acknowledged && try { canReuse(value, waitForCompletion) } catch (_: Exception) { false }
                    if (!reusable) retire(value)
                } finally { settled.countDown() }
            }
            action
        }
        try { dispatchCancel(work) }
        catch (_: RejectedExecutionException) { work() }
    }
    @Synchronized fun current(): T? = resource
    fun complete() {
        val settled = synchronized(this) {
            completed = true
            resource = null
            cancellation
        } ?: return
        // Interruption cannot release the worker while its old cancellation
        // still owns a possible retirement. Restore the interrupt afterwards.
        var interrupted = false
        while (true) {
            try { settled.await(); break }
            catch (_: InterruptedException) { interrupted = true }
        }
        if (interrupted) Thread.currentThread().interrupt()
    }
}
