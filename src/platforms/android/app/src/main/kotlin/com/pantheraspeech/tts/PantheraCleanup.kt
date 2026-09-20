package com.pantheraspeech.tts

import java.util.concurrent.Callable
import java.util.concurrent.ExecutorService
import java.util.concurrent.TimeUnit

/** A failed/expired cleanup leaves ownership with the caller, which must retire
 * the worker. Never interrupt a native cleanup: it may still own guest locks. */
internal object PantheraCleanup {
    fun attempt(executor: ExecutorService, timeoutMs: Long,
                stop: () -> Unit, finish: () -> Boolean): Boolean {
        return try {
            stop() // Only the thread-safe stop flag; release a pending pull.
            executor.submit(Callable { finish() }).get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        } catch (_: Exception) {
            // Do not cancel the future or interrupt the synthesis thread.
            // Returning false requires retiring this entire worker process.
            false
        }
    }
}
