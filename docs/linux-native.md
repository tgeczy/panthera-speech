# Native Linux command-line host

Panthera builds an ELF host that runs Apple's i386 engine directly on x86
Linux. It accepts the same TGR3/TGR4 pipe requests as the Windows host.
The host can render files or serve a client over pipes. It does not install a
Speech Dispatcher module or a settings application. Supply your own extracted
engine and voice data; none is included in the build.

Other projects may provide Speech Dispatcher integrations; they can use
Panthera's host protocol.

## Build and check AAC

On Ubuntu/Debian x86-64:

```sh
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install gcc-multilib libfaad-dev:i386 libsqlite3-0:i386
./build_linux.sh i686
./build/linux-i686/tiger_host --capabilities
./build/linux-i686/tiger_host --aac-check
```

`--capabilities` prints JSON to stdout, including `guest_execution`,
`aac_backend`, `aac_available`, `ready_handshake`, and `cancel_signal`.
Diagnostics go to stderr. `--aac-check` exits zero when the decoder opens
and initializes, or 2 when unavailable. This checks codec initialization;
a render of Vicki or Alex additionally tests the user's actual AAC bank.
Fred and other formant voices do not require AAC.

The Linux build links FAAD2 when present at build time. For a dynamically
linked build, the runtime machine needs the matching 32-bit FAAD2 library
(Ubuntu 22.04: `libfaad2:i386`). If the executable cannot even start, use
`ldd build/linux-i686/tiger_host` to find missing runtime libraries. A codec
installed only for x86-64 cannot satisfy a 32-bit host. A build made without
AAC support must be rebuilt after installing the development package.
SQLite is loaded separately at runtime for Leopard phrasing dictionaries.

## Render a WAV from the command line

`--render` accepts a tree containing `Speech/` and
`SpeechDictionary.framework/`. The host reads the voice identifier from the
bundle. Text arguments, input files, and stdin use UTF-8:

```sh
./build/linux-i686/tiger_host --render \
  --tree /path/to/speech-tiger/x86 --voice Fred \
  --text 'Hello there.' --rate 180 --volume 80 --output speech.wav

printf '%s\n' 'There are 1234567 people.' | \
  ./build/linux-i686/tiger_host --render \
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
