package com.pantheraspeech.tts

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class PantheraRequestTest {
    @Test fun cancellationDetachesImmediatelyAndWaitsBeforeReleasingOwnership() {
        val retired = ArrayList<Int>()
        val work = ArrayList<() -> Unit>()
        var rendered = false
        val request = PantheraRequest<Int>({ retired.add(it) }, { _, grace -> assertTrue(grace); rendered }, { work.add(it) })
        assertTrue(request.attach(1))
        request.started(allowGrace = true)
        request.cancel()
        request.cancel()
        assertNull(request.current())
        assertEquals(1, work.size)
        assertFalse(request.attach(2))

        val entered = CountDownLatch(1)
        val released = CountDownLatch(1)
        val completion = Thread { entered.countDown(); request.complete(); released.countDown() }
        completion.start()
        try {
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            assertFalse(released.await(30, TimeUnit.MILLISECONDS))
            rendered = true // engine finishes during the deferred decision
        } finally { work.single().invoke(); completion.join(1000) }
        assertEquals(0L, released.count)
        assertTrue(retired.isEmpty())
        request.cancel() // late stops cannot retire the now reusable worker
        assertEquals(1, work.size)
    }

    @Test fun unfinishedWorkIsRetiredExactlyOnceBeforeCompleteReturns() {
        val retired = ArrayList<Int>()
        val work = ArrayList<() -> Unit>()
        val request = PantheraRequest<Int>({ retired.add(it) }, { _, grace -> assertFalse(grace); false }, { work.add(it) })
        assertTrue(request.attach(3)); request.started()
        request.cancel(); request.cancel()
        work.single().invoke()
        request.complete()
        assertEquals(listOf(3), retired)
    }

    @Test fun cancellationDuringStartCannotUseThePreviousUtterancesCompletion() {
        var queried = false
        val retired = ArrayList<Int>()
        val work = ArrayList<() -> Unit>()
        val request = PantheraRequest<Int>({ retired.add(it) }, { _, _ -> queried = true; true }, { work.add(it) })
        assertTrue(request.attach(4))
        request.cancel(); request.started()
        work.single().invoke(); request.complete()
        assertFalse(queried)
        assertEquals(listOf(4), retired)
    }

    @Test fun failedCompletionQueryRetiresRatherThanReusingUnknownState() {
        val retired = ArrayList<Int>()
        val request = PantheraRequest<Int>({ retired.add(it) }, { _, _ -> error("worker disappeared") })
        assertTrue(request.attach(5)); request.started(); request.cancel(); request.complete()
        assertEquals(listOf(5), retired)
    }

    @Test fun stoppedBeforeAttachmentAndStoppedAfterCompletionDoNotRetire() {
        val retired = ArrayList<Int>()
        val before = PantheraRequest<Int>({ retired.add(it) })
        before.cancel(); assertFalse(before.attach(6)); before.complete()
        val after = PantheraRequest<Int>({ retired.add(it) })
        assertTrue(after.attach(6)); after.started(); after.complete(); after.cancel()
        assertTrue(retired.isEmpty())
    }

    @Test fun interruptedCleanupStillWaitsForItsCancellationDecision() {
        val work = ArrayList<() -> Unit>()
        val request = PantheraRequest<Int>({}, { _, _ -> true }, { work.add(it) })
        assertTrue(request.attach(7)); request.started(); request.cancel()
        val entered = CountDownLatch(1)
        val released = CountDownLatch(1)
        var interruptRestored = false
        val completion = Thread {
            Thread.currentThread().interrupt()
            entered.countDown()
            request.complete()
            interruptRestored = Thread.currentThread().isInterrupted
            released.countDown()
        }
        completion.start()
        try {
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            assertFalse(released.await(30, TimeUnit.MILLISECONDS))
        } finally { work.single().invoke(); completion.join(1000) }
        assertEquals(0L, released.count)
        assertTrue(interruptRestored)
    }
}
