package com.pantheraspeech.tts

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.Looper
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Bound only by the app; workers never read or write SharedPreferences.
 * Keeping one connection per generation makes a return to an earlier voice warm.
 * Android may reclaim a process; discard dead bindings and reopen on next use. */
internal object PantheraWorkers {
    private class Connection : ServiceConnection {
        val ready = CountDownLatch(1)
        @Volatile var api: IPantheraWorker? = null
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            api = IPantheraWorker.Stub.asInterface(binder)
            ready.countDown()
        }
        override fun onServiceDisconnected(name: ComponentName) { api = null }
        override fun onBindingDied(name: ComponentName) { api = null; ready.countDown() }
        override fun onNullBinding(name: ComponentName) { ready.countDown() }
    }
    private val connections = mutableMapOf<String, Connection>()
    // Calls are serialized by PantheraEngine's synthesis lock, never on the UI thread.
    fun get(context: Context, generation: String): IPantheraWorker {
        check(Looper.myLooper() != Looper.getMainLooper())
        val ctx = context.applicationContext
        connections[generation]?.let { connection ->
            connection.api?.let { if (it.asBinder().isBinderAlive) return it }
            ctx.unbindService(connection)
            connections.remove(generation)
        }
        val service = when (generation) {
            "tiger" -> TigerWorkerService::class.java
            "leopard" -> LeopardWorkerService::class.java
            "snowleopard" -> SnowLeopardWorkerService::class.java
            "lion" -> LionWorkerService::class.java
            else -> error("Unknown generation: $generation")
        }
        val connection = Connection()
        check(ctx.bindService(Intent(ctx, service), connection, Context.BIND_AUTO_CREATE))
        connections[generation] = connection
        check(connection.ready.await(30, TimeUnit.SECONDS)) { "Engine connection timed out" }
        return connection.api ?: error("Engine connection failed")
    }
}
