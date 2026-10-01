# Panthera 3.3.1

Pitch changes are heard on Android at last, engine data you no longer want can
be removed from the phone, and **every updater now knows which version it is
actually looking at** -- which is why this release carries a new add-on and a
new SAPI installer even though the speech they produce has not changed.

## Pitch changes are heard again (#23)

Thanks to Christopher for reporting this, which arrived as a question about
capital letters and turned out to be bigger than that.

**Panthera's Android service never read the pitch a calling app asked for.**
Every request was spoken at the voice's own pitch, however loudly the caller
asked otherwise. The engines were never the problem: pitch has worked on Windows
since the beginning, and asked through the host directly, Snow Leopard Alex
answers every offset across an octave either way with audibly different speech.

So nothing that asked for a pitch got it. That includes **the system pitch
slider**, in Android's own Text-to-speech settings -- if you had given up on
that slider, it is worth another try -- and it includes every pitch change a
screen reader makes to tell you something.

The easiest one to hear is **deleting text**: TalkBack speaks the character it
removed at a raised pitch, and on Panthera that character used to come back at
the same pitch as everything else. Tomi confirmed it by ear this way. For
capital letters specifically, how TalkBack marks them is TalkBack's own setting
rather than Panthera's -- saying the word "capital" is what it does by default,
and it does not announce every capital as you type the way VoiceOver does. If you
would rather hear them by pitch, TalkBack can do that: in TalkBack settings,
under **Verbosity** or **Keyboard feedback** depending on your version, set the
capital letters option to change pitch. It reaches the engine by the same route a
deleted character does, so if one is audible the other is too.

The offset uses the same scale the NVDA add-on uses: an octave either way from
whatever the voice's own pitch is, so one setting means one thing whichever voice
is speaking, and the same request is raised by the same amount on both platforms.
Nothing changes for an app that never asks for a pitch, which is most of them.

Measured on the Nothing Phone through Android's own speech client, not through a
shortcut into the engine -- the gap was in the layer a shortcut would have
skipped. The same words render differently at normal, raised and lowered pitch,
and coming back to normal reproduces the original audio exactly. That last check
is the one that matters for a raised character: one word up, the next back down,
with nothing left behind to drift the pitch of everything after it.

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

## Every updater reads the version off the file, not off the release

**Check for updates could tell you a version was available and then hand you the
one you already had** -- again and again, every time you pressed it.

A release carries four things that do not move in step: the add-on, the SAPI
installer, the Android APK and the Linux tarballs. Only one release can be
GitHub's "latest". All three updaters read the version from the release's tag,
which names the release and not the file inside it, so a release tagged newer
than its add-on claimed an add-on update that did not exist -- and kept claiming
it, because what you had installed never caught up with a tag. This project
already had that shape: the 3.0.2 release carries the 3.0.0 add-on and installer,
and the 3.0.0 release carries the 3.0.2 APK.

The way round it was to leave an Android-only release unmarked, so the desktop
updaters never saw it. That worked and it cost something real: the newest release
was then findable only by knowing it was there, and anyone who went to "latest"
landed on an older set of files. For somebody moving through a releases page with
a screen reader, hunting for which of several entries holds the current download
is work nobody should have to do.

So each thing is now versioned by **its own filename**.
`pantheraspeech-3.3.1.nvda-addon` says 3.3.1 wherever it sits, and the updaters
walk the recent releases for the newest file of their own kind. A release can
therefore keep older files alongside new ones -- so that everything for a version
is in one place -- and still never offer you something you have. From now on the
newest release is simply the latest one, with every file in it.

Nothing about this changes what the voices sound like.

## Everything else

Linux support is unchanged, and the tarballs attached here are the 3.3.0 builds.

No Apple engines or voice data are included. Keep using your extracted data.

## Updating

Press **Check for updates** in the speech data manager (NVDA's Tools menu) or in
the SAPI settings window, and this release installs itself; on Android, Check for
updates on the app's Setup page. The NVDA add-on, the SAPI installer, the APK and
the Linux tarballs are all attached below.
