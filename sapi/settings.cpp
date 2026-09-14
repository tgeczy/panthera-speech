#include "settings.h"
#include <shlobj.h>
#include <cwctype>

namespace panthera_sapi {
/* The user settings the NVDA driver has and SAPI users were living without,
 * kept by the settings program and read afresh on every Speak, so a change
 * takes effect on the very next thing spoken.  The two the *engine* reads
 * rather than this code -- phrasing and abbreviations -- take effect by
 * replacing the engine; see host_ensure.
 *
 * **They live in a file now, and the registry is the fallback.**  Two files,
 * one typed value at a time: this user's, under %APPDATA%, and the machine's,
 * under %ProgramData%.  A person's own choice has to win, and it also has to
 * be *possible*: a voice speaking under a service account -- the Windows
 * sign-in screen is the one that matters -- has an %APPDATA% belonging to
 * that account and holding nothing anybody chose.  %ProgramData% is readable
 * by every account and writable by a standard user without elevation, so
 * the settings program can keep the machine copy current on every save, and
 * the sign-in screen speaks with the settings its owner chose last rather
 * than whatever an elevated trip mirrored months ago.  It also ends the
 * WOW64 dance: both builds of this DLL read the same two files.
 *
 * The registry keeps its place behind the files so nothing breaks on the day
 * of the upgrade: HKCU then HKLM, per value, as before, until the settings
 * program has moved a person's values out.  DataPath is not a setting and
 * never comes through here; every voice token carries its own.
 *
 * Flat TOML, because the reader has to fit in this file and the writer in a
 * PowerShell script: `Name = 1`, `Name = "word"`, `# comments`, and a line
 * that does not parse is ignored.  `true` and `false` are read as 1 and 0
 * as a courtesy to hand edits; the program writes numbers.  Keys are the
 * registry's own names, so nothing maps them anywhere.
 *
 * Typed, like the registry read was: a value of the wrong type is passed
 * over and the next source consulted, so a hand edit that breaks one value
 * cannot take a setting with it.  Strings are capped where the registry
 * buffer capped them.  Every string this DLL reads is matched against a
 * fixed vocabulary by its caller before it reaches the host, which is what
 * makes a machine-wide file any account can write safe for SYSTEM to read. */
static const HKEY SETTING_HIVES[2] = {HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
static const size_t STRING_CAP = 63;   /* the registry read's 64-wchar buffer */

/* ---- the file format ---------------------------------------------------- */

static std::wstring trim(const std::wstring &s) {
    size_t a = 0, b = s.size();
    while (a < b && iswspace(s[a])) a++;
    while (b > a && iswspace(s[b - 1])) b--;
    return s.substr(a, b - a);
}

static bool bare_key(const std::wstring &k) {
    if (k.empty()) return false;
    for (size_t i = 0; i < k.size(); i++) {
        wchar_t c = k[i];
        if (!(iswalnum(c) || c == L'_' || c == L'-')) return false;
    }
    return true;
}

void settings_parse(const std::wstring &toml, SettingsTable &out) {
    out.clear();
    size_t pos = 0;
    if (!toml.empty() && toml[0] == 0xFEFF) pos = 1;
    while (pos <= toml.size()) {
        size_t nl = toml.find(L'\n', pos);
        std::wstring line = toml.substr(pos, nl == std::wstring::npos ? std::wstring::npos : nl - pos);
        pos = (nl == std::wstring::npos) ? toml.size() + 1 : nl + 1;
        line = trim(line);
        if (line.empty() || line[0] == L'#') continue;
        size_t eq = line.find(L'=');
        if (eq == std::wstring::npos) continue;
        std::wstring key = trim(line.substr(0, eq));
        std::wstring value = trim(line.substr(eq + 1));
        if (!bare_key(key) || value.empty()) continue;
        SettingValue v; v.isString = false; v.number = 0;
        if (value[0] == L'"') {
            std::wstring s;
            bool closed = false;
            for (size_t i = 1; i < value.size(); i++) {
                wchar_t c = value[i];
                if (c == L'\\' && i + 1 < value.size()) { s += value[++i]; continue; }
                if (c == L'"') { closed = true; break; }
                s += c;
            }
            if (!closed) continue;
            if (s.size() > STRING_CAP) s.resize(STRING_CAP);
            v.isString = true; v.text = s;
        } else {
            size_t hash = value.find(L'#');
            if (hash != std::wstring::npos) value = trim(value.substr(0, hash));
            if (value == L"true") v.number = 1;
            else if (value == L"false") v.number = 0;
            else {
                size_t i = 0; bool neg = false;
                if (value[i] == L'+' || value[i] == L'-') { neg = value[i] == L'-'; i++; }
                if (i >= value.size()) continue;
                long long n = 0; bool ok = true;
                for (; i < value.size(); i++) {
                    if (value[i] < L'0' || value[i] > L'9') { ok = false; break; }
                    n = n * 10 + (value[i] - L'0');
                    if (n > 0x7fffffffLL) { ok = false; break; }
                }
                if (!ok) continue;
                v.number = neg ? -n : n;
            }
        }
        out.push_back(std::make_pair(key, v));
    }
}

std::wstring settings_serialize(const SettingsTable &table) {
    std::wstring out = L"# Panthera SAPI settings.  Written by the settings program; safe to edit by hand.\r\n"
                       L"# Numbers stay numbers and words stay in quotes; a line that does not parse is ignored.\r\n";
    for (size_t i = 0; i < table.size(); i++) {
        out += table[i].first;
        out += L" = ";
        if (table[i].second.isString) {
            out += L'"';
            const std::wstring &s = table[i].second.text;
            for (size_t j = 0; j < s.size(); j++) {
                if (s[j] == L'"' || s[j] == L'\\') out += L'\\';
                out += s[j];
            }
            out += L'"';
        } else {
            wchar_t buf[32];
            swprintf_s(buf, L"%lld", table[i].second.number);
            out += buf;
        }
        out += L"\r\n";
    }
    return out;
}

static bool read_whole_file(const std::wstring &path, std::wstring &text) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           0, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, 0);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size;
    if (!GetFileSizeEx(h, &size) || size.QuadPart > (1 << 20)) { CloseHandle(h); return false; }
    std::string bytes((size_t)size.QuadPart, '\0');
    DWORD got = 0;
    bool ok = size.QuadPart == 0 || (ReadFile(h, &bytes[0], (DWORD)bytes.size(), &got, 0) && got == bytes.size());
    CloseHandle(h);
    if (!ok) return false;
    int n = MultiByteToWideChar(CP_UTF8, 0, bytes.data(), (int)bytes.size(), 0, 0);
    text.assign((size_t)(n > 0 ? n : 0), L'\0');
    if (n > 0) MultiByteToWideChar(CP_UTF8, 0, bytes.data(), (int)bytes.size(), &text[0], n);
    return true;
}

