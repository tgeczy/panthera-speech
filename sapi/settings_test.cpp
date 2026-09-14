/* The settings reader without a host: the file format, the order of the
 * sources, the typed fall-through, and that a rewrite reaches the very next
 * lookup.  Built and run by build.ps1 like rules_test, linking the shipping
 * settings.cpp.  Both files are pointed at scratch paths through the
 * environment and the registry is redirected, so nobody's settings are
 * read or written. */
#include "settings.h"
#include <cstdio>
#include <string>
using namespace panthera_sapi;

static int g_fail;
static void check(bool ok, const char *what) {
    if(!ok){ g_fail++; printf("  FAIL  %s\n", what); }
    else     printf("  ok    %s\n", what);
}

/* Redirect only this process's registry access, as resident_test does. */
class TestRegistry {
    HKEY user, machine;
    std::wstring path;
public:
    bool ready;
    TestRegistry():user(0),machine(0),ready(false) {
        wchar_t suffix[80];
        swprintf_s(suffix,L"Software\\PantheraTests\\Settings-%lu",GetCurrentProcessId());
        path=suffix;
        if(RegCreateKeyExW(HKEY_CURRENT_USER,(path+L"\\User").c_str(),0,0,0,KEY_ALL_ACCESS,0,&user,0) ||
           RegCreateKeyExW(HKEY_CURRENT_USER,(path+L"\\Machine").c_str(),0,0,0,KEY_ALL_ACCESS,0,&machine,0))return;
        if(RegOverridePredefKey(HKEY_CURRENT_USER,user))return;
        if(RegOverridePredefKey(HKEY_LOCAL_MACHINE,machine)){RegOverridePredefKey(HKEY_CURRENT_USER,0);return;}
        ready=true;
    }
    ~TestRegistry() {
        if(ready){RegOverridePredefKey(HKEY_CURRENT_USER,0);RegOverridePredefKey(HKEY_LOCAL_MACHINE,0);}
        if(user)RegCloseKey(user);
        if(machine)RegCloseKey(machine);
        RegDeleteTreeW(HKEY_CURRENT_USER,path.c_str());
    }
};
static void reg_dword(HKEY hive, const wchar_t *name, DWORD v) {
    HKEY k;
    if(!RegCreateKeyExW(hive,SETTING_KEY,0,0,0,KEY_WRITE,0,&k,0)){
        RegSetValueExW(k,name,0,REG_DWORD,(BYTE*)&v,sizeof v);RegCloseKey(k);
    }
}
static void reg_string(HKEY hive, const wchar_t *name, const wchar_t *v) {
    HKEY k;
    if(!RegCreateKeyExW(hive,SETTING_KEY,0,0,0,KEY_WRITE,0,&k,0)){
        RegSetValueExW(k,name,0,REG_SZ,(BYTE*)v,(DWORD)((wcslen(v)+1)*sizeof(wchar_t)));RegCloseKey(k);
    }
}

/* Raw bytes, so the parser sees exactly what a hand edit would leave. */
static bool write_raw(const std::wstring &path, const char *utf8) {
    HANDLE h=CreateFileW(path.c_str(),GENERIC_WRITE,0,0,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,0);
    if(h==INVALID_HANDLE_VALUE)return false;
    DWORD put=0; DWORD n=(DWORD)strlen(utf8);
    bool ok=WriteFile(h,utf8,n,&put,0)&&put==n;
    CloseHandle(h);
    Sleep(20);   /* the cache keys on the write time; two writes in one tick would be one */
    return ok;
}
static SettingValue num(long long n){ SettingValue v; v.isString=false; v.number=n; return v; }
static SettingValue str(const wchar_t *s){ SettingValue v; v.isString=true; v.number=0; v.text=s; return v; }

