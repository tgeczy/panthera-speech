package com.pantheraspeech.tts

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class PantheraCleanupTest {
    @Test fun stopReleasesPendingPullAndFinishRunsOnItsOwningThread() {
        val executor = Executors.newSingleThreadExecutor()
        val pulling = CountDownLatch(1)
        val stopped = CountDownLatch(1)
        var owner: Thread? = null
        executor.submit { owner = Thread.currentThread(); pulling.countDown(); stopped.await() }
        try {
            assertTrue(pulling.await(1, TimeUnit.SECONDS))
            assertTrue(PantheraCleanup.attempt(executor, 1000,
                stop = { stopped.countDown() },
                finish = { assertSame(owner, Thread.currentThread()); true }))
        } finally { stopped.countDown(); executor.shutdownNow() }
    }

    @Test fun timeoutRefusesReuseWithoutInterruptingNativeCleanup() {
        val executor = Executors.newSingleThreadExecutor()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        var interrupted = false
        try {
            assertFalse(PantheraCleanup.attempt(executor, 40, stop = {}, finish = {
                entered.countDown()
                try { release.await() } catch (_: InterruptedException) { interrupted = true }
                finished.countDown(); true
            }))
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            assertEquals(1L, finished.count)
            release.countDown()
            assertTrue(finished.await(1, TimeUnit.SECONDS))
            assertFalse(interrupted)
        } finally { release.countDown(); executor.shutdownNow() }
    }

    @Test fun cleanupFailureAndIncompleteStateBothRefuseReuse() {
        val executor = Executors.newSingleThreadExecutor()
        try {
            assertFalse(PantheraCleanup.attempt(executor, 1000, {}, { error("failed") }))
            assertFalse(PantheraCleanup.attempt(executor, 1000, {}, { false }))
        } finally { executor.shutdownNow() }
    }
}