bool settings_read_file(const std::wstring &path, SettingsTable &out) {
    std::wstring text;
    if (!read_whole_file(path, text)) return false;
    settings_parse(text, out);
    return true;
}

bool settings_write_file(const std::wstring &path, const SettingsTable &table) {
    /* Whole and then swapped in: the engine may be reading the old one. */
    std::wstring text = settings_serialize(table);
    int n = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), (int)text.size(), 0, 0, 0, 0);
    std::string bytes((size_t)(n > 0 ? n : 0), '\0');
    if (n > 0) WideCharToMultiByte(CP_UTF8, 0, text.c_str(), (int)text.size(), &bytes[0], n, 0, 0);
    size_t slash = path.find_last_of(L"\\/");
    if (slash != std::wstring::npos) {
        std::wstring dir = path.substr(0, slash);
        CreateDirectoryW(dir.c_str(), 0);
    }
    std::wstring tmp = path + L".tmp";
    HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, 0, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD put = 0;
    bool ok = bytes.empty() || (WriteFile(h, bytes.data(), (DWORD)bytes.size(), &put, 0) && put == bytes.size());
    CloseHandle(h);
    if (!ok) { DeleteFileW(tmp.c_str()); return false; }
    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(tmp.c_str());
        return false;
    }
    return true;
}

/* ---- where the files are ------------------------------------------------ */

static std::wstring known_folder(REFKNOWNFOLDERID id, const wchar_t *envName) {
    PWSTR p = 0;
    std::wstring dir;
    if (SUCCEEDED(SHGetKnownFolderPath(id, KF_FLAG_DONT_VERIFY, 0, &p)) && p) {
        dir = p;
        CoTaskMemFree(p);
    } else {
        wchar_t buf[MAX_PATH];
        DWORD n = GetEnvironmentVariableW(envName, buf, MAX_PATH);
        if (n && n < MAX_PATH) dir = buf;
    }
    return dir;
}

static std::wstring env_override(const wchar_t *name) {
    wchar_t buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(name, buf, MAX_PATH);
    return (n && n < MAX_PATH) ? std::wstring(buf) : std::wstring();
}

static std::wstring settings_path(REFKNOWNFOLDERID id, const wchar_t *envName, const wchar_t *overrideName) {
    std::wstring o = env_override(overrideName);
    if (!o.empty()) return o;
    std::wstring dir = known_folder(id, envName);
    if (dir.empty()) return dir;
    return dir + L"\\" SETTINGS_FOLDER L"\\" SETTINGS_FILE;
}

std::wstring settings_user_path() {
    return settings_path(FOLDERID_RoamingAppData, L"APPDATA", L"PANTHERA_SAPI_SETTINGS_USER");
}
std::wstring settings_machine_path() {
    return settings_path(FOLDERID_ProgramData, L"ProgramData", L"PANTHERA_SAPI_SETTINGS_MACHINE");
}

