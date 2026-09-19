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
    /** Cancellation-to-next-PCM, with exact replacement audio and worker
     * identity. Uses real Binder/native work, without the playback queue. */
    fun runHandoffProbe(test: Instrumentation) {
        val result = Bundle()
        val report = ArrayList<String>()
        try {
            val ctx = test.targetContext
            var count = 0
            for (gen in PantheraEngine.availableGens(ctx)) {
                val voices = PantheraEngine.scanVoices(ctx, gen)
                val voice = voices.firstOrNull { it.name == "Alex" } ?: voices.first { it.name == "Fred" }
                val reference = checkNotNull(PantheraEngine.render(ctx, voice, "Seven", 180))
                val texts = listOf("Search", "Messages", "Search apps and more.",
                    "Navigation item three, with more text to interrupt.",
                    "This is a long navigation item with several sentences. ".repeat(15))
                repeat(2) { run ->
                    for ((index, text) in texts.withIndex()) for (gap in listOf(10L, 25L, 50L)) {
                        // Every cancellation starts warm, including after a kill.
                        checkNotNull(PantheraEngine.render(ctx, voice, "Seven", 180))
                        var old: IPantheraWorker? = null
                        var at = 0L
                        var stopMs = 0L
                        var completeAtStop = false
                        fun elapsed() = (android.os.SystemClock.elapsedRealtimeNanos() - at) / 1_000_000
                        PantheraEngine.withSynthesis {
                            check(PantheraEngine.speakStart(ctx, voice, PantheraText.bytes(text), 387) == 0)
                            old = PantheraWorkers.get(ctx, gen)
                            Thread.sleep(gap)
                            completeAtStop = old!!.renderComplete()
                            at = android.os.SystemClock.elapsedRealtimeNanos()
                            PantheraEngine.stop()
                            stopMs = elapsed()
                        }
                        val released = elapsed()
                        val chunks = ArrayList<ShortArray>()
                        var first = -1L
                        var same = false
                        PantheraEngine.withSynthesis {
                            check(PantheraEngine.speakStart(ctx, voice, PantheraText.bytes("Seven"), 180) == 0)
                            same = PantheraWorkers.get(ctx, gen).asBinder() === old?.asBinder()
                            val buffer = ShortArray(4096)
                            while (true) {
                                val n = PantheraEngine.pull(buffer)
                                check(n >= 0) { "$gen replacement pull failed" }
                                if (n == 0) break
                                if (first < 0) first = elapsed()
                                chunks.add(buffer.copyOf(n))
                            }
                        }
                        val pcm = ShortArray(chunks.sumOf { it.size })
                        var offset = 0
                        for (chunk in chunks) { chunk.copyInto(pcm, offset); offset += chunk.size }
                        check(pcm.contentEquals(reference)) { "$gen replacement PCM changed after text=$index gap=$gap" }
                        val line = "handoff gen=$gen run=$run text=$index gap=$gap complete=$completeAtStop " +
                            "stop=$stopMs released=$released first=$first done=${elapsed()} reused=$same frames=${pcm.size}"
                        report.add(line); Log.i("PantheraLatency", line)
                        count++
                    }
                }
            }
            check(count > 0) { "No installed test voices" }
            result.putString("stream", "PASS handoff probe ($count replacements, exact PCM)\n" + report.joinToString("\n"))
            test.finish(Activity.RESULT_OK, result)
        } catch (e: Throwable) {
            result.putString("stream", "FAIL handoff probe: ${Log.getStackTraceString(e)}\n" + report.joinToString("\n"))
            test.finish(Activity.RESULT_CANCELED, result)
        }
    }

    /** Measure completion without cancelling first: how much work would a
     * stop at 10/25/50 ms throw away? No playback or preference changes. */
    fun runCompletionProbe(test: Instrumentation) {
        val result = Bundle()
        val report = ArrayList<String>()
        try {
            val ctx = test.targetContext
            val texts = listOf("7", "Search", "Messages", "Search apps and more.",
                "Navigation item three, with more text to interrupt.",
                "This is a long navigation item with several sentences. ".repeat(15))
            var count = 0
            for (gen in PantheraEngine.availableGens(ctx)) {
                for (voice in PantheraEngine.scanVoices(ctx, gen).filter { it.name in listOf("Fred", "Alex") }) {
                    repeat(3) { run ->
                        for ((index, text) in texts.withIndex()) {
                            // A preceding long render may have retired the worker.
                            // Exclude its cold load from the completion window.
                            checkNotNull(PantheraEngine.render(ctx, voice, "Seven", 387))
                            PantheraEngine.withSynthesis {
                                val at = android.os.SystemClock.elapsedRealtimeNanos()
                                fun elapsed() = (android.os.SystemClock.elapsedRealtimeNanos() - at) / 1_000_000
                                check(PantheraEngine.speakStart(ctx, voice, PantheraText.bytes(text), 387) == 0)
                                val started = elapsed()
                                val api = PantheraWorkers.get(ctx, gen)
                                var complete = api.renderComplete()
                                while (!complete && elapsed() - started < 110) {
                                    Thread.sleep(2)
                                    complete = api.renderComplete()
                                }
                                val observed = elapsed()
                                val line = "completion voice=${voice.id} run=$run text=$index chars=${text.length} " +
                                    "start=$started observed=$observed complete=$complete"
                                report.add(line); Log.i("PantheraLatency", line)
                                // Stop only AFTER observation. The existing policy either
                                // keeps the completed worker or retires unfinished work.
                                PantheraEngine.stop()
                            }
                            count++
                        }
                    }
                }
            }
            check(count > 0) { "No installed test voices" }
            result.putString("stream", "PASS completion probe ($count renders)\n" + report.joinToString("\n"))
            test.finish(Activity.RESULT_OK, result)
        } catch (e: Throwable) {
            result.putString("stream", "FAIL completion probe: ${Log.getStackTraceString(e)}\n" + report.joinToString("\n"))
            test.finish(Activity.RESULT_CANCELED, result)
        }
    }

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
            val notStarted = PantheraRequest<Int>({ retired.add(it) }, { _, _ -> true })
            check(notStarted.attach(5)); notStarted.cancel(); notStarted.started()
            check(retired == listOf(2, 5) && !notStarted.attach(5))
            val rendered = PantheraRequest<Int>({ retired.add(it) }, { _, _ -> true })
            check(rendered.attach(6)); rendered.started(); rendered.cancel()
            check(retired == listOf(2, 5) && rendered.current() == null)
            val rendering = PantheraRequest<Int>({ retired.add(it) }, { _, _ -> false })
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
