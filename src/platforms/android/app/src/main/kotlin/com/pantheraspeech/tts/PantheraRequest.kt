package com.pantheraspeech.tts

/** Cancellation belongs to this utterance, including time spent opening its
 * worker. Retirement is synchronous with ownership changes, but never waits
 * for synthesis or process death. A completed request cannot retire a reused
 * worker when a late stop arrives. */
internal class PantheraRequest<T>(private val retire: (T) -> Unit,
                                  private val canReuse: (T) -> Boolean = { false }) {
    private var cancelled = false
    private var completed = false
    private var resource: T? = null
    private var started = false

    @Synchronized fun attach(value: T): Boolean {
        if (cancelled || completed) return false
        resource = value
        started = false
        return true
    }
    @Synchronized fun started() { if (!cancelled && !completed) started = true }
    @Synchronized fun cancel() {
        if (cancelled || completed) return
        cancelled = true
        try { resource?.let { if (!started || !canReuse(it)) retire(it) } }
        finally { resource = null }
    }
    @Synchronized fun current(): T? = resource
    @Synchronized fun complete() {
        completed = true
        resource = null
    }
}