int wmain() {
    wchar_t temp[MAX_PATH];
    if(!GetTempPathW(MAX_PATH,temp)){fprintf(stderr,"no temp folder\n");return 1;}
    wchar_t leaf[64]; swprintf_s(leaf,L"panthera-settings-test-%lu",GetCurrentProcessId());
    std::wstring dir=std::wstring(temp)+leaf;
    CreateDirectoryW(dir.c_str(),0);
    std::wstring user=dir+L"\\user.toml", machine=dir+L"\\machine.toml";
    SetEnvironmentVariableW(L"PANTHERA_SAPI_SETTINGS_USER",user.c_str());
    SetEnvironmentVariableW(L"PANTHERA_SAPI_SETTINGS_MACHINE",machine.c_str());
    TestRegistry reg;
    if(!reg.ready){fprintf(stderr,"cannot isolate the registry\n");return 1;}
    check(settings_user_path()==user&&settings_machine_path()==machine,"the environment names both files");

    /* 1. The format. */
    SettingsTable t;
    settings_parse(L"\xFEFF# a comment\r\nA = 1\r\nB = \"two\"\r\n C = true \r\nD = false\r\nE = -5\r\n"
                   L"F = 2 # trailing\r\nG = \"never closed\r\nH = 99999999999\r\nno equals\r\n= 3\r\n"
                   L"Bad Key = 1\r\nI = \"a \\\"q\\\" \\\\ b\"\r\nA = 7\r\nJ = \"" +
                   std::wstring(70,L'x') + L"\"\n", t);
    check(t.size()==9,"lines that do not parse are left out and nothing else is");
    check(t.size()>0&&t[0].first==L"A"&&!t[0].second.isString&&t[0].second.number==1,"a number reads as a number");
    check(t.size()>1&&t[1].second.isString&&t[1].second.text==L"two","a quoted word reads as a string");
    check(t.size()>3&&t[2].second.number==1&&t[3].second.number==0,"true and false read as 1 and 0");
    check(t.size()>4&&t[4].second.number==-5,"a negative number parses");
    check(t.size()>5&&t[5].second.number==2,"a trailing comment is not part of the number");
    check(t.size()>6&&t[6].second.text==L"a \"q\" \\ b","escapes inside quotes are honoured");
    check(t.size()>7&&t[7].first==L"A"&&t[7].second.number==7,"a repeated key keeps both lines in order");
    check(t.size()>8&&t[8].second.text.size()==63,"a string is capped where the registry buffer capped it");
    SettingsTable back;
    settings_parse(settings_serialize(t),back);
    bool same=back.size()==t.size();
    for(size_t i=0;same&&i<t.size();i++)
        same=back[i].first==t[i].first&&back[i].second.isString==t[i].second.isString&&
             back[i].second.number==t[i].second.number&&back[i].second.text==t[i].second.text;
    check(same,"a serialized table parses back as it was");
    std::wstring shown=settings_serialize(t);
    check(shown.find(L"I = \"a \\\"q\\\" \\\\ b\"")!=std::wstring::npos&&shown.find(L"C = 1\r\n")!=std::wstring::npos,
          "serializing quotes and escapes strings and writes numbers as numbers");

    /* 2. The order of the sources, one typed value at a time. */
    check(setting_dword(L"Inflection",50)==50,"no file and no registry: the default");
    reg_dword(HKEY_LOCAL_MACHINE,L"Inflection",35);
    check(setting_dword(L"Inflection",50)==35,"no file and no HKCU: HKLM");
    reg_dword(HKEY_CURRENT_USER,L"Inflection",30);
    check(setting_dword(L"Inflection",50)==30,"no file: HKCU beats HKLM");
    check(write_raw(machine,"Inflection = 20\nPhrasing = \"more\"\n"),"a machine file can be written");
    check(setting_dword(L"Inflection",50)==20,"the machine file beats the registry");
    check(write_raw(user,"Inflection = 10\n"),"a user file can be written");
    check(setting_dword(L"Inflection",50)==10,"the user file beats the machine file");
    check(setting_string(L"Phrasing",L"fewest")==L"more","a value the user file lacks comes from the machine file");
    reg_string(HKEY_CURRENT_USER,L"NumberStyle",L"words");
    check(setting_string(L"NumberStyle",L"fix")==L"words","a value neither file has comes from the registry");
    check(setting_string(L"Inflection",L"none")==L"none","a number is not a string: the default");
    write_raw(user,"Inflection = \"loud\"\n");
    check(setting_dword(L"Inflection",50)==20,"a wrong-typed value in the user file passes to the machine file");
    write_raw(machine,"Inflection = \"loud\"\nPhrasing = \"more\"\n");
    check(setting_dword(L"Inflection",50)==30,"wrong-typed in both files passes to the registry");
    write_raw(user,"Inflection = true\n");
    check(setting_dword(L"Inflection",50)==1,"a hand-written true reads as 1");
    write_raw(user,"Inflection = -1\n");
    check(setting_dword(L"Inflection",50)==50,"a negative number is not a DWORD: the default");
    write_raw(user,"inflection = 12\n");
    check(setting_dword(L"Inflection",50)==12,"names match without regard to case, as the registry's did");

    /* 3. A change reaches the next lookup: rewritten, same length, gone. */
    write_raw(user,"Inflection = 25\n");
    check(setting_dword(L"Inflection",50)==25,"a rewrite reaches the next lookup");
    write_raw(user,"Inflection = 26\n");
    check(setting_dword(L"Inflection",50)==26,"a rewrite of the same length reaches the next lookup");
    DeleteFileW(user.c_str());
    check(setting_dword(L"Inflection",50)==30,"a deleted user file drops out of the order");
    SettingsTable written;
    written.push_back(std::make_pair(std::wstring(L"Inflection"),num(40)));
    written.push_back(std::make_pair(std::wstring(L"Phrasing"),str(L"most")));
    check(settings_write_file(user,written),"the writer puts a file on disk");
    check(setting_dword(L"Inflection",50)==40&&setting_string(L"Phrasing",L"fewest")==L"most",
          "what the writer wrote is what the reader reads");
    SettingsTable stale; stale.push_back(std::make_pair(std::wstring(L"Inflection"),num(41)));
    Sleep(20);
    check(settings_write_file(user,stale)&&setting_dword(L"Inflection",50)==41,"the writer replaces a file that is being read");
    check(GetFileAttributesW((user+L".tmp").c_str())==INVALID_FILE_ATTRIBUTES,"the writer leaves no temporary behind");

    DeleteFileW(user.c_str()); DeleteFileW(machine.c_str()); RemoveDirectoryW(dir.c_str());
    printf(g_fail?"%d settings checks FAILED\n":"settings checks passed\n",g_fail);
    return g_fail?1:0;
}
