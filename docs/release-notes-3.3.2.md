# Panthera 3.3.2

**An Android release.** Alex can be asked to go much faster. Nothing else
changes, and the NVDA add-on and the SAPI voices are the 3.3.1 files.

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

The voices hold up. Measured on the same host the app runs, the same sentence
takes 4.34 seconds at the default 180 words per minute, 1.58 at 500, and 0.67 at
1200 — six and a half times faster than normal, two and a half times faster than
the old top, with Snow Leopard Alex steady the whole way.

It is a switch rather than a wider slider on purpose: widening the slider would
have made everyone's existing setting faster the moment they updated. **Nothing
changes until you turn it on.** The slowest speeds are untouched; the switch
raises only the top.

Boost is remembered per generation, like the rate it raises, and a rate above the
current top is kept rather than rewritten — turn the switch off and back on and
the number you chose is still there.

## Everything else

The NVDA add-on and the SAPI installer attached below are the **3.3.1** files,
unchanged. Your add-on and SAPI installs will not offer you an update, and do not
need one — which is itself the first outing for the updater fix that shipped in
3.3.1: before it, a release tagged newer than the add-on it carried would have
offered you that add-on over and over.

Linux support is unchanged, and the tarballs here are the 3.3.0 builds.

No Apple engines or voice data are included. Keep using your extracted data.

## Updating

On Android, **Check for updates** on the app's Setup page, or install the APK
below. Nothing to do on NVDA or SAPI.
