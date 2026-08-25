#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sapi.h>
#include <sapiddk.h>
#include <olectl.h>
#include <string>
#include <vector>
#include <cmath>
#include <cwctype>

static HMODULE g_module;
static long g_objects;
static const CLSID CLSID_Panthera = {0xc1f7fc55,0x3512,0x4f5d,{0xa6,0xeb,0xf5,0x32,0x20,0xbe,0x46,0x93}};
static const unsigned REQ_MAGIC_STREAM = 0x54475234, RSP_MAGIC = 0x54475253;
static const GUID PantheraWaveFormatEx = {0xc31adbae,0x527f,0x4ff5,{0xa2,0x30,0xf6,0x2b,0xb6,0x1f,0xf7,0x0c}};

static bool exact(HANDLE h, void *p, DWORD n, bool write) {
    BYTE *b=(BYTE*)p; DWORD done=0, x;
    while(done<n) {
        BOOL ok=write?WriteFile(h,b+done,n-done,&x,0):ReadFile(h,b+done,n-done,&x,0);
        if(!ok || !x) return false; done+=x;
    }
    return true;
}
static std::wstring module_dir() {
    wchar_t p[MAX_PATH]; GetModuleFileNameW(g_module,p,MAX_PATH);
    wchar_t *s=wcsrchr(p,L'\\'); if(s)*s=0; return p;
}
static std::string utf8(const std::wstring &s) {
    int n=WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),0,0,0,0);
    std::string r(n,0); if(n) WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),&r[0],n,0,0); return r;
}
static std::wstring token_string(ISpObjectToken *t, const wchar_t *name) {
    wchar_t *v=0; std::wstring r;
    if(t && SUCCEEDED(t->GetStringValue(name,&v)) && v) { r=v; CoTaskMemFree(v); }
    return r;
}

/* The two user settings the NVDA driver has and SAPI users were living
 * without, kept in HKCU by the settings program and read per utterance --
 * each Speak launches a fresh host, so a change takes effect on the very
 * next thing spoken. */
static DWORD setting_dword(const wchar_t *name, DWORD def) {
    HKEY k; DWORD v=def, n=sizeof v, t;
    if(!RegOpenKeyExW(HKEY_CURRENT_USER,L"Software\\Panthera SAPI",0,KEY_READ,&k)){
        if(RegQueryValueExW(k,name,0,&t,(BYTE*)&v,&n)||t!=REG_DWORD)v=def;
        RegCloseKey(k);
    }
    return v;
}
static std::wstring setting_string(const wchar_t *name, const wchar_t *def) {
    HKEY k; wchar_t buf[64]; DWORD n=sizeof buf-sizeof(wchar_t), t; std::wstring r=def;
    if(!RegOpenKeyExW(HKEY_CURRENT_USER,L"Software\\Panthera SAPI",0,KEY_READ,&k)){
        if(!RegQueryValueExW(k,name,0,&t,(BYTE*)buf,&n)&&t==REG_SZ){
            buf[n/sizeof(wchar_t)]=0; r=buf;
        }
        RegCloseKey(k);
    }
    return r;
}

/* The engine really parses [[...]] in any text it is handed, and a wiki
 * page's [[Main Page]] does not merely change how things sound -- measured,
 * the engine eats the bracketed words entirely.  Same bounds as the NVDA
 * driver's COMMAND_RE: a close within 64 characters, and an unclosed "[["
 * stays literal rather than swallowing the paragraph. */
static void strip_commands(std::wstring &t) {
    size_t i=0;
    while((i=t.find(L"[[",i))!=std::wstring::npos){
        size_t close=t.find(L"]]",i+2);
        if(close==std::wstring::npos||close-(i+2)>64){i+=2;continue;}
        t.erase(i,close+2-i);
    }
}

/* The engine reads numbers well up to six digits and spells them out one
 * digit at a time from seven -- and grouped digits read correctly, so the
 * repair is the NVDA driver's: put the separators back.  Runs only, so
 * "0.7.3" (three one-digit runs) is untouched, and never inside a [[...]]
 * command, where a comma would corrupt it. */
