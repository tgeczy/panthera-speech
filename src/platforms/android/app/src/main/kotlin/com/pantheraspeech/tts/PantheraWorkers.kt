package com.pantheraspeech.tts

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.Looper
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** One binding owner per generation. A retired connection stays in the map
 * until its Binder dies, so a replacement cannot bind the exiting process. */
internal object PantheraWorkers {
    private val lock = Any()
    private class Connection(val context: Context) : ServiceConnection {
        val ready = CountDownLatch(1)
        val closed = CountDownLatch(1)
        var api: IPantheraWorker? = null
        var bound = false
        var retired = false

        override fun onServiceConnected(name: ComponentName, binder: IBinder) = synchronized(lock) {
            if (retired) return@synchronized
            api = IPantheraWorker.Stub.asInterface(binder)
            try {
                binder.linkToDeath({ disconnected() }, 0)
            } catch (_: android.os.DeadObjectException) {
                disconnected()
            }
            ready.countDown()
        }
        private fun unbind() {
            if (bound) {
                context.unbindService(this)
                bound = false
            }
        }
        private fun disconnected() = synchronized(lock) {
            retired = true
            unbind()
            ready.countDown()
            closed.countDown()
        }
        override fun onServiceDisconnected(name: ComponentName) { disconnected() }
        override fun onBindingDied(name: ComponentName) = synchronized(lock) { retire() }
        override fun onNullBinding(name: ComponentName) { disconnected() }

        // Called under lock. Never await Binder death while holding it: the
        // death recipient needs this lock, and cancellation must return promptly.
        fun retire() {
            if (retired) return
            retired = true
            unbind() // Drop BIND_AUTO_CREATE BEFORE intentionally killing it.
            ready.countDown()
            val target = api
            if (target == null || !target.asBinder().isBinderAlive) {
                closed.countDown()
                return
            }
            android.util.Log.i("PantheraEngine", "Retiring unbound engine worker")
            try { target.shutdown() } catch (_: android.os.DeadObjectException) {
                closed.countDown()
            }
        }
    }
    private val connections = mutableMapOf<String, Connection>()

    /** Identity matters: a late cancellation must never retire a replacement. */
    fun retire(api: IPantheraWorker) = synchronized(lock) {
        connections.values.firstOrNull { it.api?.asBinder() === api.asBinder() }?.retire()
        Unit
    }

    fun restart(generation: String) {
        val connection = synchronized(lock) { connections[generation]?.also { it.retire() } } ?: return
        check(connection.closed.await(10, TimeUnit.SECONDS)) { "Engine worker did not stop" }
        synchronized(lock) { if (connections[generation] === connection) connections.remove(generation) }
    }

    // Synthesis callers are serialized. Lifecycle callbacks and stop are not.
    fun get(context: Context, generation: String): IPantheraWorker {
        check(Looper.myLooper() != Looper.getMainLooper())
        val ctx = context.applicationContext
        while (true) {
            val connection = synchronized(lock) {
                connections[generation] ?: Connection(ctx).also { connection ->
                    val service = when (generation) {
                        "tiger" -> TigerWorkerService::class.java
                        "leopard" -> LeopardWorkerService::class.java
                        "snowleopard" -> SnowLeopardWorkerService::class.java
                        "lion" -> LionWorkerService::class.java
                        else -> error("Unknown generation: $generation")
                    }
                    connection.bound = ctx.bindService(Intent(ctx, service), connection, Context.BIND_AUTO_CREATE)
                    check(connection.bound) { "Engine binding failed" }
                    connections[generation] = connection
                }
            }
            if (!connection.ready.await(30, TimeUnit.SECONDS)) {
                synchronized(lock) { connection.retire() }
                error("Engine connection timed out")
            }
            synchronized(lock) {
                if (!connection.retired) {
                    connection.api?.let { if (it.asBinder().isBinderAlive) return it }
                    connection.retire()
                }
            }
            check(connection.closed.await(10, TimeUnit.SECONDS)) { "Engine worker did not stop" }
            synchronized(lock) { if (connections[generation] === connection) connections.remove(generation) }
            // A failed initial binding is an error, not an unbounded respawn loop.
            check(connection.api != null) { "Engine connection failed" }
        }
    }
}
