# Linux command-line host and synthesis library

Panthera builds an ELF host that runs Apple's i386 engine directly on x86
Linux. It accepts the same TGR3/TGR4 pipe requests as the Windows host.
The host can render files or serve a client over pipes. It does not install a
Speech Dispatcher module or a settings application. Supply your own extracted
engine and voice data; none is included in the build.

Other projects may provide Speech Dispatcher integrations; they can use
Panthera's host protocol.

## 64-bit ARM

`./build_linux.sh aarch64` builds the host for AArch64 Linux on the pinned
Box64 translator the Android phones run, with Panthera's own 32-bit bridge and
Glint for AAC, all MIT. It needs `g++`, `cmake`, `python3` and `git`; the
translator and decoder are exported from clones at their pinned commits, fetched
into `build/dependencies` when absent. The output lands in
`build/linux-aarch64-glint` with the same files as the i686 build.

Scope, stated plainly: CI builds this on ARM hardware and runs the checks that
push i386 code through the translator, so the bridge is exercised, but nobody
has yet listened to it on an ARM Linux machine. The same translator and the
same host were validated by ear on ARM64 phones. `PANTHERA_RUNTIME=unicorn`
selects the older GPL Unicorn build instead.

## Build and check AAC

CI binaries target Ubuntu 22.04 / glibc 2.35 and newer, with the matching 32-bit glibc
runtime for i686. Older distributions should build from source locally.

On Ubuntu/Debian x86-64:

```sh
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install g++-multilib python3 git libsqlite3-0:i386
./build_linux.sh i686
./build/linux-i686-glint/tiger_host --capabilities
./build/linux-i686-glint/tiger_host --aac-check
```

`--capabilities` prints JSON to stdout, including `guest_execution`,
`aac_backend`, `aac_available`, `ready_handshake`, and `cancel_signal`.
Diagnostics go to stderr. `--aac-check` exits zero when the decoder opens
and initializes, or 2 when unavailable. This checks codec initialization;
a render of Vicki or Alex additionally tests the user's actual AAC bank.
Fred and other formant voices do not require AAC.

The default build fetches a pinned Glint source revision and builds the decoder
into the host. It needs no system AAC codec package. Set `GLINT_SOURCE` to an
existing official clone for offline builds. SQLite is loaded separately at
runtime for Leopard phrasing dictionaries (`libsqlite3-0:i386` on Ubuntu).

`AAC=faad2 ./build_linux.sh i686` retains the GPL comparison configuration in
`build/linux-i686`. That configuration needs the matching FAAD2 development
and runtime libraries; it can also build without AAC if none is found. Use
`ldd` on the executable to diagnose missing runtime dependencies.

## Render a WAV from the command line

`--render` accepts a tree containing `Speech/` and
`SpeechDictionary.framework/`. The host reads the voice identifier from the
bundle. Text arguments, input files, and stdin use UTF-8:

```sh
./build/linux-i686-glint/tiger_host --render \
  --tree /path/to/speech-tiger/x86 --voice Fred \
  --text 'Hello there.' --rate 180 --volume 80 --output speech.wav

printf '%s\n' 'There are 1234567 people.' | \
  ./build/linux-i686-glint/tiger_host --render \
  --tree /path/to/speech-tiger/x86 --numbers words --output - > speech.wav
```

`--input FILE` reads a text file; omitting both `--input` and `--text` reads
stdin. `--output -` writes a WAV to stdout, with diagnostics on stderr.
`--help` lists rate, pitch, volume, and number-style arguments. The host itself
is a native executable and requires no Python interpreter.

The output is mono PCM16 WAV at 22050 Hz. This file-rendering mode completes
the utterance before emitting the WAV; persistent IPC below streams the first
audio while synthesis continues. Each request stays whole, preserving the
engine's sentence timing and Alex's breaths. The host has a four-minute PCM
buffer per request; the CLI reports an error if that limit is reached. Longer
documents should be submitted at paragraph boundaries. Text is converted to
MacRoman; unsupported characters become spaces with a stderr diagnostic.
Text may include embedded engine speech commands.

