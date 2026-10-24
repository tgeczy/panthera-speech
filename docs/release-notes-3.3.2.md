# Panthera 3.3.2

**An Android release.** Alex can be asked to go much faster, and Panthera no
longer drains the battery while it sits silent. The NVDA add-on and the SAPI
voices are the 3.3.1 files.

## Rate boost on Android: up to 1200 words per minute

Credit to Jade, who said Alex still sounded slow with the setting as fast as it
would go — at 500 words per minute, which is exactly where the slider stopped.

**500 was never the engine's limit. It was ours.** The NVDA add-on has had a rate
boost switch and a 1200 ceiling for a while, and the SAPI voices reach 1200 too.
Android was the one place that could not ask for it, so somebody already at the
top of the slider asking to go faster got precisely what they already had, with
nothing to say why.

Setup now has a **Rate boost** checkbox under Rate. With it on, the slider goes to
1200 words per minute instead of 500, and the ceiling lifts on the system rate
this engine follows as well — which matters, because that is where a screen
reader's own speed lives. If you have TalkBack turned up and Panthera set to
follow it, you were being quietly held at 500 without ever visiting these
settings.

The voices hold up. On the desktop, through the same host the app runs, the same
sentence takes 4.34 seconds at the default 180 words per minute, 1.58 at 500 and
0.67 at 1200 — six and a half times faster than normal, with Snow Leopard Alex
steady the whole way.

On a Galaxy S22 running Android 16, asking for 1200 instead of 500 gives:

| Generation | How much faster |
| --- | --- |
| Tiger | 1.69x |
| Leopard | 1.93x |
| Snow Leopard, Alex | 2.08x |
| Lion, Alex | 2.09x |

How much faster a voice actually gets is the engine's own business, which is why
the table is not one number. Alex gains the most, which is the voice this came up
about.

It is a switch rather than a wider slider on purpose: widening the slider would
have made everyone's existing setting faster the moment they updated. **Nothing
changes until you turn it on.** The slowest speeds are untouched; the switch
raises only the top.

Boost is remembered per generation, like the rate it raises, and a rate above the
current top is kept rather than rewritten — turn the switch off and back on and
the number you chose is still there.

## Battery: no more work while nothing is being said

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

## Everything else

The NVDA add-on and the SAPI installer attached below are the **3.3.1** files,
unchanged. Your add-on and SAPI installs will not offer you an update, and do not
need one — which is itself the first outing for the updater fix that shipped in
3.3.1: before it, a release tagged newer than the add-on it carried would have
offered you that add-on over and over.

The desktop host had the same idle check, at a smaller cost: on Windows it woke
about 63 times a second. The fix is in the shared code and comes to NVDA, SAPI
and Linux with their next release.

Linux support is unchanged, and the tarballs here are the 3.3.0 builds.

No Apple engines or voice data are included. Keep using your extracted data.

## Updating

On Android, **Check for updates** on the app's Setup page, or install the APK
below. Nothing to do on NVDA or SAPI.
