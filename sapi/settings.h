#pragma once
#include <windows.h>
#include <string>

namespace panthera_sapi {
#define SETTING_KEY L"Software\\Panthera SAPI"
// Read fresh per utterance, HKCU first and HKLM fallback per typed value.
DWORD setting_dword(const wchar_t *name, DWORD fallback);
std::wstring setting_string(const wchar_t *name, const wchar_t *fallback);
}
