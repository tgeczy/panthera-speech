# Panthera SAPI development driver

This is a SAPI 5 engine shim built for x86 and x64. Both variants launch the
existing 32-bit `panthera_host.exe`, because Apple's engine itself is i386.

**The host stays resident.** It used to be started and killed once per
utterance; keeping it takes the time from asking for speech to hearing it
from 19 ms to 11 on Tiger, 29 to 11 on Leopard, 26 to 10 on Snow Leopard,
and 47 to 21 for Lion's Alex. An interrupted utterance still kills the
host -- measured, that is cheaper here than the engine's own graceful
cancel, which costs a flat ~47 ms to stop and settle where a whole cold
start is 21-52 ms -- so interruptions cost exactly what they always did
and everything else got two to three times faster. The replacement starts
at the interruption rather than at the next request, so whatever gap the
listener leaves is spent booting.

**Nothing is logged unless you ask for it.** An earlier build wrote a line
per utterance to `%TEMP%`, forever, with the first forty characters of the
text in it -- which for a screen reader is a transcript of whatever its
owner reads, in a folder anything running as them can open. It is now off,
and with it off no file is created at all: the engine writes nothing and
its child's diagnostics go to `NUL`. A build that finds logs left by an
earlier one deletes them. To turn it on for a bug report:

```
reg add "HKCU\Software\Panthera SAPI" /v Diagnostics /t REG_DWORD /d 1 /f
```

`1` records the measurements -- byte counts, flags, which voice -- and that
is what has actually settled every bug this log has settled. `2` also
records a slice of the spoken text, and is worth using only when the report
is about particular words. Either way the file stops at 4 MB and starts
over. `/d 0` turns it off again and the next run clears up.

Three things follow from the engine outliving the utterance, and
`sapi/resident_test.cpp` gates all of them on every generation: a warm
utterance must be byte-identical to a cold one, a settings change must
respawn the host rather than be quietly ignored by one that read its
environment at startup, and an embedded command must not be left in force.
That last is why returning **Inflection** to the middle restarts the engine
instead of sending `[[pmod 100]]` -- 100 is a value, not a default, and on
Lion's Alex, who ignores a raised inflection entirely, sending it is the
only thing that ever changes his voice.

Run `powershell -ExecutionPolicy Bypass -File .\sapi\build.ps1`. All build and
test output is staged under `C:\panthera\sapi`; no generated file is written
to this repository. Run `C:\panthera\sapi\settings.cmd` to see which speech
data is present and register or unregister the voices.

The MacinTalk data folder is resolved in this order: a folder you chose with
the "Data location" button (remembered per user), then a folder set for the
whole machine (`HKLM\Software\Panthera SAPI\DataPath`), then NVDA's own shared
folder at `%APPDATA%\nvda\macintalk` -- so an NVDA user registers SAPI voices
from the data they already extracted, with nothing copied and nothing
extracted twice -- then `%APPDATA%\macintalk-data`, where earlier versions put
it, and finally `%ProgramData%\macintalk-data`, where a fresh install puts it
now. Inside the root sit the generation folders (`tiger`, `leopard`,
`snowleopard`, `lion`; case does not matter on Windows), each laid out exactly
as the NVDA add-on lays them out. The tool never creates folders inside NVDA's
tree; extraction is the only thing that writes.

**The SAPI data moves to `%ProgramData%` and NVDA's does not**, which reads as
an inconsistency and is not one. A portable NVDA copy carries its own
configuration folder with it, so data kept inside that folder travels and data
outside it is silently lost -- and on the Windows sign-in screen NVDA reads a
copy of that folder and nothing else. SAPI has no portable copy to protect,
and every account on the machine should read one copy rather than each
extracting their own. So the NVDA driver only *adds* `%ProgramData%` to the
places it looks, while this tool offers, once, to move its own data there.

Exactly one arrangement is offered a move: the per-user default,
`%APPDATA%\macintalk-data`. A folder you chose by hand stays where you put it,
and NVDA's `macintalk` folder is moved by nothing, ever. Saying no is
remembered, and "Data location" has always moved the folder by hand.

The move also resets the folder's permissions to what `%ProgramData%` grants
everybody. A folder moved within one volume keeps the security descriptor it
had, which would otherwise leave it machine-wide in name and readable by one
account in fact -- and a machine with one account on it cannot tell the
difference.

Both registry views are written and read throughout, because `HKLM\Software`
is redirected under WOW64 while `HKCU\Software` is not: NVDA and the 32-bit
engine DLL see `Wow6432Node`, so a machine-wide value written once, from
64-bit code, would be perfectly present and entirely invisible.

The settings program carries the NVDA driver's engine settings: **Accept
embedded speech commands in text** (off by default -- the engine really
parses `[[...]]`, and a wiki page's `[[Main Page]]` is eaten rather than
mispronounced), **Pauses** (the phrase-break threshold, from fewest to the
engine's own default), **Expand abbreviations**, **Rate boost** (the top of
the range rises to about 1200 wpm; the bottom never moves), **Inflection**
(0-100, 50 is the voice exactly as Apple ships it) and **Long numbers**
(grouping separators restored into seven-plus digit runs, which the engine
otherwise spells out one digit at a time). All apply to every SAPI
application at once, from the next utterance spoken. SAPI's own per-voice
pitch XML is honoured too. Not ported, with reasons: sentence joining and
the announcement gap are driver-architecture (SAPI applications control
their own chunking), and the stress respelling assumes NVDA's symbol
dictionary has already turned ":" into the word "colon", which SAPI input
never has.

This is development work: do not distribute it with extracted Apple data.
