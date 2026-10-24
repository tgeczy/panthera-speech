# Panthera 3.3.3

**A battery release, everywhere.** Panthera used to keep working while it had
nothing to say: on a phone it woke the processor hundreds of times a second, and
on a Windows laptop it held a fast system timer for as long as NVDA was running.
Neither happens any more. The speech itself has not changed, and every file
attached here carries the same number.

## Android: no more work while nothing is being said (#25)

Credit to Christopher, who found the battery going quickly with Snow Leopard Alex
and asked whether that was a bug or just the price of emulation. It was some of
both, and the bug part was ours.

The engine runs in its own background process, so a screen reader's next word
does not have to wait for it to load. Inside that process, the thread that keeps
the audio on time checked for new work every 2 milliseconds, forever, whether
anything was being spoken or not. On a phone with BackTalk and Panthera, an idle
worker woke **667 times a second** and used about 1% of a processor core while
the phone sat silent, which also kept the processor out of its low-power states.

Now, once speech has stopped for half a second, that thread waits to be told
there is something to say. The same idle worker wakes about 12 times a second and
uses next to nothing. While speech is playing nothing changes: the audio is the
same, and the first word after a pause comes just as quickly, because the worker
still stays loaded. It now costs almost nothing to keep it that way.

What this does not change is the cost of speaking itself. While Alex is talking,
the original Mac code is being translated for your phone's processor as it runs,
and that takes more power than a voice written for Android.

## Windows: the fast timer is let go between utterances

The same check ran on Windows too, more gently -- about 63 times a second on
Windows 11, and now 9. The larger cost on a laptop was a different one.

The engine's own waits need a 1 millisecond system timer to run at full speed, so
Panthera asks Windows for one. It used to keep that request for as long as the
host was running, which under NVDA is the whole session. On **Windows 7, 8 and
Windows 10 before version 2004**, that request changes the timer for the entire
machine: every program on the laptop is woken a thousand times a second for as
long as Panthera is your synthesizer, which is a well-known way to lose battery.
Windows 11 confines the effect to the program that asked, but its own energy
report (`powercfg /energy`) still listed an idle Panthera as holding a 1 ms timer.

Now Panthera holds the timer only while it is speaking. It lets go half a second
after an utterance ends and takes it back the moment the next one arrives, before
the engine sees any text, so speech is paced exactly as before. The energy report
no longer lists it. This applies to the NVDA add-on and the SAPI voices alike.

Measured on Windows 11: the audio is byte-identical for Tiger Fred and for the
Leopard, Snow Leopard and Lion Alex, including speech after a pause long enough
for the timer to have been let go, and the time to first sound is unchanged. The
older versions of Windows, where this matters most, were not available to test.

## Sending engines to a watch or phone from a PC

Not part of the app, but new since the last release: `tools/push_engines.cmd`
in the repository is a small Windows window that copies your extracted engine
folders to a phone or watch over adb, into the folder the app collects new data
from. It can download Google's own adb if you have none, or use one you already
have wherever you keep it. Download it from
[the repository](https://github.com/tgeczy/panthera-speech/blob/main/tools/push_engines.cmd)
with **Download raw file**, and double-click it.

## Everything else

The Linux host gets the first change: the idle check is gone there too. The
second never applied, because the timer request is a Windows thing. The tarballs
attached are 3.3.3 builds.

No Apple engines or voice data are included. Keep using your extracted data.

## Updating

Press **Check for updates** in the speech data manager (NVDA's Tools menu) or in
the SAPI settings window, and this release installs itself; on Android, Check for
updates on the app's Setup page. The NVDA add-on, the SAPI installer, the APK and
the Linux tarballs are all attached below.
