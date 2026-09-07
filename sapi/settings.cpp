#include "settings.h"

namespace panthera_sapi {
/* The user settings the NVDA driver has and SAPI users were living without,
 * kept by the settings program and read afresh on every Speak, so a change
 * takes effect on the very next thing spoken.  The two the *engine* reads
 * rather than this code -- phrasing and abbreviations -- take effect by
 * replacing the engine; see host_ensure.
 *
 * HKCU first and HKLM as a fallback, one value at a time.  A person's own
 * choice has to win, and it also has to be *possible*: a voice speaking under
 * a service account -- the Windows sign-in screen is the one that matters --
 * has an HKCU belonging to that account and holding nothing anybody chose.
 * Falling back per value rather than per key means a machine-wide default
 * still shows through for the settings this user never touched.
 *
 * Both hives are read without a view flag on purpose, so each build sees the
 * view it was registered in: the settings tool writes the machine-wide key
 * through both, exactly because HKLM\Software is redirected under WOW64 and
 * this DLL is built twice. */
static const HKEY SETTING_HIVES[2] = {HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};

DWORD setting_dword(const wchar_t *name, DWORD def) {
    for(int i=0;i<2;i++){
        HKEY k; DWORD v, n=sizeof v, t;
        if(RegOpenKeyExW(SETTING_HIVES[i],SETTING_KEY,0,KEY_READ,&k))continue;
        bool got = !RegQueryValueExW(k,name,0,&t,(BYTE*)&v,&n) && t==REG_DWORD;
        RegCloseKey(k);
        if(got)return v;
    }
    return def;
}
std::wstring setting_string(const wchar_t *name, const wchar_t *def) {
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
