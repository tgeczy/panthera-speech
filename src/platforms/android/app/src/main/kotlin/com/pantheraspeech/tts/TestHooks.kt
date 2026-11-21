// What the settings screen -- the app's launcher, so exported -- takes from the
// intent that started it.  No Android in it, so the JVM tests run it.
//
// Any installed app can start an exported screen with any extras: `import`
// imported a zip it named, replacing an engine, without asking, and the test
// hook `autospeak` did what it does for anyone.  And a rebuild of the screen
// (turned, dark mode, a keyboard) starts it again with the same intent, so an
// import that had just finished ran again (panthera-speech#40).  The rules are
// ssi263-speech's for the same class (ab34ed7), which Tomi chose: a test hook
// is a debug build's, on a fresh start only; the adb route runs on a fresh
// start only, and in a release build -- where another app may have sent it --
// with its confirm dialog first.
package com.pantheraspeech.tts

object TestHooks {
    /** A test hook (`autospeak`): a debug build's -- the device tests' -- and
     * on the screen's fresh start only, never its rebuild. */
    fun on(debuggable: Boolean, fresh: Boolean): Boolean = debuggable && fresh

    /** The adb route (`--es import <zip>`, docs/android-tts-debugging.md): on
     * a fresh start only. */
    fun route(fresh: Boolean): Boolean = fresh

    /** ... and with its confirm dialog first in a release build; in a debug
     * build the command typed at adb is the consent. */
    fun confirm(debuggable: Boolean): Boolean = !debuggable

    /** A fresh start: not a rebuild (no saved state), and not the screen
     * reopened from Recents, which hands it its old intent again. */
    fun fresh(rebuilt: Boolean, fromHistory: Boolean): Boolean = !rebuilt && !fromHistory
}
