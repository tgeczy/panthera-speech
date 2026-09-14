#pragma once
#include <windows.h>
#include <string>
#include <utility>
#include <vector>

namespace panthera_sapi {
#define SETTING_KEY L"Software\\Panthera SAPI"
#define SETTINGS_FOLDER L"Panthera SAPI"
#define SETTINGS_FILE L"settings.toml"

/* Read fresh per utterance: this user's settings file, the machine's, then
 * HKCU and HKLM, one typed value at a time.  See settings.cpp. */
DWORD setting_dword(const wchar_t *name, DWORD fallback);
std::wstring setting_string(const wchar_t *name, const wchar_t *fallback);

/* The file itself, for anything that writes one -- the settings program,
 * when it is native code -- so it links the reader and writer the engine
 * ships rather than a copy.  A value is a number or a string; a table is
 * the file's lines in order, duplicates and all. */
struct SettingValue {
    bool isString;
    std::wstring text;
    long long number;
};
typedef std::vector<std::pair<std::wstring, SettingValue> > SettingsTable;

void settings_parse(const std::wstring &toml, SettingsTable &out);
std::wstring settings_serialize(const SettingsTable &table);
bool settings_read_file(const std::wstring &path, SettingsTable &out);
bool settings_write_file(const std::wstring &path, const SettingsTable &table);
/* Where the two files live: %APPDATA%\Panthera SAPI\settings.toml and
 * %ProgramData%\Panthera SAPI\settings.toml, unless the environment says
 * otherwise (PANTHERA_SAPI_SETTINGS_USER / _MACHINE, read once, for tests). */
std::wstring settings_user_path();
std::wstring settings_machine_path();
}
