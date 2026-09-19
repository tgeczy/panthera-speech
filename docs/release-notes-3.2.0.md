# Panthera 3.2.0 - draft

One release for the NVDA add-on, SAPI for Windows, Android, and the native
Linux command-line host and C library. No Apple engine or voice data is
included; use your existing extracted data.

## NVDA add-on (Leopard, Snow Leopard and Lion)

- **Earcon pauses can preserve the surrounding sentence.** With text joining
  disabled, supported interior indexes now follow their audio positions, and
  a requested pause is inserted without ending the engine utterance. This
  avoids restarting sentence inflection at the pause and preserves the speech
  samples around it. Tomi confirmed that both Lion Alex and Leopard Alex keep
  the natural breath in the new Run-dialog listening recordings; automated
  driver checks also cover Snow Leopard Alex. (Issue 21.)
- **The checkbox is now "Join text blocks during Say All to preserve breaths".**
  Its saved choice and enabled default stay the same. Joining gives Alex the
  next block in time to breathe between sentences, but can report reading
  positions and callbacks ahead of speech. With joining disabled, the new
  path preserves the tested breaths within text already supplied together;
  separate Say All blocks are not joined.
- The new marker path applies to ordinary text with embedded commands off.
  Unsupported interfaces and a text rewrite crossing a marker fall back to
  the previous boundary behavior. Tiger's separate NVDA driver is unchanged.
- Built and tested against the NVDA 2026.2 interfaces, including playback
  timing, cancellation, fallback and the Windows secure-screen DLL path.

## Android

- **ZIP imports check the folder layout before extraction.** Generation folders
  (`tiger`, `leopard`, `snowleopard`, `lion`) can be at the ZIP root or directly
  inside `panthera/` or `panthera-data/`, regardless of case.
  A single engine's extracted contents at the root remain supported. Other
  nesting is rejected with layout guidance; engine, dictionary and voice
  checks still apply before existing data is replaced. Archives with Outspoken
  folders are refused with a message pointing to Outspoken TTS for Android.

- **Smoother rapid movement between short labels.** Repeated interruptions
  could restart the worker unnecessarily, adding a delay to the next item.
  A successfully started short request now gets up to 60 ms to finish, so
  the next request can reuse its worker. Longer requests retain the existing
  immediate retirement decision; cancelled audio is discarded immediately.
- Cancellation belongs to the request that started it. Completion and worker
  handoff wait for that cancellation decision, keeping an old request from
  retiring a replacement's worker.
- Tomi confirmed the rapid-navigation improvement on the Nothing Phone with
  Android 16. Automated checks cover all four generations, cancellation,
  replacement audio and worker ownership. This update retains Direct Boot,
  existing engine data and saved settings.

## SAPI (Windows)

The installer carries the rebuilt shared host and both x86 and x64 SAPI
components. Settings and resident-engine checks cover reuse, settings
changes and cancellation recovery. SAPI continues using its existing
streaming protocol; this release does not change its interruption policy or
replace estimated bookmarks with the new NVDA marker path.

## Linux

Both release tarballs are rebuilt from the release commit in CI and include
`tiger_host`, `libpanthera.so`, the public header, an example C client, README,
component notices and the corresponding sources.

- **i686:** the recommended x86 build, including on x86-64 Linux with 32-bit
  runtime support. Apple's i386 engine runs natively. The build targets Ubuntu
  22.04 and newer and uses Glint for AAC.
- **AArch64:** the Box64 + Glint build, compiled and self-tested on ARM hardware
  in CI. As documented in the previous release, it still awaits listening
  confirmation on an ARM Linux machine; successful phone and watch tests do
  not establish that result.
- **x86-64 emulation remains experimental and source-only.** It uses Unicorn,
  is slower than native i686, retains GPL dependencies, and has a documented
  intermittent startup failure. It remains a comparison CI build rather than
  an additional release asset. Box64 is used for AArch64, not substituted into
  this x86-64 build.

The existing client protocol is retained. The shared host adds an optional
versioned marker stream with request-scoped cleanup; clients only receive
marker records when they request that protocol explicitly. The new marker
adapter is enabled only on native hosts; emulated hosts refuse it before
speech and continue supporting the existing protocols.

## Draft validation still to finish before publication

Live NVDA/Earcons interaction and the updated Android candidate on Galaxy
Watch9 still need user confirmation. The listening files and automated checks
establish the results described above, not every voice or input boundary.
See [marker implementation and evidence](https://github.com/tgeczy/panthera-speech/blob/main/docs/speech-markers.md).

## Who helped shape this release

**Astra 6 helped shape this release end to end. It earned its keep this
session in every way.** Tomi requested this credit, and his careful listening
confirmed both the rapid-navigation improvement and the preserved breaths.

## Downloads

- **NVDA:** `pantheraspeech-3.2.0.nvda-addon`
- **SAPI:** `panthera-sapi-3.2.0-setup.exe`
- **Android:** `panthera-android-3.2.0.apk`, signed with the existing release key
- **Linux x86:** `panthera-linux-i686-3.2.0.tar.gz`
- **Linux ARM64:** `panthera-linux-aarch64-3.2.0.tar.gz`

GitHub also provides the tagged source archives. The Linux tarballs contain
additional pinned dependency sources; normal binaries include their notices.
