# Windows AAC fallback

The native Windows host prefers Media Foundation and includes the pinned,
MIT-licensed Glint decoder when the Windows multimedia platform or AAC
transform cannot initialize. Alex and Vicki remain available on Windows N/KN
without installing the Media Feature Pack. Both NVDA host forms (EXE and the
secure-screen DLL) and SAPI use this same implementation.

Selection happens before feeding packets. A decoder is never changed halfway
through a stream, which would discard its overlap and alter speech. Converter
priming, paragraph timing and cancellation stay shared. `--capabilities` reports
the decoder actually selected; `--aac-check` diagnoses initialization.

Build with `./build.sh` for NVDA, or `sapi/build.ps1` for SAPI. Both call
`tools/build_windows.py`, which exports the exact Glint pin and applies the
same reviewed patches as the AAC comparisons. Set `GLINT_SOURCE` to an existing
official clone for offline builds. Otherwise the builder fetches the official
repository into the ignored dependency directory. NVDA packaging and the Inno
Setup installer include both MIT notices. No engine or voice data is packaged.

For reproducible missing-component tests, set `TIGER_AAC_MF_UNAVAILABLE` to
`library` or `decoder` in the test process's environment. These exercise the
initialization failure paths without changing system DLLs, codec registration,
or user preferences. Leave it unset for ordinary use. The tests compare full
renders against the existing Windows and reviewed Glint hosts, respectively.
`sapi/registered_test.ps1 -OutputDirectory <ignored-folder>` additionally renders
through the installed DLL and registered tokens; run it with both 32-bit and
64-bit PowerShell on a machine with the eight AAC/formant control voices.

No YY-Thunks dependency is needed by the added decoder imports. With MSVC
14.44 and a static C++ runtime, the only additions are SRW-lock and condition
variable APIs available since Vista. The binary imports no Media Foundation
DLL or redistributable C++ runtime DLL. This is import compatibility evidence;
it does not claim a new Windows 7 device test. See Microsoft's requirements for
[SRW locks](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-acquiresrwlockexclusive)
and [condition variables](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-sleepconditionvariablesrw).
