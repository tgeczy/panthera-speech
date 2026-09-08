#pragma once
#include <windows.h>

namespace panthera_sapi {
int diagLevel();
void sweep_logs();
void logline(const wchar_t *format, ...);
// Caller owns this inheritable NUL/file handle; never an undrained pipe.
HANDLE host_stderr();
}