The legacy positional paths and `TIGER_TEXT` interface remain available.
Playback belongs to the audio application or integration of your choice.

## amd64 build

The i686 executable is recommended on x86-64 distributions with 32-bit runtime
support. It executes the i386 guest directly. The amd64 (`x86_64`) host uses
Unicorn and needs substantially more time, particularly when cancelling a long
request. On the test VM, a cancelled Tiger paragraph took about 3.5 seconds
under emulation; native i686 took about 51 ms. Leopard Vicki took about 536 ms
and 108 ms respectively. These are synthesis/protocol measurements, not audio
device latency guarantees.

To build amd64, install a native C++ toolchain and build Unicorn
2.1.4 with its x86 target and position-independent code:

```sh
cmake -S /path/to/unicorn -B /path/to/unicorn/build \
  -DCMAKE_BUILD_TYPE=Release -DUNICORN_ARCH=x86 \
  -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON
cmake --build /path/to/unicorn/build -j
UNICORN_DIR=/path/to/unicorn ./build_linux.sh x86_64
```

`UNICORN_LIBRARY` can override the static library path. Dependencies must match
the chosen architecture; this also applies to a locally built static FAAD2,
which needs PIC for the shared library. Both Linux targets have been checked
with Tiger Fred and Leopard Vicki: CLI file/pipes, native number handling,
volume, repeated synthesis and cancellation recovery. AAC output can differ
slightly between decoder/architecture builds. Snow Leopard and Lion Linux
rendering are not part of this validation claim.

The amd64 build remains experimental: one Leopard CLI startup failed during
validation, followed by 45 successful repetitions and a successful complete
CLI check. Its cause is not established. Prefer i686 for a screen reader that
needs the most thoroughly exercised path.

## Link to libpanthera

The same build produces `libpanthera.so.0`, its `libpanthera.so` link, and
`include/panthera.h`. CI archives contain these alongside the executable,
this document, and an example C client. No engine or voice data is packaged.
The normal native i686 build uses Panthera and Glint under MIT, with compiler
runtime notices alongside them. Builds using FAAD2 or Unicorn retain GPL
requirements; this includes the amd64 Linux configuration, and an AArch64 one
built with `PANTHERA_RUNTIME=unicorn` instead of the normal Box64 build.
Archives include the applicable notices and source. See
[distribution licensing](../licenses/DISTRIBUTION.txt).

```sh
out="$PWD/build/linux-i686-glint"
cc -m32 -I"$out/include" tools/native_library_check.c \
  -L"$out" -Wl,-rpath,"$out" -lpanthera -o library_check
./library_check --check                         # no engine data needed
./library_check /path/to/speech-leopard Vicki    # render/stream/cancel checks
```

For amd64, use `build/linux-x86_64` and omit `-m32`. An installed application
should arrange its library search path for the intended installation directory.
The public header has C linkage and can be included from C++.

Set phrase breaks before `panthera_init`, read the bundle identity with
`panthera_voice_spec`, then configure volume, numbers and abbreviations between
utterances. `panthera_speak_start` plus `panthera_pull` provides streaming mono
PCM16 at `panthera_sample_rate()`. `panthera_render` returns a complete allocated
buffer that the caller frees with `free`. Library text uses MacRoman; the CLI
handles UTF-8 conversion for clients that prefer a subprocess.

One engine generation and one synthesis owner are supported per process.
Serialize settings and synthesis calls. `panthera_stop` alone can be called
from another thread; follow it with `panthera_finish` on the synthesis thread
before starting again. Cancellation drains outstanding callbacks; if that
does not settle within ten seconds, further synthesis is rejected until a
successful finish. Clients needing a firm cancellation deadline should use the
subprocess protocol, where they can replace an unresponsive child.

Keep the library loaded for the process lifetime: guest workers retain code
references, and there is no unload/shutdown API. Use separate processes for
different generations or to change phrase breaks after initialization. The
library owns synthesis; clients own playback and their settings interface.