/* ---- the cache ---------------------------------------------------------- */
/*
 * Eight values are read per utterance.  Each file is stat'ed on every
 * lookup and re-read only when its size or write time has moved, so a
 * change in the settings program reaches the next thing spoken and an
 * unchanged file costs one GetFileAttributesEx.  One lock, because this
 * DLL's threading model is Both and the registry read before it was
 * lockless only by opening and closing its own key every time.
 */
struct FileCache {
    std::wstring path;
    bool resolved;
    bool exists;
    FILETIME written;
    ULONGLONG size;
    SettingsTable table;
    FileCache() : resolved(false), exists(false), size(0) { written.dwLowDateTime = written.dwHighDateTime = 0; }
};

static CRITICAL_SECTION g_lock;
static INIT_ONCE g_once = INIT_ONCE_STATIC_INIT;
static FileCache g_user, g_machine;

static BOOL CALLBACK init_lock(PINIT_ONCE, PVOID, PVOID *) {
    InitializeCriticalSection(&g_lock);
    return TRUE;
}

/* Called under the lock. */
static void refresh(FileCache &c, std::wstring (*where)()) {
    if (!c.resolved) { c.path = where(); c.resolved = true; }
    if (c.path.empty()) { c.exists = false; c.table.clear(); return; }
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExW(c.path.c_str(), GetFileExInfoStandard, &a) ||
        (a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
        c.exists = false; c.table.clear(); c.size = 0;
        c.written.dwLowDateTime = c.written.dwHighDateTime = 0;
        return;
    }
    ULONGLONG size = ((ULONGLONG)a.nFileSizeHigh << 32) | a.nFileSizeLow;
    if (c.exists && size == c.size && a.ftLastWriteTime.dwLowDateTime == c.written.dwLowDateTime &&
        a.ftLastWriteTime.dwHighDateTime == c.written.dwHighDateTime)
        return;
    SettingsTable fresh;
    if (!settings_read_file(c.path, fresh)) return;   /* mid-swap: keep what we had */
    c.table.swap(fresh);
    c.exists = true; c.size = size; c.written = a.ftLastWriteTime;
}

/* The last line naming `name` in `t`, or -1: a hand edit that repeats a key
 * means the later one, which is what a person reading the file would say. */
static int find_key(const SettingsTable &t, const wchar_t *name) {
    for (int i = (int)t.size() - 1; i >= 0; i--)
        if (_wcsicmp(t[(size_t)i].first.c_str(), name) == 0) return i;
    return -1;
}

/* -> true with `out` filled when this user's file or the machine's holds
 * `name` as the wanted type.  A wrong-typed value passes through to the next
 * source, which is the registry read's rule carried over. */
static bool from_files(const wchar_t *name, bool wantString, SettingValue &out) {
    InitOnceExecuteOnce(&g_once, init_lock, 0, 0);
    EnterCriticalSection(&g_lock);
    bool found = false;
    FileCache *sources[2] = {&g_user, &g_machine};
    std::wstring (*where[2])() = {settings_user_path, settings_machine_path};
    for (int i = 0; i < 2 && !found; i++) {
        refresh(*sources[i], where[i]);
        if (!sources[i]->exists) continue;
        int at = find_key(sources[i]->table, name);
        if (at < 0) continue;
        const SettingValue &v = sources[i]->table[(size_t)at].second;
        if (v.isString != wantString) continue;
        out = v; found = true;
    }
    LeaveCriticalSection(&g_lock);
    return found;
}

/* ---- what the engine calls ---------------------------------------------- */

DWORD setting_dword(const wchar_t *name, DWORD def) {
    SettingValue v;
    if (from_files(name, false, v)) {
        if (v.number < 0) return def;      /* a DWORD has no negative to give */
        return (DWORD)v.number;
    }
    for(int i=0;i<2;i++){
        HKEY k; DWORD val, n=sizeof val, t;
        if(RegOpenKeyExW(SETTING_HIVES[i],SETTING_KEY,0,KEY_READ,&k))continue;
        bool got = !RegQueryValueExW(k,name,0,&t,(BYTE*)&val,&n) && t==REG_DWORD;
        RegCloseKey(k);
        if(got)return val;
    }
    return def;
}
std::wstring setting_string(const wchar_t *name, const wchar_t *def) {
    SettingValue v;
    if (from_files(name, true, v)) return v.text;
    for(int i=0;i<2;i++){
        HKEY k; wchar_t buf[64]; DWORD n=sizeof buf-sizeof(wchar_t), t;
        if(RegOpenKeyExW(SETTING_HIVES[i],SETTING_KEY,0,KEY_READ,&k))continue;
        bool got = !RegQueryValueExW(k,name,0,&t,(BYTE*)buf,&n) && t==REG_SZ;
        RegCloseKey(k);
        if(got){ buf[n/sizeof(wchar_t)]=0; return buf; }
    }
    return def;
}

} // namespace panthera_sapi
