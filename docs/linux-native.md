# Native Linux host for existing Speech Dispatcher integrations

Panthera builds an ELF host that runs Apple's i386 engine directly on x86
Linux. It accepts the same TGR3/TGR4 pipe requests as the Windows host.
No Wine and no new Speech Dispatcher module are needed. Supply your own
extracted engine and voice data; none is included in the build.

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

## Devin's retro-tts-pack

Its existing Panthera client already accepts ELF hosts. Override the Wine
backend and host path in the environment of the retro-tts server/module:

```sh
export RETRO_TTS_TIGER_BACKEND=native
export RETRO_TTS_TIGER_HOST=/absolute/path/to/panthera-speech/build/linux-i686/tiger_host
export RETRO_TTS_TIGER_TREE=/absolute/path/to/speech-tiger/x86
export RETRO_TTS_LEOPARD_BACKEND=native
export RETRO_TTS_LEOPARD_HOST="$RETRO_TTS_TIGER_HOST"
export RETRO_TTS_LEOPARD_TREE=/absolute/path/to/speech-leopard
```

Each tree must contain `Speech/Voices` and `SpeechDictionary.framework`.
The same host supports Lion with `RETRO_TTS_LION_*`; validate that generation
with its own engine data before using it. Do not leave the installer's
`BACKEND=wine` setting active while selecting an ELF executable.

The client opts into a four-byte little-endian `TRDY` startup handshake with
`TIGER_READY_HANDSHAKE=1`. The host sends it after setup and before reading
requests. Without that variable, the existing pipe protocol is unchanged.
On POSIX, `SIGUSR1` requests cancellation; the host drains/terminates the
current streamed response so the next request stays aligned. The signal
handler only sets a flag; the synthesis thread stops the engine.

The inspected retro-tts client restarts and preloads its native host after
*every* streamed utterance as an old AAC workaround. This is compatible but
costs startup time. Removing that workaround is a separate downstream change
after persistent AAC and cancellation testing against the released host.
The client already sends voice-normalized volume commands; no volume
protocol extension is required.

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
