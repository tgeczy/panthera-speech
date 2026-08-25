# Panthera SAPI development driver

This is a SAPI 5 engine shim built for x86 and x64. Both variants launch the
existing 32-bit `panthera_host.exe`, because Apple's engine itself is i386.

Run `powershell -ExecutionPolicy Bypass -File .\sapi\build.ps1`. All build and
test output is staged under `C:\panthera\sapi`; no generated file is written
to this repository. Run `C:\panthera\sapi\settings.cmd` to see which speech
data is present and register or unregister the voices.

The MacinTalk data folder is resolved in this order: a folder you chose with
the "Data location" button (remembered per user), then NVDA's own shared
folder at `%APPDATA%\nvda\macintalk` -- so an NVDA user registers SAPI voices
from the data they already extracted, with nothing copied and nothing
extracted twice -- and finally `%APPDATA%\macintalk-data` for a machine with
no NVDA at all. Inside the root sit the generation folders (`tiger`,
`leopard`, `snowleopard`, `lion`; case does not matter on Windows), each laid
out exactly as the NVDA add-on lays them out. The tool never creates folders
inside NVDA's tree; extraction is the only thing that writes.

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