## Client protocol

Start `tiger_host --serve ENGINE DICTIONARY VOICES`, passing paths to the
extracted MacinTalk executable, SpeechDictionary framework executable, and
voice directory. Clients use the existing TGR3/TGR4 protocol documented in
`src/tiger_host_serve.c`; `tools/native_stream_check.py` provides a runnable
streaming example with user-supplied data. An integration owns its settings
and chooses the host executable; installing this host does not register or
replace a system speech module.

Requests contain little-endian `u32 magic, i32 rate, i32 pitch, u32 flags,
u32 voiceBytes, u32 textBytes`, then the UTF-8 bundle name and MacRoman text.
Magic `0x54475234` selects streaming. The response is `u32 0x54475253,
i32 status`, followed by repeated `u32 frames, i16 PCM[frames]` chunks and a
zero-frame terminator. PCM is mono at 22050 Hz. Flag `2` selects fixed numbers,
`4` selects numbers as words, and `0` leaves client-prepared text unchanged.
Send one request at a time and drain its response, including the terminator,
before sending another. `--capabilities` advertises the available number flags.

The client opts into a four-byte little-endian `TRDY` startup handshake with
`TIGER_READY_HANDSHAKE=1`. The host sends it after setup and before reading
requests. Without that variable, the existing pipe protocol is unchanged.
On POSIX, `SIGUSR1` requests cancellation; the host drains/terminates the
current streamed response so the next request stays aligned. The signal
handler only sets a flag; the synthesis thread stops the engine.

Native i686 streaming has been checked with Tiger and Leopard, including
volume commands, repeated utterances, cancellation and recovery. The x86_64
Unicorn build also passes the Fred streaming volume and recovery checks after
correcting the guest character-classification table binding. Native i686 is
the preferred x86 Linux build for latency: cancelling a long Tiger request
under emulation can still wait for the remainder of that request to render.
Cancellation uses a separate audio-discard flag from ordinary sentence resets;
status queries also marshal their output into guest-visible memory.

`tools/native_cli_check.py HOST TREE [VOICE]` checks file/stdin/stdout input,
UTF-8 conversion against IPC, number styles, rate, and mute using personal data.
`tools/native_cli_args_check.py HOST` checks argument handling without engine
data and runs in CI for both Linux builds.

The native i686 host also carries the same high-rate guest divide-by-zero
recovery as Windows. Tiger Fred at 380 and 387 wpm has been checked against
Windows byte for byte, through both codec builds; the shared-library client
also checks streaming and cancellation at 387 wpm. Recovery is restricted to
zero memory divisors in loaded guest images. Other arithmetic faults retain
the previous signal action or the normal process fault.

`tools/native_library_check.c` accepts `TREE VOICE [WPM]` to exercise the public
library at a chosen rate. Building Linux runs the synthetic instruction/signal
tests in `tools/native_divide_check.c`; native i686 additionally runs
`tiger_host --audio-timeline-check`. These tests need no engine data.

The Glint decoder remains opt-in:

```sh
AAC=glint GLINT_SOURCE=/path/to/glint-clone sh build_linux.sh i686
```

This requires a C++17 multilib toolchain and the pinned official source clone
described in [the AAC experiment](../tools/aac_experiment/README.md). It builds
into `build/linux-i686-glint-glint`, separately from the default codec, and has its
own Linux CI job. Dependency promotion and release packaging remain separate
from this experimental build.

## Android volume

The in-process API exposes `panthera_set_volume(level, generation)` under
synthesis ownership. Both preview and streamed speech apply it every
utterance using the Speech Manager volume parameter and NVDA's measured
generation-specific tables. Tiger defaults to 100; later generations default to 90. Zero mutes.
The 90–100 range on normalized generations can introduce distortion.
`tools/volume_oracle.py` checks the shared C mapping against the NVDA tables.
Android rate and volume controls provide spoken values, one-step arrow keys,
Home/End, and the native SeekBar accessibility adjustment actions, following
TG Speechbox's AccessibleSlider behavior.
