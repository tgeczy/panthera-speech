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
            val early = PantheraRequest<Int>(retire = { retired.add(it) })
            early.cancel()
            check(!early.attach(1) && retired.isEmpty()) // stop during open

            val active = PantheraRequest<Int>(retire = { retired.add(it) })
            check(active.attach(2))
            active.cancel(); active.cancel()
            check(retired == listOf(2) && active.current() == null)
            check(!active.attach(3)) // cannot start a cancelled request

            val complete = PantheraRequest<Int>(retire = { retired.add(it) })
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
            val replacement = PantheraRequest<Int>(retire = { retired.add(it) })
            check(replacement.attach(4)) // same warm worker, different request
            later.countDown()
            check(finished.await(5, TimeUnit.SECONDS))
            check(retired == listOf(2) && replacement.current() == 4)
            replacement.complete()

            // An idle result before start is acknowledged belongs to the
            // preceding request. It cannot authorize reusing this worker.
            val notStarted = PantheraRequest<Int>({ retired.add(it) }, { true })
            check(notStarted.attach(5)); notStarted.cancel(); notStarted.started()
            check(retired == listOf(2, 5) && !notStarted.attach(5))
            val rendered = PantheraRequest<Int>({ retired.add(it) }, { true })
            check(rendered.attach(6)); rendered.started(); rendered.cancel()
            check(retired == listOf(2, 5) && rendered.current() == null)
            val rendering = PantheraRequest<Int>({ retired.add(it) }, { false })
            check(rendering.attach(7)); rendering.started(); rendering.cancel()
            check(retired == listOf(2, 5, 7))

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

    /** Model Android keeping PCM queued after native synthesis completes. */
    fun runReuse(test: Instrumentation) {
        val result = Bundle()
        try {
            val ctx = test.targetContext
            for (gen in PantheraEngine.availableGens(ctx)) {
                val voices = PantheraEngine.scanVoices(ctx, gen)
                val voice = voices.firstOrNull { it.name == "Alex" } ?: voices.first { it.name == "Fred" }
                val reference = checkNotNull(PantheraEngine.render(ctx, voice, "Seven", 180))
                repeat(4) {
                    var old: IPantheraWorker? = null
                    PantheraEngine.withSynthesis {
                        check(PantheraEngine.speakStart(ctx, voice,
                            PantheraText.bytes("Restart with debug logging enabled."), 180) == 0)
                        val api = PantheraWorkers.get(ctx, gen)
                        old = api
                        val deadline = android.os.SystemClock.elapsedRealtime() + 10000
                        // Deliberately do not drain PCM: it is still queued for
                        // playback when the user's stop arrives.
                        while (!api.renderComplete() && android.os.SystemClock.elapsedRealtime() < deadline)
                            Thread.sleep(5)
                        check(api.renderComplete()) { "$gen did not finish rendering" }
                        PantheraEngine.stop()
                    }
                    check(PantheraWorkers.get(ctx, gen).asBinder() === old?.asBinder()) {
                        "$gen restarted a completed renderer"
                    }
                    val replacement = checkNotNull(PantheraEngine.render(ctx, voice, "Seven", 180))
                    check(replacement.contentEquals(reference)) { "$gen replacement audio changed after playback stop" }
                }
            }
            result.putString("stream", "PASS completed renderer reuse and replacement PCM for every installed generation")
            test.finish(Activity.RESULT_OK, result)
        } catch (e: Throwable) {
            result.putString("stream", "FAIL renderer reuse: ${Log.getStackTraceString(e)}")
            test.finish(Activity.RESULT_CANCELED, result)
        }
    }
}
