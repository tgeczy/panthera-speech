package com.pantheraspeech.tts

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.util.Log
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Deterministic ownership boundaries plus real Binder retire/rebind cycles.
 * No preferences or engine data are needed for these lifecycle checks. */
internal object PantheraWorkerCheck {
    fun run(test: Instrumentation) {
        val result = Bundle()
        try {
            val retired = mutableListOf<Int>()
            val early = PantheraRequest<Int> { retired.add(it) }
            early.cancel()
            check(!early.attach(1) && retired.isEmpty()) // stop during open

            val active = PantheraRequest<Int> { retired.add(it) }
            check(active.attach(2))
            active.cancel(); active.cancel()
            check(retired == listOf(2) && active.current() == null)
            check(!active.attach(3)) // cannot start a cancelled request

            val complete = PantheraRequest<Int> { retired.add(it) }
            check(complete.attach(4))
            val later = CountDownLatch(1)
            val finished = CountDownLatch(1)
            val stop = Thread {
                check(later.await(5, TimeUnit.SECONDS))
                complete.cancel()
                finished.countDown()
            }
            stop.start()
            complete.complete()
            val replacement = PantheraRequest<Int> { retired.add(it) }
            check(replacement.attach(4)) // same warm worker, different request
            later.countDown()
            check(finished.await(5, TimeUnit.SECONDS))
            check(retired == listOf(2) && replacement.current() == 4)
            replacement.complete()

            val ctx = test.targetContext
            var previous: IPantheraWorker? = null
            repeat(8) {
                val worker = PantheraWorkers.get(ctx, "leopard")
                check(worker.asBinder().isBinderAlive)
                check(worker.asBinder() !== previous?.asBinder())
                check(PantheraWorkers.get(ctx, "leopard").asBinder() === worker.asBinder())
                previous?.let(PantheraWorkers::retire) // stale stop cannot kill this one
                check(worker.asBinder().isBinderAlive)
                val died = CountDownLatch(1)
                worker.asBinder().linkToDeath({ died.countDown() }, 0)
                PantheraWorkers.retire(worker)
                check(died.await(5, TimeUnit.SECONDS)) { "Retired worker stayed alive" }
                previous = worker
            }
            result.putString("stream", "PASS worker ownership and 8 Binder retire/rebind cycles")
            test.finish(Activity.RESULT_OK, result)
        } catch (e: Throwable) {
            result.putString("stream", "FAIL worker lifecycle: ${Log.getStackTraceString(e)}")
            test.finish(Activity.RESULT_CANCELED, result)
        }
    }
}
