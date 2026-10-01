# Panthera 3.3.1

**An Android release.** Capital letters can be heard on Android, and engine
data you no longer want can be removed from the phone. The NVDA add-on, the
SAPI voices and the Linux builds are unchanged.

## Capital letters are heard again (#23)

Thanks to Christopher for reporting this.

Snow Leopard Alex on Android gave no sign of a capital letter. The only way to
hear one was to set TalkBack to say the word "capital" aloud.

The engines were never the problem. Pitch has worked on Windows since the
beginning, and asked through the host directly, Snow Leopard Alex answers every
offset across an octave either way with audibly different speech. What was
missing sat in Panthera's own Android service: it never read the pitch the
calling app asked for. Every request was spoken at the voice's own pitch,
however loudly the caller asked otherwise.

So a screen reader indicating a capital by raising the pitch was asking into
the void — and so was **the system pitch slider**, in Android's own
Text-to-speech settings. Both work now. If you had given up on that slider,
it is worth another try.

The offset uses the same scale the NVDA add-on uses: an octave either way from
whatever the voice's own pitch is, so one setting means one thing whichever
voice is speaking, and a capital is raised by the same amount on both
platforms. Nothing changes for an app that never asks for a pitch, which is
most of them.

Measured on the Nothing Phone through Android's own speech client, not through
a shortcut into the engine — the gap was in the layer a shortcut would have
skipped. The same words render differently at normal, raised and lowered
pitch, and coming back to normal reproduces the original audio exactly. That
last check is the capital-letter case itself: one word raised, the next back
down, with nothing left behind to drift the pitch of everything after it.

## Removing engine data from the phone

Engine settings could already switch a generation off, which hides its voices
and keeps its files. This is the other half, for when you have decided you do
not want a generation on the phone at all. Alex alone is around 670 MB, and
until now the only way to get that space back was a file manager pointed into
an app's private storage, which on a modern Android is no way at all.

On the Setup page, **Remove engine data** lists what is installed with the
space each one takes and how many voices it has, and you can check off any
number of them at once. The confirmation names them, says what it frees, says
what will still be installed afterwards, and says plainly that your own copy
of the speech data is untouched — so you can import it again whenever you
like. If you are removing the last generation it tells you that too, because
it leaves the engine with no voices.

Every copy goes, including one sitting in the folder a PC's file window shows.
That matters: anything left there is copied back into the app the next time it
runs, so a removal that missed it would quietly undo itself.

Tested on the Nothing Phone by removing Tiger while its engine was live and
speaking, then restarting the app twice and walking back through Setup, where
a missed copy would have reappeared. It stayed gone, the remaining generations
were untouched, and Tiger imported cleanly again afterwards.

## Everything else

No change to the NVDA add-on, the SAPI voices or the Linux builds. The NVDA
add-on, the SAPI installer and the Linux tarballs attached below are the 3.3.0
files, unchanged and kept here so that everything for this version can be found
in one place; your add-on and SAPI installs will not offer you an update, and do
not need one.

No Apple engines or voice data are included. Keep using your extracted data.

## Updating

On Android, **Check for updates** on the app's Setup page, or install the APK
below. Nothing to do on NVDA or SAPI.