static void fix_long_numbers(std::wstring &t) {
    size_t i=0;
    while(i<t.size()){
        if(t.compare(i,2,L"[[")==0){
            size_t close=t.find(L"]]",i+2);
            if(close!=std::wstring::npos&&close-(i+2)<=64){i=close+2;continue;}
        }
        if(iswdigit(t[i])){
            size_t start=i;
            while(i<t.size()&&iswdigit(t[i]))i++;
            size_t len=i-start;
            if(len>=7){
                for(size_t pos=i-3;pos>start;pos-=3){
                    t.insert(pos,1,L',');
                    i++;
                    if(pos<start+4)break;
                }
            }
            continue;
        }
        i++;
    }
}

class Engine : public ISpTTSEngine, public ISpObjectWithToken {
    LONG refs; ISpObjectToken *token;
public:
    Engine():refs(1),token(0){InterlockedIncrement(&g_objects);}
    ~Engine(){if(token)token->Release();InterlockedDecrement(&g_objects);}
    STDMETHODIMP QueryInterface(REFIID i,void **p){
        if(!p)return E_POINTER; *p=0;
        if(i==IID_IUnknown||i==IID_ISpTTSEngine)*p=(ISpTTSEngine*)this;
        else if(i==IID_ISpObjectWithToken)*p=(ISpObjectWithToken*)this;
        else return E_NOINTERFACE; AddRef(); return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef(){return InterlockedIncrement(&refs);}
    STDMETHODIMP_(ULONG) Release(){ULONG n=InterlockedDecrement(&refs);if(!n)delete this;return n;}
    STDMETHODIMP SetObjectToken(ISpObjectToken *t){if(!t)return E_INVALIDARG;if(token)return E_UNEXPECTED;token=t;t->AddRef();return S_OK;}
    STDMETHODIMP GetObjectToken(ISpObjectToken **t){if(!t)return E_POINTER;*t=token;if(token)token->AddRef();return token?S_OK:S_FALSE;}
    STDMETHODIMP GetOutputFormat(const GUID*,const WAVEFORMATEX*,GUID *id,WAVEFORMATEX **wf){
        if(!id||!wf)return E_POINTER; *id=PantheraWaveFormatEx;
        WAVEFORMATEX f={WAVE_FORMAT_PCM,1,22050,44100,2,16,0};
        *wf=(WAVEFORMATEX*)CoTaskMemAlloc(sizeof f);if(!*wf)return E_OUTOFMEMORY;**wf=f;return S_OK;
    }
    STDMETHODIMP Speak(DWORD,REFGUID,const WAVEFORMATEX*,const SPVTEXTFRAG *frags,ISpTTSEngineSite *site){
        if(!token||!site)return E_UNEXPECTED;
        std::wstring text;
        for(auto f=frags;f;f=f->pNext){
            /* JAWS sends each word as its own SPVA_Speak fragment with an
             * SPVA_Bookmark between every pair, and a bookmark fragment's
             * text is its name.  Appending blindly reads the names aloud;
             * appending without a separator runs the words together.  Only
             * text meant to be heard goes in, with a space restored at the
             * seam when neither side brought one. */
            switch(f->State.eAction){
            case SPVA_Speak: case SPVA_SpellOut: case SPVA_Pronounce: break;
            default: continue;
            }
            if(!f->pTextStart||!f->ulTextLen)continue;
            if(!text.empty()&&!iswspace(text.back())&&!iswspace(f->pTextStart[0]))
                text.push_back(L' ');
            text.append(f->pTextStart,f->ulTextLen);
        }
        if(text.empty())return S_OK;
        if(!setting_dword(L"AcceptCommands",0))
            strip_commands(text);
        if(text.empty())return S_OK;
        if(setting_string(L"NumberStyle",L"fix")==L"fix")
            fix_long_numbers(text);
        /* Inflection is an embedded command prefix, exactly as the NVDA
         * driver sends it: pmod is inflection times two, nothing at the
         * halfway default so the untouched utterance stays byte-for-byte
         * what Apple ships.  No return-to-default bookkeeping here because
         * each utterance is a fresh process: channel state cannot outlive
         * it.  Prepended after the stripping on purpose. */
        {
            DWORD infl=setting_dword(L"Inflection",50);
            if(infl>100)infl=100;
            if(infl!=50){
                wchar_t cmd[32];
                swprintf_s(cmd,32,L"[[pmod %u]]",infl*2);
                text.insert(0,cmd);
            }
        }
        /* Phrasing rides the same TIGER_PARAMS the NVDA host reads, and
         * abbreviations the same TIGER_NO_ABBREV.  The child inherits the
         * environment; process-global, so two truly simultaneous Speak
         * calls in one application could momentarily see each other's
         * value -- the same value, unless the user changes the setting
         * mid-word. */
        {
            std::wstring ph=setting_string(L"Phrasing",L"fewest");
            const wchar_t *thr = ph==L"fewest"?L"-8":ph==L"fewer"?L"-4":
                                 ph==L"more"?L"0":ph==L"most"?L"5":NULL;
            if(thr)
                SetEnvironmentVariableW(L"TIGER_PARAMS",
                    (std::wstring(L"Boundaries.SilThreshold=")+thr).c_str());
            else
                SetEnvironmentVariableW(L"TIGER_PARAMS",NULL);
            SetEnvironmentVariableW(L"TIGER_NO_ABBREV",
                setting_dword(L"ExpandAbbreviations",1)?NULL:L"1");
        }
        std::wstring root=token_string(token,L"DataPath"), gen=token_string(token,L"Generation"), voice=token_string(token,L"VoiceName");
        std::wstring tree=root+L"\\"+gen, mt=tree+L"\\Speech\\Synthesizers\\MacinTalk.SpeechSynthesizer\\Contents\\MacOS\\MacinTalk";
        std::wstring sd=tree+L"\\SpeechDictionary.framework\\Versions\\A\\SpeechDictionary", vd=tree+L"\\Speech\\Voices";
        std::wstring cmd=L"\""+module_dir()+L"\\panthera_host.exe\" --serve \""+mt+L"\" \""+sd+L"\" \""+vd+L"\"";
        SECURITY_ATTRIBUTES sa={sizeof(sa),0,TRUE}; HANDLE inR,inW,outR,outW;
        if(!CreatePipe(&inR,&inW,&sa,0)||!CreatePipe(&outR,&outW,&sa,0))return HRESULT_FROM_WIN32(GetLastError());
        SetHandleInformation(inW,HANDLE_FLAG_INHERIT,0);SetHandleInformation(outR,HANDLE_FLAG_INHERIT,0);
        STARTUPINFOW si={sizeof(si)};si.dwFlags=STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW;si.wShowWindow=SW_HIDE;si.hStdInput=inR;si.hStdOutput=outW;si.hStdError=GetStdHandle(STD_ERROR_HANDLE);
        PROCESS_INFORMATION pi={}; std::vector<wchar_t> mutableCmd(cmd.begin(),cmd.end());mutableCmd.push_back(0);
        BOOL made=CreateProcessW(0,mutableCmd.data(),0,0,TRUE,CREATE_NO_WINDOW,0,module_dir().c_str(),&si,&pi);CloseHandle(inR);CloseHandle(outW);
        if(!made){CloseHandle(inW);CloseHandle(outR);return HRESULT_FROM_WIN32(GetLastError());}
        long sapiRate=0;
        site->GetRate(&sapiRate);
        if(sapiRate < -10)sapiRate=-10;if(sapiRate > 10)sapiRate=10;
        /* SAPI's rate is logarithmic: zero is the engine default and ten
         * steps span roughly a factor of three in either direction.  Rate
         * boost raises only the top -- the engine honours 1200 wpm without a
         * stumble, and it was measured doing so -- and never the bottom,
         * because a boost that also made slow slower would be a different
         * setting wearing this one's name. */
        double top=(setting_dword(L"RateBoost",0)&&sapiRate>0)?6.667:3.0;
        int rate=(int)(180.0*pow(top,(double)sapiRate/10.0)+0.5);
        /* SAPI's per-utterance pitch, from the XML the application sent;
         * the request's pitch field is an offset in tenths of a semitone
         * from the voice's own, so one SAPI step is a bit over a semitone
         * and the ten-step ends land an octave out, matching the NVDA
         * slider's ends. */
        int pitch=0;
        if(frags){
            long pa=frags->State.PitchAdj.MiddleAdj;
            if(pa<-10)pa=-10;if(pa>10)pa=10;
            pitch=(int)(pa*12);
        }
        std::string v=utf8(voice), u=utf8(text); unsigned request=REQ_MAGIC_STREAM,flags=0,nv=(unsigned)v.size(),nt=(unsigned)u.size();
        bool ok=exact(inW,&request,4,true)&&exact(inW,&rate,4,true)&&exact(inW,&pitch,4,true)&&exact(inW,&flags,4,true)&&exact(inW,&nv,4,true)&&exact(inW,&nt,4,true)&&exact(inW,(void*)v.data(),nv,true)&&exact(inW,(void*)u.data(),nt,true);CloseHandle(inW);
        unsigned magic=0;int status=-1;bool aborted=false;
        ok=ok&&exact(outR,&magic,4,false)&&exact(outR,&status,4,false)&&magic==RSP_MAGIC;
        std::vector<BYTE> audio;
        while(ok){
            unsigned frames=0;
            if(!exact(outR,&frames,4,false)){ok=false;break;}
            if(!frames)break;
            unsigned bytes=frames*2; audio.resize(bytes);
            if(!exact(outR,audio.data(),bytes,false)){ok=false;break;}
            if(site->GetActions()&SPVES_ABORT){aborted=true;TerminateProcess(pi.hProcess,0);break;}
            ULONG wrote=0;if(FAILED(site->Write(audio.data(),bytes,&wrote))){ok=false;break;}
        }
        CloseHandle(outR);
        if(!aborted)WaitForSingleObject(pi.hProcess,2000);
        CloseHandle(pi.hThread);CloseHandle(pi.hProcess);
        return aborted||(ok&&status==0)?S_OK:E_FAIL;
    }
};
class Factory:public IClassFactory{LONG refs;public:Factory():refs(1){InterlockedIncrement(&g_objects);} ~Factory(){InterlockedDecrement(&g_objects);} STDMETHODIMP QueryInterface(REFIID i,void**p){if(!p)return E_POINTER;*p=0;if(i==IID_IUnknown||i==IID_IClassFactory)*p=this;else return E_NOINTERFACE;AddRef();return S_OK;} STDMETHODIMP_(ULONG)AddRef(){return InterlockedIncrement(&refs);} STDMETHODIMP_(ULONG)Release(){ULONG n=InterlockedDecrement(&refs);if(!n)delete this;return n;} STDMETHODIMP CreateInstance(IUnknown*o,REFIID i,void**p){if(o)return CLASS_E_NOAGGREGATION;Engine*e=new Engine;HRESULT h=e->QueryInterface(i,p);e->Release();return h;} STDMETHODIMP LockServer(BOOL x){InterlockedExchangeAdd(&g_objects,x?1:-1);return S_OK;}};

STDAPI DllCanUnloadNow(){return g_objects?S_FALSE:S_OK;}
STDAPI DllGetClassObject(REFCLSID c,REFIID i,void **p){if(c!=CLSID_Panthera)return CLASS_E_CLASSNOTAVAILABLE;Factory*f=new Factory;HRESULT h=f->QueryInterface(i,p);f->Release();return h;}
static HRESULT reg(bool add){
    wchar_t cls[64];StringFromGUID2(CLSID_Panthera,cls,64);std::wstring key=L"Software\\Classes\\CLSID\\"+std::wstring(cls);
    if(!add){RegDeleteTreeW(HKEY_LOCAL_MACHINE,key.c_str());return S_OK;}
    HKEY h,k; if(RegCreateKeyExW(HKEY_LOCAL_MACHINE,key.c_str(),0,0,0,KEY_WRITE,0,&h,0))return SELFREG_E_CLASS;
    const wchar_t name[]=L"Panthera SAPI speech engine";RegSetValueExW(h,0,0,REG_SZ,(BYTE*)name,sizeof(name));
    std::wstring sub=key+L"\\InprocServer32",path=module_dir()+L"\\panthera_sapi.dll";RegCloseKey(h);
    if(RegCreateKeyExW(HKEY_LOCAL_MACHINE,sub.c_str(),0,0,0,KEY_WRITE,0,&k,0))return SELFREG_E_CLASS;
    RegSetValueExW(k,0,0,REG_SZ,(BYTE*)path.c_str(),(DWORD)((path.size()+1)*2));const wchar_t both[]=L"Both";RegSetValueExW(k,L"ThreadingModel",0,REG_SZ,(BYTE*)both,sizeof(both));RegCloseKey(k);return S_OK;
}
STDAPI DllRegisterServer(){return reg(true);} STDAPI DllUnregisterServer(){return reg(false);}
BOOL WINAPI DllMain(HINSTANCE h,DWORD why,LPVOID){if(why==DLL_PROCESS_ATTACH){g_module=h;DisableThreadLibraryCalls(h);}return TRUE;}
