/* The resident host, exercised through the engine itself.
 *
 * rules_test.cpp proves the text rules still match the Python spec.  This
 * proves the things that only exist because the host now outlives the
 * utterance, and every one of them is a bug that would otherwise be found
 * by ear:
 *
 *   - an utterance sounds the same when it is not the first one
 *   - returning inflection to the middle actually returns it
 *   - a settings change is not silently ignored by a host that has already
 *     read its environment
 *   - an interrupted utterance leaves an engine that still speaks
 *
 * It needs extracted speech data and skips cleanly without it, so it gates
 * the machines that can answer and stays quiet on the ones that cannot.
 */
#include "panthera_sapi.cpp"
#include <cstdio>
#include <algorithm>
#include <atomic>
#include <thread>

static int g_fail;

/* Redirect only this process's registry access. Build checks must neither
 * change the listener's preferences nor turn an inherited default into an
 * explicit user choice. Child hosts receive their settings via environment. */
class TestSettings {
    HKEY user, machine;
    std::wstring path, files;
public:
    bool ready;
    TestSettings():user(0),machine(0),ready(false) {
        wchar_t suffix[80];
        swprintf_s(suffix,L"Software\\PantheraTests\\Resident-%lu",GetCurrentProcessId());
        path=suffix;
        /* The settings files come before the registry, so they are pointed
         * at a scratch folder too -- before the first lookup, which is when
         * the DLL reads its environment -- or Tomi's own file would shadow
         * every value this test pins. */
        wchar_t temp[MAX_PATH];
        if(GetTempPathW(MAX_PATH,temp)){
            swprintf_s(suffix,L"panthera-resident-test\\settings-%lu",GetCurrentProcessId());
            files=std::wstring(temp)+suffix;
            CreateDirectoryW((std::wstring(temp)+L"panthera-resident-test").c_str(),0);
            CreateDirectoryW(files.c_str(),0);
            SetEnvironmentVariableW(L"PANTHERA_SAPI_SETTINGS_USER",(files+L"\\user.toml").c_str());
            SetEnvironmentVariableW(L"PANTHERA_SAPI_SETTINGS_MACHINE",(files+L"\\machine.toml").c_str());
        }
        if(RegCreateKeyExW(HKEY_CURRENT_USER,(path+L"\\User").c_str(),0,0,0,
                          KEY_ALL_ACCESS,0,&user,0) ||
           RegCreateKeyExW(HKEY_CURRENT_USER,(path+L"\\Machine").c_str(),0,0,0,
                          KEY_ALL_ACCESS,0,&machine,0))return;
        if(RegOverridePredefKey(HKEY_CURRENT_USER,user))return;
        if(RegOverridePredefKey(HKEY_LOCAL_MACHINE,machine)){
            RegOverridePredefKey(HKEY_CURRENT_USER,0);return;
        }
        ready=true;
    }
    ~TestSettings() {
        if(ready){RegOverridePredefKey(HKEY_CURRENT_USER,0);
                  RegOverridePredefKey(HKEY_LOCAL_MACHINE,0);}
        if(user)RegCloseKey(user);
        if(machine)RegCloseKey(machine);
        RegDeleteTreeW(HKEY_CURRENT_USER,path.c_str());
        if(!files.empty()){
            DeleteFileW((files+L"\\user.toml").c_str());
            DeleteFileW((files+L"\\machine.toml").c_str());
            RemoveDirectoryW(files.c_str());
        }
    }
};
static void check(bool ok, const char *what) {
    if(!ok){ g_fail++; printf("  FAIL  %s\n", what); }
    else     printf("  ok    %s\n", what);
}

/* ---- the two interfaces the engine actually leans on ------------------- */

class FakeToken : public ISpObjectToken {
    LONG refs; std::wstring root, gen, voice;
public:
    FakeToken(const std::wstring &r,const std::wstring &g,const std::wstring &v)
        :refs(1),root(r),gen(g),voice(v){}
    STDMETHODIMP QueryInterface(REFIID i,void**p){
        if(i==IID_IUnknown||i==IID_ISpObjectToken||i==IID_ISpDataKey){
            *p=this;AddRef();return S_OK;
        }
        *p=0;return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef(){return ++refs;}
    STDMETHODIMP_(ULONG) Release(){return --refs;}
    /* The one method the engine calls. */
    STDMETHODIMP GetStringValue(LPCWSTR name,LPWSTR *value){
        const std::wstring *v = !name?0:
            !wcscmp(name,L"DataPath")?&root:
            !wcscmp(name,L"Generation")?&gen:
            !wcscmp(name,L"VoiceName")?&voice:0;
        if(!v)return E_INVALIDARG;
        *value=(LPWSTR)CoTaskMemAlloc((v->size()+1)*sizeof(wchar_t));
        if(!*value)return E_OUTOFMEMORY;
        wcscpy_s(*value,v->size()+1,v->c_str());
        return S_OK;
    }
    /* ISpDataKey, unused */
    STDMETHODIMP SetData(LPCWSTR,ULONG,const BYTE*){return E_NOTIMPL;}
    STDMETHODIMP GetData(LPCWSTR,ULONG*,BYTE*){return E_NOTIMPL;}
    STDMETHODIMP SetStringValue(LPCWSTR,LPCWSTR){return E_NOTIMPL;}
    STDMETHODIMP SetDWORD(LPCWSTR,DWORD){return E_NOTIMPL;}
    STDMETHODIMP GetDWORD(LPCWSTR,DWORD*){return E_NOTIMPL;}
    STDMETHODIMP OpenKey(LPCWSTR,ISpDataKey**){return E_NOTIMPL;}
    STDMETHODIMP CreateKey(LPCWSTR,ISpDataKey**){return E_NOTIMPL;}
    STDMETHODIMP DeleteKey(LPCWSTR){return E_NOTIMPL;}
    STDMETHODIMP DeleteValue(LPCWSTR){return E_NOTIMPL;}
    STDMETHODIMP EnumKeys(ULONG,LPWSTR*){return E_NOTIMPL;}
    STDMETHODIMP EnumValues(ULONG,LPWSTR*){return E_NOTIMPL;}
    /* ISpObjectToken, unused */
    STDMETHODIMP SetId(LPCWSTR,LPCWSTR,BOOL){return E_NOTIMPL;}
    STDMETHODIMP GetId(LPWSTR*){return E_NOTIMPL;}
    STDMETHODIMP GetCategory(ISpObjectTokenCategory**){return E_NOTIMPL;}
    STDMETHODIMP CreateInstance(IUnknown*,DWORD,REFIID,void**){return E_NOTIMPL;}
    STDMETHODIMP GetStorageFileName(REFCLSID,LPCWSTR,LPCWSTR,ULONG,LPWSTR*){return E_NOTIMPL;}
    STDMETHODIMP RemoveStorageFileName(REFCLSID,LPCWSTR,BOOL){return E_NOTIMPL;}
    STDMETHODIMP Remove(const CLSID*){return E_NOTIMPL;}
    STDMETHODIMP IsUISupported(LPCWSTR,void*,ULONG,IUnknown*,BOOL*){return E_NOTIMPL;}
    STDMETHODIMP DisplayUI(HWND,LPCWSTR,LPCWSTR,void*,ULONG,IUnknown*){return E_NOTIMPL;}
    STDMETHODIMP MatchesAttributes(LPCWSTR,BOOL*){return E_NOTIMPL;}
};

class FakeSite : public ISpTTSEngineSite {
    LONG refs;
public:
    std::vector<BYTE> audio; ULONG marks; DWORD abortAfter; std::atomic<bool> abortNow;
    USHORT volume=100, laterVolume=100;
    DWORD changeVolumeAfter=0;
    long rate=0;
    /* Every bookmark: its name, the offset it claims, and how much audio
     * had already been written when it arrived. */
    struct Event { std::wstring name; ULONGLONG offset; size_t written; };
    std::vector<Event> events;
    /* A host that takes at most `maxTake` bytes per Write, and nothing at
     * all on every `refuseEvery`th call -- Windows 7's SAPI with its buffer
     * full. */
    ULONG maxTake=0; int refuseEvery=0, writes=0;
    /* ISpVoice::Skip: once `skipAfter` bytes are written, ask to skip
     * `skipAsk` sentences; `skipAt` is how much audio there was when the
     * engine answered, `skipped` what it said it skipped. */
    size_t skipAfter=0, skipAt=0; long skipAsk=0, skipped=-999;
    bool skipAsked=false, skipInfoFails=false;
    FakeSite(DWORD abortAfterBytes)
        :refs(1),marks(0),abortAfter(abortAfterBytes),abortNow(false){}
    STDMETHODIMP QueryInterface(REFIID,void**p){*p=this;AddRef();return S_OK;}
    STDMETHODIMP_(ULONG) AddRef(){return ++refs;}
    STDMETHODIMP_(ULONG) Release(){return --refs;}
    STDMETHODIMP AddEvents(const SPEVENT *e,ULONG n){
        marks+=n;
        for(ULONG i=0;i<n;i++)
            events.push_back({e[i].elParamType==SPET_LPARAM_IS_STRING&&e[i].lParam?
                              (const wchar_t*)e[i].lParam:L"",
                              e[i].ullAudioStreamOffset,audio.size()});
        return S_OK;
    }
    STDMETHODIMP GetEventInterest(ULONGLONG *i){*i=SPEI_TTS_BOOKMARK;return S_OK;}
    STDMETHODIMP_(DWORD) GetActions(){
        if(abortNow)return SPVES_ABORT;
        if(abortAfter&&audio.size()>=abortAfter)return SPVES_ABORT;
        return (skipAfter&&!skipAsked&&audio.size()>=skipAfter)?SPVES_SKIP:0;
    }
    /* When the first and the last audio arrived, on QueryPerformanceCounter's
     * clock: two voices taking turns never overlap. */
    std::atomic<LONGLONG> firstWrite{0}, lastWrite{0};   /* read across threads */
    STDMETHODIMP Write(const void *p,ULONG n,ULONG *w){
        LARGE_INTEGER now; QueryPerformanceCounter(&now);
        if(!firstWrite)firstWrite=now.QuadPart;
        lastWrite=now.QuadPart;
        writes++;
        if(refuseEvery&&writes%refuseEvery==0){if(w)*w=0;return S_OK;}
        ULONG take=maxTake&&n>maxTake?maxTake:n;
        audio.insert(audio.end(),(const BYTE*)p,(const BYTE*)p+take);
        if(w)*w=take; return S_OK;
    }
    STDMETHODIMP GetRate(long *r){*r=rate;return S_OK;}
    STDMETHODIMP GetVolume(USHORT *v){
        *v=changeVolumeAfter&&audio.size()>=changeVolumeAfter?laterVolume:volume;
        return S_OK;
    }
    STDMETHODIMP GetSkipInfo(SPVSKIPTYPE *t,long *n){
        if(skipInfoFails)return E_NOTIMPL;
        *t=SPVST_SENTENCE;*n=skipAsk;return S_OK;
    }
    STDMETHODIMP CompleteSkip(long n){skipAsked=true;skipped=n;skipAt=audio.size();return S_OK;}
};

/* ---- driving it ------------------------------------------------------- */

static std::wstring g_root, g_gen, g_voice;

static HRESULT say(Engine *e, const wchar_t *text, std::vector<BYTE> *out,
                   DWORD abortAfter, USHORT volume=100,
                   DWORD changeVolumeAfter=0, USHORT laterVolume=100) {
    FakeSite site(abortAfter);
    site.volume=volume; site.changeVolumeAfter=changeVolumeAfter;
    site.laterVolume=laterVolume;
    SPVTEXTFRAG frag; memset(&frag,0,sizeof frag);
    frag.State.eAction=SPVA_Speak;
    frag.State.Volume=100;
    frag.pTextStart=text; frag.ulTextLen=(ULONG)wcslen(text);
    HRESULT hr=e->Speak(0,GUID_NULL,0,&frag,&site);
    if(out)*out=site.audio;
    return hr;
}

/* A fragment list as SAPI hands it over once it has parsed the markup.
 * Fragments are separated by "|"; "#name" is a bookmark; a fragment may
 * open with {r=N} (rate), {p=N} (pitch), {v=N} (volume), {s} (spell) or
 * {sil=N} (a silence of N ms, with no text). */
struct Script {
    std::vector<SPVTEXTFRAG> frags; std::vector<std::wstring> text;
    explicit Script(const std::wstring &s) {
        size_t at=0;
        for(;;){
            size_t bar=s.find(L'|',at);
            text.push_back(s.substr(at,bar==std::wstring::npos?std::wstring::npos:bar-at));
            if(bar==std::wstring::npos)break;
            at=bar+1;
        }
        frags.assign(text.size(),SPVTEXTFRAG());
        for(size_t i=0;i<text.size();i++){
            SPVTEXTFRAG &f=frags[i]; memset(&f,0,sizeof f);
            f.State.eAction=SPVA_Speak; f.State.Volume=100;
            std::wstring &t=text[i];
            while(!t.empty()&&t[0]==L'{'){
                size_t close=t.find(L'}');
                std::wstring m=t.substr(1,close-1); t.erase(0,close+1);
                if(m==L"s")f.State.eAction=SPVA_SpellOut;
                else if(!m.compare(0,4,L"sil=")){f.State.eAction=SPVA_Silence;f.State.SilenceMSecs=_wtol(m.c_str()+4);}
                else if(!m.compare(0,2,L"r="))f.State.RateAdj=_wtol(m.c_str()+2);
                else if(!m.compare(0,2,L"p="))f.State.PitchAdj.MiddleAdj=_wtol(m.c_str()+2);
                else if(!m.compare(0,2,L"v="))f.State.Volume=(USHORT)_wtol(m.c_str()+2);
            }
            if(!t.empty()&&t[0]==L'#'){f.State.eAction=SPVA_Bookmark;t.erase(0,1);}
            f.pTextStart=t.empty()?0:t.c_str(); f.ulTextLen=(ULONG)t.size();
            if(i+1<text.size())f.pNext=&frags[i+1];
        }
    }
};
static HRESULT say_script(Engine *e, const wchar_t *script, FakeSite &site) {
    Script s(script);
    return e->Speak(0,GUID_NULL,0,s.frags.data(),&site);
}
/* Samples of a run of exact silence at least this long, anywhere. */
static bool has_silence(const std::vector<BYTE> &pcm, size_t ms) {
    size_t need=ms*22050/1000, run=0;
    const short *s=(const short*)pcm.data();
    for(size_t i=0;i<pcm.size()/2;i++){ run=s[i]?0:run+1; if(run>=need)return true; }
    return false;
}

static void set_dword(const wchar_t *name, DWORD v) {
    HKEY k;
    if(!RegCreateKeyExW(HKEY_CURRENT_USER,L"Software\\Panthera SAPI",0,0,0,
                        KEY_WRITE,0,&k,0)){
        RegSetValueExW(k,name,0,REG_DWORD,(BYTE*)&v,sizeof v);RegCloseKey(k);
    }
}
static void set_string(const wchar_t *name, const wchar_t *v) {
    HKEY k;
    if(!RegCreateKeyExW(HKEY_CURRENT_USER,L"Software\\Panthera SAPI",0,0,0,
                        KEY_WRITE,0,&k,0)){
        RegSetValueExW(k,name,0,REG_SZ,(BYTE*)v,
                       (DWORD)((wcslen(v)+1)*sizeof(wchar_t)));RegCloseKey(k);
    }
}

/* ---- a host that misbehaves on purpose --------------------------------- */
/*
 * A game crashed, rarely, after hours of route announcements, and the chain
 * ran: a stray print in the host landed in the protocol stream, the client
 * read ASCII as a frame count in the billions, resize threw bad_alloc, and
 * the exception sailed through the COM boundary into the application.  The
 * host's stream is defended at its end now; these checks prove this end
 * survives a rogue host anyway -- and that a silent or dead one costs an
 * utterance, not the session.  None of them need speech data, so they run
 * on every machine that builds.
 */

static HANDLE g_rogue_write;              /* test's end of the response pipe */
static HANDLE g_rogue_sink;               /* keeps the request pipe writable */

static void rogue_install(const std::wstring &tree, HANDLE proc) {
    host_drop();
    SECURITY_ATTRIBUTES sa={sizeof(sa),0,TRUE};
    HANDLE inR=0,inW=0,outR=0,outW=0;
    /* The read end stays open, held by the test: close it and the engine's
     * *request write* fails with a broken pipe before the scripted response
     * is ever read -- which quietly turned both Speak checks into tests of
     * nothing, and was caught by the byte count below. */
    CreatePipe(&inR,&inW,&sa,1<<20);      /* requests land here, unread */
    CreatePipe(&outR,&outW,&sa,1<<20);    /* the test scripts responses */
    g_rogue_sink=inR;
    g_proc=proc; g_in=inW; g_out=outR; g_rogue_write=outW;
    /* Match what speakInner will compute, so host_ensure reuses this host
     * rather than spawning a real one.  The values mirror the settings the
     * caller pins: Phrasing "fewest", expansion on. */
    g_hostTree=tree;
    g_hostParams=L"Boundaries.SilThreshold=-8";
    g_hostAbbrev=L"";
    g_inflSent=false;
}
static void rogue_teardown() {
    if(g_rogue_write){CloseHandle(g_rogue_write);g_rogue_write=0;}
    if(g_rogue_sink){CloseHandle(g_rogue_sink);g_rogue_sink=0;}
    host_drop();
}
static HANDLE self_handle() {             /* real, waitable, never signaled */
    HANDLE h=0;
    /* Query and synchronize only, no PROCESS_TERMINATE: host_drop calls
     * TerminateProcess on whatever it holds, and with this handle that call
     * fails instead of killing the test. */
    DuplicateHandle(GetCurrentProcess(),GetCurrentProcess(),
                    GetCurrentProcess(),&h,PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,
                    FALSE,0);
    return h;
}
static void rogue_checks() {
    printf("rogue host:\n");
    /* A place SetObjectToken's existence probe will accept. */
    wchar_t tmp[MAX_PATH];
    GetEnvironmentVariableW(L"TEMP",tmp,MAX_PATH);
    std::wstring root=std::wstring(tmp)+L"\\panthera_rogue";
    std::wstring mt=root+L"\\tiger\\Speech\\Synthesizers"
                    L"\\MacinTalk.SpeechSynthesizer\\Contents\\MacOS";
    {   /* Creating a directory that exists fails harmlessly, so every
         * prefix is simply attempted. */
        size_t from=0;
        for(;;){
            size_t cut=mt.find(L'\\',from);
            CreateDirectoryW(mt.substr(0,cut).c_str(),0);
            if(cut==std::wstring::npos)break;
            from=cut+1;
        }
        HANDLE f=CreateFileW((mt+L"\\MacinTalk").c_str(),GENERIC_WRITE,0,0,
                             CREATE_ALWAYS,0,0);
        if(f!=INVALID_HANDLE_VALUE)CloseHandle(f);
    }
    /* Pin the settings the fake host's identity depends on, and a short
     * deadline so the wedge check is not a thirty-second wait. */
    DWORD wasInfl=setting_dword(L"Inflection",50);
    DWORD wasExpand=setting_dword(L"ExpandAbbreviations",1);
    DWORD wasCommands=setting_dword(L"AcceptCommands",0);
    DWORD wasTimeout=setting_dword(L"ReadTimeoutMs",30000);
    std::wstring wasPhrasing=setting_string(L"Phrasing",L"fewest");
    set_dword(L"Inflection",50); set_string(L"Phrasing",L"fewest");
    set_dword(L"ExpandAbbreviations",1); set_dword(L"AcceptCommands",0);
    set_dword(L"ReadTimeoutMs",1000);
    std::wstring tree=root+L"\\tiger";

    FakeToken tok(root,L"tiger",L"Fred");
    Engine *e=new Engine;
    check(SUCCEEDED(e->SetObjectToken(&tok)),"the rogue token is accepted");

    /* 1. exact_wait alone: the four ways out that are not data. */
    {
        HANDLE alive=self_handle();
        rogue_install(tree,alive);
        BYTE b[4]; DWORD t0;
        DWORD word=0x12345678; DWORD w=0;
        WriteFile(g_rogue_write,&word,4,&w,0);
        check(exact_wait(g_out,b,4,0)==RW_OK,"exact_wait reads data");
        FakeSite site(0); site.abortNow=true;
        check(exact_wait(g_out,b,4,&site)==RW_ABORT,
              "an abort ends the wait early");
        t0=GetTickCount();
        check(exact_wait(g_out,b,4,0)==RW_FAIL,
              "a silent host runs out of deadline");
        check(GetTickCount()-t0>=800&&GetTickCount()-t0<8000,
              "and the deadline is the configured one");
        CloseHandle(g_rogue_write);g_rogue_write=0;   /* now a broken pipe */
        check(exact_wait(g_out,b,4,0)==RW_FAIL,"a broken pipe fails at once");
        rogue_teardown();
    }
    {   /* a dead host with an empty pipe fails without waiting */
        STARTUPINFOW si={sizeof si};PROCESS_INFORMATION pi={};
        wchar_t cmd[]=L"cmd.exe /c exit 0";
        if(CreateProcessW(0,cmd,0,0,FALSE,CREATE_NO_WINDOW,0,0,&si,&pi)){
            WaitForSingleObject(pi.hProcess,10000);CloseHandle(pi.hThread);
            rogue_install(tree,pi.hProcess);
            BYTE b[4];DWORD t0=GetTickCount();
            check(exact_wait(g_out,b,4,0)==RW_FAIL,
                  "a dead host fails the read");
            check(GetTickCount()-t0<800,"without spending the deadline");
            rogue_teardown();
        }
    }

    /* 2. Through Speak: the counts that used to be a crash.  The host end
     * of the stream is the load-bearing fix -- a *small* misread is
     * arithmetic this side cannot detect -- so what is being promised here
     * is only: whatever lands on the pipe, the application survives. */
    {
        rogue_install(tree,self_handle());
        DWORD w=0; unsigned hdr[2]={RSP_MAGIC,0};
        WriteFile(g_rogue_write,hdr,8,&w,0);
        unsigned garbage=0x615b2020;              /* "  [a" read as a count */
        WriteFile(g_rogue_write,&garbage,4,&w,0);
        std::vector<BYTE> out;
        HRESULT hr=say(e,L"route update",&out,0);
        check(FAILED(hr),"a billion-frame count is refused, not allocated");
        check(g_proc==0,"and the desynced host is dropped");
        rogue_teardown();
    }
    {   /* the shape from the field: a good chunk, then a stray print */
        rogue_install(tree,self_handle());
        check(host_alive(),"the fake host reads as alive");
        {
            HANDLE beforeOut=g_out;
            host_ensure(tree,L"x",L"x",L"x",
                        L"Boundaries.SilThreshold=-8",L"");
            check(g_out==beforeOut,"host_ensure reuses the fake");
        }
        DWORD w=0; unsigned hdr[2]={RSP_MAGIC,0};
        WriteFile(g_rogue_write,hdr,8,&w,0);
        unsigned frames=229;
        WriteFile(g_rogue_write,&frames,4,&w,0);
        std::vector<short> pcm(229,64);
        WriteFile(g_rogue_write,pcm.data(),458,&w,0);
        static const char stray[]="  [shim] first call: CFRunLoopAddTimer\n";
        WriteFile(g_rogue_write,stray,(DWORD)strlen(stray),&w,0);
        std::vector<BYTE> out;
        HRESULT hr=say(e,L"route update",&out,0);
        check(FAILED(hr),"a stray print mid-stream fails the utterance");
        if(out.size()!=458)printf("  --    delivered %u byte(s), hr=%08lx\n",
                                  (unsigned)out.size(),(unsigned long)hr);
        check(out.size()==458,"after delivering the audio that was real");
        check(g_proc==0,"and that host is dropped too");
        rogue_teardown();
    }

    e->Release();
    set_dword(L"Inflection",wasInfl);
    set_dword(L"ExpandAbbreviations",wasExpand);
    set_dword(L"AcceptCommands",wasCommands);
    set_dword(L"ReadTimeoutMs",wasTimeout);
    set_string(L"Phrasing",wasPhrasing.c_str());
}

/* ---- what SAPI asks of an engine ---------------------------------------- */
/*
 * Each of these was found missing in TGSpeechBox's SAPI engine first, fixed
 * there, and then found missing here too: the parts of the contract that
 * have nothing to do with which engine is speaking.
 */
static const size_t PAD = 2204;                /* the 50 ms tail, in bytes */
static bool names_in_order(const FakeSite &s, std::initializer_list<const wchar_t*> want) {
    if(s.events.size()!=want.size())return false;
    size_t i=0;
    for(auto w:want)if(s.events[i++].name!=w)return false;
    return true;
}
static void contract_checks(Engine *e, const wchar_t *LINE, const std::vector<BYTE> &first) {
    printf("the SAPI contract:\n");
    /* Windows 7's SAPI takes nothing while its buffer is full, and any
     * host may take part of a block. */
    {
        FakeSite s(0); s.maxTake=1000; s.refuseEvery=3;
        say_script(e,LINE,s);
        check(s.audio==first,"writes taken in part, or refused, lose no audio");
    }
    /* Markup combines with the application's own settings. */
    {
        std::wstring half=std::wstring(L"{v=50}")+LINE;
        FakeSite s(0); say_script(e,half.c_str(),s);
        bool ok=s.audio.size()==first.size();
        for(size_t i=0;ok&&i<first.size()/2;i++)
            ok=((const short*)s.audio.data())[i]==((const short*)first.data())[i]/2;
        check(ok,"volume markup scales the speech");
        std::wstring eighty=std::wstring(L"{v=80}")+LINE;
        FakeSite a(0); a.volume=50; say_script(e,eighty.c_str(),a);
        FakeSite b(0); b.volume=40; say_script(e,LINE,b);
        check(a.audio==b.audio,"volume markup is a share of the application's volume");
        std::wstring plus2=std::wstring(L"{r=2}")+LINE, minus2=std::wstring(L"{r=-2}")+LINE;
        FakeSite c(0); c.rate=5; say_script(e,plus2.c_str(),c);
        FakeSite d(0); d.rate=7; say_script(e,LINE,d);
        check(!c.audio.empty()&&c.audio==d.audio,"rate markup adds to the application's rate");
        FakeSite c2(0); c2.rate=5; say_script(e,minus2.c_str(),c2);
        FakeSite d2(0); d2.rate=3; say_script(e,LINE,d2);
        check(c2.audio==d2.audio,"and slows it too");
        std::wstring way=std::wstring(L"{r=8}")+LINE;
        FakeSite f(0); f.rate=8; say_script(e,way.c_str(),f);
        FakeSite g(0); g.rate=10; say_script(e,LINE,g);
        check(f.audio==g.audio,"inside SAPI's -10..+10");
        FakeSite flat(0); say_script(e,L"{p=0}The quick brown fox|{p=0}jumps over the lazy dog.",flat);
        check(flat.audio==first,"fragments in one state are one utterance");
        FakeSite rise(0); say_script(e,L"{p=0}The quick brown fox|{p=5}jumps over the lazy dog.",rise);
        check(!rise.audio.empty()&&rise.audio!=flat.audio,"pitch markup reaches a later fragment");
    }
    /* Bookmarks go out at their own place, no later than their audio. */
    {
        FakeSite s(0);
        say_script(e,L"The|#1|quick|#2|brown|#3|fox|#4|jumps over the lazy dog.",s);
        bool order=names_in_order(s,{L"1",L"2",L"3",L"4"});
        check(order,"a bookmark between every word: all of them, in order");
        bool rising=order, before=order, inside=order;
        for(size_t i=0;order&&i<s.events.size();i++){
            if(i&&s.events[i].offset<=s.events[i-1].offset)rising=false;
            if(s.events[i].written>s.events[i].offset)before=false;
            if(!s.events[i].offset||s.events[i].offset>=s.audio.size()-PAD)inside=false;
        }
        check(rising,"each later than the one before");
        check(inside,"each inside the speech");
        check(before,"and each sent before the audio it stands in front of");
        bool aligned=true;
        for(auto &ev:s.events)if(ev.offset%2)aligned=false;
        check(aligned,"on a sample boundary");
        FakeSite h(0);
        say_script(e,L"#head|The quick brown fox jumps over the lazy dog.|#tail",h);
        check(names_in_order(h,{L"head",L"tail"})&&h.events[0].offset==0&&
              h.events[1].offset==h.audio.size()-PAD,
              "a mark before the words is at the start, one after them at their end");
    }
    /* A sync must not change what the text rules make of the words: "Dr."
     * before a name is Doctor, whether a sentence-start sync or a JAWS mark
     * falls after it -- 8a73b6a let both split the rule, and the name was
     * read as a street. */
    {
        FakeSite doctor(0); say_script(e,L"Phycologist Doctor Kirk spoke.",doctor);
        FakeSite plain(0); say_script(e,L"Phycologist Dr. Kirk spoke.",plain);
        check(!plain.audio.empty()&&plain.audio==doctor.audio,"\"Dr.\" before a name is still Doctor");
        FakeSite jaws(0); say_script(e,L"Phycologist|#1|Dr.|#2|Kirk|#3|spoke.",jaws);
        check(jaws.audio==doctor.audio,"and still Doctor with a bookmark after it");
        check(names_in_order(jaws,{L"1",L"2",L"3"}),"whose marks all arrive, in order");
    }
    /* A text that makes no sound still answers its marks. */
    {
        const wchar_t *quiet[]={L"\x2014",L"...",L" ",L"\x00a0",L"\x200b",L"-----",L"."};
        int answered=0;
        for(auto q:quiet){
            std::wstring script=std::wstring(L"#7|")+q+L"|#8";
            FakeSite s(0); say_script(e,script.c_str(),s);
            if(names_in_order(s,{L"7",L"8"}))answered++;
        }
        check(answered==7,"marks around a text that makes no sound are answered");
        FakeSite m(0); say_script(e,L"#1|one|#2|\x2014|#3|two|#4",m);        check(names_in_order(m,{L"1",L"2",L"3",L"4"}),"and a silent piece between words keeps its marks in order");
    }
    /* <spell> reads the letters, not the word. */
    {
        FakeSite w(0); say_script(e,L"hello",w);
        FakeSite s(0); say_script(e,L"{s}hello",s);
        check(!s.audio.empty()&&s.audio!=w.audio,"spelled text is not read as the word");
        check(s.audio.size()>w.audio.size()*13/10,"it takes as long as letters do");
        FakeSite n(0); say_script(e,L"42",n);
        FakeSite d(0); say_script(e,L"{s}42",d);
        check(!d.audio.empty()&&d.audio!=n.audio,"spelled digits are digits, not a number");
    }
    /* <silence msec="500"/> is half a second of silence. */
    {
        /* The tag divides the text, so the measure is the two halves said
         * on their own -- "Hello. World." in one piece has a pause of the
         * engine's own between its sentences. */
        FakeSite h(0); say_script(e,L"Hello.",h);
        FakeSite w(0); say_script(e,L"World.",w);
        FakeSite b(0); say_script(e,L"Hello.|{sil=500}|World.",b);
        check(has_silence(b.audio,450),"a silence tag is silence");
        long extra=(long)b.audio.size()-(long)(h.audio.size()-PAD+w.audio.size());
        check(extra>=22050-200&&extra<=22050+200,"of the length it asked for, between the words");
    }
    /* A skip request moves by sentences, and speech goes on from there.
     * Three sentences of clearly different lengths; what follows a skip has
     * to be, sample for sample, the text from the sentence it landed on. */
    {
        const std::wstring S1=L"One.", S2=L"This second sentence is a good deal longer than the first one.",
                           S3=L"And a third, of middling length.";
        const std::wstring ALL=S1+L" "+S2+L" "+S3, FROM2=S2+L" "+S3;
        /* What follows a skip comes from a host started for it, so the
         * references are made the same way. */
        host_drop(); FakeSite all(0); say_script(e,ALL.c_str(),all);
        host_drop(); FakeSite from2(0); say_script(e,FROM2.c_str(),from2);
        host_drop(); FakeSite from3(0); say_script(e,S3.c_str(),from3);
        FakeSite one(0); say_script(e,S1.c_str(),one);
        /* Where speech went on from is told by length: the three sentences
         * are far apart in it.  Not by bytes, because Lion's host is not
         * byte-reproducible across a fresh start -- measured on the
         * unchanged engine too, a frame added or moved somewhere inside
         * (two sentences on a fresh host: 257792 bytes or 257794) -- and
         * a few frames either way is still the same sentence. */
        auto lands=[&](const FakeSite &s,const FakeSite &rest){
            long after=(long)s.audio.size()-(long)s.skipAt, want=(long)rest.audio.size();
            size_t head=s.skipAt<2048?s.skipAt:2048;     /* it began as the text */
            bool ok=s.audio.size()>=s.skipAt&&after>=want-32&&after<=want+32&&
                    std::equal(s.audio.begin(),s.audio.begin()+head,all.audio.begin());
            if(!ok)
                printf("  --    skipped=%ld at %u: %ld byte(s) after, %ld expected\n",
                       s.skipped,(unsigned)s.skipAt,after,want);
            return ok;
        };
        /* A second past where the first sentence alone ends is well inside
         * the second, whatever pause the engine puts between them. */
        size_t inFirst=2048, inSecond=one.audio.size()-PAD+44100;
        FakeSite a(0); a.skipAfter=inFirst; a.skipAsk=1; say_script(e,ALL.c_str(),a);
        check(a.skipped==1&&lands(a,from2),"+1 in the first sentence goes on with the second");
        FakeSite b(0); b.skipAfter=inFirst; b.skipAsk=2; say_script(e,ALL.c_str(),b);
        check(b.skipped==2&&lands(b,from3),"+2 goes on with the third");
        FakeSite c(0); c.skipAfter=inSecond; c.skipAsk=0; say_script(e,ALL.c_str(),c);
        check(c.skipped==0&&lands(c,from2),"0 goes back to the start of the sentence");
        FakeSite d(0); d.skipAfter=inSecond; d.skipAsk=-1; say_script(e,ALL.c_str(),d);
        check(d.skipped==-1&&lands(d,all),"-1 goes back to the one before");
        FakeSite f(0); f.skipAfter=inFirst; f.skipAsk=5; say_script(e,ALL.c_str(),f);
        check(f.skipped==2&&f.audio.size()<=f.skipAt+PAD,
              "more than the text has: what could be skipped, and the call ends");
        FakeSite g(0); g.skipAfter=inFirst; g.skipInfoFails=true; say_script(e,ALL.c_str(),g);
        check(g.skipped==0&&g.audio.size()<=g.skipAt+PAD,"a host that cannot say how far is told none");
        std::wstring marked=std::wstring(L"#a|")+S1+L" |#b|"+S2+L" |#c|"+S3;
        FakeSite m(0); m.skipAfter=inFirst; m.skipAsk=2; say_script(e,marked.c_str(),m);
        check(names_in_order(m,{L"a",L"c"}),"marks skipped over are not reported, the one before the landing is");
        std::vector<BYTE> after; say(e,LINE,&after,0);
        check(after==first,"and the next call is unaffected");
    }
    /* No mark after an abort. */
    {
        FakeSite s(4096);
        say_script(e,L"#1|This is a deliberately long paragraph, the kind a listener "
                     L"interrupts long before it has finished saying itself.|#2",s);
        bool late=false;
        for(auto &ev:s.events)if(ev.name==L"2")late=true;
        check(!late,"an interrupted utterance reports no mark after the interruption");
        std::vector<BYTE> after; say(e,LINE,&after,0);
        check(after==first,"and the next one is unharmed");
    }
}

int wmain(int argc, wchar_t **argv) {
    TestSettings settings;
    if(!settings.ready){fprintf(stderr,"cannot isolate test settings\n");return 1;}
    InitializeCriticalSection(&g_hostLock); g_lockReady=true;
    rogue_checks();
    wchar_t appdata[MAX_PATH];
    if(!GetEnvironmentVariableW(L"APPDATA",appdata,MAX_PATH))return 0;
    g_root  = argc>1?argv[1]:(std::wstring(appdata)+L"\\nvda\\macintalk");
    g_gen   = argc>2?argv[2]:L"tiger";
    g_voice = argc>3?argv[3]:L"Fred";
    std::wstring probe=g_root+L"\\"+g_gen+L"\\Speech\\Voices\\"+g_voice
                      +L".SpeechVoice";
    if(GetFileAttributesW(probe.c_str())==INVALID_FILE_ATTRIBUTES){
        wprintf(L"no speech data at %s\nresident host tests skipped\n",
                probe.c_str());
        /* The rogue checks above needed no data and their verdict stands. */
        return g_fail?1:0;
    }
    wprintf(L"resident host: %s / %s\n",g_gen.c_str(),g_voice.c_str());
    /* A known state, so the run does not depend on this machine's settings. */
    DWORD wasInfl=setting_dword(L"Inflection",50);
    DWORD wasExpand=setting_dword(L"ExpandAbbreviations",1);
    DWORD wasCommands=setting_dword(L"AcceptCommands",0);
    std::wstring wasPhrasing=setting_string(L"Phrasing",L"fewest");
    set_dword(L"Inflection",50); set_string(L"Phrasing",L"fewest");
    set_dword(L"ExpandAbbreviations",1); set_dword(L"AcceptCommands",0);
    host_drop();

    FakeToken tok(g_root,g_gen,g_voice);
    Engine *e=new Engine; e->SetObjectToken(&tok);
    const wchar_t *LINE=L"The quick brown fox jumps over the lazy dog.";

    /* 1. The same words, cold and then warm, are the same words.  This is
     *    the whole refactor in one line: nothing about how an utterance
     *    sounds may depend on whether the engine was already running. */
    std::vector<BYTE> first, second;
    check(SUCCEEDED(say(e,LINE,&first,0))&&!first.empty(),
          "a cold utterance speaks");
    check(SUCCEEDED(say(e,LINE,&second,0)),"a warm utterance speaks");
    check(first==second,"the warm utterance is byte-identical to the cold one");
    contract_checks(e,LINE,first);

    /* Two voices in one process take turns; they never talk over each other.
     * This is the design, not a fault (Tomi, 2026-11-21, closing #57): one
     * engine at a time holds the host for its whole utterance.  The second
     * voice is set speaking while the first is mid-utterance, and the first
     * byte it gets must come after the last byte the first one got, with
     * both utterances whole and exactly as they sound alone. */
    {
        /* Long enough that the second voice asks with most of the first
         * still to come: a first write is one 15.6 ms tick of Sleep(1) away. */
        const wchar_t *LONGLINE=L"The quick brown fox jumps over the lazy dog. "
                                L"The quick brown fox jumps over the lazy dog. "
                                L"The quick brown fox jumps over the lazy dog. "
                                L"The quick brown fox jumps over the lazy dog. "
                                L"The quick brown fox jumps over the lazy dog. "
                                L"The quick brown fox jumps over the lazy dog.";
        std::vector<BYTE> alone;
        say(e,LONGLINE,&alone,0);
        Engine *other=new Engine; other->SetObjectToken(&tok);
        FakeSite a(0), b(0);
        std::atomic<LONGLONG> bAsked(0);
        std::atomic<bool> aDone(false);
        HRESULT ha=E_FAIL, hb=E_FAIL;
        std::thread ta([&]{ ha=say_script(e,LONGLINE,a); aDone=true; });
        /* Until the first is speaking -- or has finished, failed included, so
         * a voice that never writes fails the checks below, not the build. */
        while(!a.firstWrite&&!aDone)Sleep(1);
        std::thread tb([&]{
            LARGE_INTEGER now; QueryPerformanceCounter(&now); bAsked=now.QuadPart;
            hb=say_script(other,LONGLINE,b);
        });
        ta.join(); tb.join();
        check(SUCCEEDED(ha)&&SUCCEEDED(hb),"two voices on two threads both speak");
        check(bAsked<a.lastWrite,"the second voice asked to speak while the first was speaking");
        check(b.firstWrite>a.lastWrite,"the second voice waits its turn: no byte of it before the first is done");
        check(a.audio==alone&&b.audio==alone,"both utterances are whole, as they sound alone");

        /* And the one waiting may give up its place: cancelled while it
         * waits, it returns then -- not when the voice ahead finishes -- and
         * says nothing, while the voice speaking is untouched. */
        FakeSite c(0), d(0);
        std::atomic<bool> cDone(false);
        std::atomic<LONGLONG> dReturned(0);
        HRESULT hc=E_FAIL, hd=E_FAIL;
        std::thread tc([&]{ hc=say_script(e,LONGLINE,c); cDone=true; });
        while(!c.firstWrite&&!cDone)Sleep(1);
        std::thread td([&]{
            hd=say_script(other,LONGLINE,d);
            LARGE_INTEGER now; QueryPerformanceCounter(&now); dReturned=now.QuadPart;
        });
        Sleep(20);                                       /* d is waiting its turn */
        d.abortNow=true;
        td.join(); tc.join();
        check(SUCCEEDED(hd)&&d.audio.empty(),"a voice cancelled while waiting its turn says nothing");
        check(dReturned<c.lastWrite,"it gives up its place at once, not after the voice ahead finishes");
        check(SUCCEEDED(hc)&&c.audio==alone,"the voice that was speaking is untouched");
        other->Release();
    }

    /* Application volume changes PCM gain, never text or paragraph timing. */
    std::vector<BYTE> quiet, silent, full, changed;
    DWORD volumePid=GetProcessId(g_proc);
    check(SUCCEEDED(say(e,LINE,&quiet,0,25)),"quarter-volume utterance speaks");
    bool scaled=quiet.size()==first.size();
    for(size_t i=0;scaled&&i<first.size()/2;i++)
        scaled=((const short*)quiet.data())[i]==((const short*)first.data())[i]/4;
    check(scaled,"application volume scales every sample without changing duration");
    say(e,LINE,&silent,0,0);
    check(silent.size()==first.size()&&
          std::all_of(silent.begin(),silent.end(),[](BYTE b){return b==0;}),
          "zero volume keeps timing and silences every sample");
    say(e,LINE,&full,0,100);
    check(full==first,"restoring full volume restores the original PCM");
    say(e,LINE,&changed,0,100,1,0);
    bool prefix=false, tail=false;
    if(changed.size()==first.size()){
        size_t i=0;
        while(i<first.size()&&changed[i]==first[i])i++;
        prefix=i>0&&i<first.size();
        tail=std::all_of(changed.begin()+i,changed.end(),[](BYTE b){return b==0;});
    }
    check(prefix&&tail,"a volume change during speech reaches the next PCM chunk");
    check(GetProcessId(g_proc)==volumePid,"volume changes retain the resident host");

    /* The checkbox must affect the next request and be reversible. */
    const wchar_t *ABBREVIATIONS=L"Dr. Kirk carried 4kg down Main St.";
    std::vector<BYTE> expanded, spelled, expandedAgain;
    say(e,ABBREVIATIONS,&expanded,0);
    set_dword(L"ExpandAbbreviations",0);
    say(e,ABBREVIATIONS,&spelled,0);
    check(!spelled.empty()&&spelled!=expanded,"abbreviations off changes the next utterance");
    set_dword(L"ExpandAbbreviations",1);
    say(e,ABBREVIATIONS,&expandedAgain,0);
    check(expandedAgain==expanded,"abbreviations on restores the original utterance");
    /* The same change from the settings file, which is where the settings
     * program writes now: it has to reach the next utterance as surely as
     * the registry write did, and the file going away is a change too. */
    {
        SettingsTable off; SettingValue v; v.isString=false; v.number=0;
        off.push_back(std::make_pair(std::wstring(L"ExpandAbbreviations"),v));
        check(settings_write_file(settings_user_path(),off),"the settings file can be written");
        std::vector<BYTE> fromFile; say(e,ABBREVIATIONS,&fromFile,0);
        check(fromFile==spelled,"abbreviations off from the settings file changes the next utterance");
        DeleteFileW(settings_user_path().c_str());
        std::vector<BYTE> fileGone; say(e,ABBREVIATIONS,&fileGone,0);
        check(fileGone==expanded,"removing the settings file returns to the registry setting");
    }

    std::vector<BYTE> numbers, numberReference;
    set_string(L"NumberStyle",L"off");
    say(e,L"There are 1,234,567 people.",&numberReference,0);
    set_string(L"NumberStyle",L"fix");
    say(e,L"There are 1234567 people.",&numbers,0);
    check(numbers==numberReference,"fixed numbers reach the shared native rules");
    set_string(L"NumberStyle",L"off");
    say(e,L"There are twelve people.",&numberReference,0);
    set_string(L"NumberStyle",L"words");
    say(e,L"There are 12 people.",&numbers,0);
    check(numbers==numberReference,"words number style applies on the next request");
    set_string(L"NumberStyle",L"fix");

    /* 2. Inflection comes back.  Channel state outlives the utterance now,
     *    so returning the slider to the middle has to be *said*; the proof
     *    is that it then sounds like a host that never moved it. */
    set_dword(L"Inflection",70);
    std::vector<BYTE> raised; say(e,LINE,&raised,0);
    /* Whether a raised pmod is audible is the voice's business -- Lion's
     * Alex ignores it outright, where every mtk3 voice honours it -- so it
     * is reported and not required. */
    printf("  --    raising inflection %s this voice\n",
           raised!=first?"changes":"does nothing on");
    set_dword(L"Inflection",50);
    std::vector<BYTE> restored; say(e,LINE,&restored,0);
    check(restored==first,"returning inflection to the middle restores it");
    std::vector<BYTE> afterRestore; say(e,LINE,&afterRestore,0);
    check(afterRestore==first,"and the utterance after that is unchanged too");

    /* 3. A settings change reaches a host that read its environment once.
     *
     * The respawn is the thing being tested and is checked everywhere.  That
     * it also *sounds* different is only checkable from MacinTalk 3.4 on:
     * 3.4 is the release that gained tunable parameters at all, and Tiger's
     * signature Fred is 3.3, where Boundaries.SilThreshold is a name nothing
     * reads.  Measured, not assumed -- the same text is byte-identical
     * across all four phrasing settings on Tiger and differs on Leopard.
     * The pauses text is deliberately full of clause boundaries, because a
     * threshold on boundaries cannot show itself in a sentence without any. */
    const wchar_t *PAUSES=L"The fox jumped over the dog. Later it ran again.";
    const wchar_t *SETTINGS[]={L"fewest",L"fewer",L"more",L"most"};
    std::vector<BYTE> pcm[4]; DWORD pids[4];
    for(int i=0;i<4;i++){
        set_string(L"Phrasing",SETTINGS[i]);
        say(e,PAUSES,&pcm[i],0);
        pids[i]=GetProcessId(g_proc);
    }
    /* Four settings, four different processes.  The engine is already at
     * "fewest" when the loop starts, so that first pass rightly reuses the
     * host it has -- and the three after it must each have replaced it, or
     * the setting is being read and then thrown away. */
    bool differs=false, fresh=true;
    for(int i=1;i<4;i++){
        if(pcm[i]!=pcm[i-1])differs=true;
        for(int j=0;j<i;j++) if(pids[i]==pids[j]) fresh=false;
    }
    check(fresh,"each phrasing setting gets a host started with it");
    /* All four settings are compared rather than the two ends, because the
     * ends are not the extremes: measured, "fewest" and "most" render the
     * same bytes on Leopard while "more" differs from both.  The threshold
     * moves a boundary decision, not a dial. */
    if(g_gen==L"tiger")
        printf("  --    the 3.3 engine has no tunable parameters, and the "
               "four settings %s here\n",differs?"DIFFERED":"agreed");
    else
        check(differs,"the phrasing settings do not all sound alike");
    set_string(L"Phrasing",L"fewest");

    /* 4. An interruption leaves an engine that still speaks -- and one that
     *    still speaks *the same way*, which is where a half-killed host or
     *    a leftover channel would show up. */
    std::vector<BYTE> cut;
    HRESULT hr=say(e,L"This is a deliberately long paragraph, the kind a "
                     L"listener interrupts long before it has finished "
                     L"saying itself, twice over for good measure.",&cut,4096);
    check(SUCCEEDED(hr),"an interrupted utterance still returns success");
    check(cut.size()<first.size()*4,"an interrupted utterance stops early");
    std::vector<BYTE> after; say(e,LINE,&after,0);
    check(after==first,"the utterance after an interruption is unharmed");

    e->Release();
    host_drop();
    /* Put the machine back: these are the user's live SAPI settings, not
     * the test's, and a build gate that quietly turns off somebody's
     * embedded commands has broken more than it checked. */
    set_dword(L"Inflection",wasInfl);
    set_dword(L"ExpandAbbreviations",wasExpand);
    set_dword(L"AcceptCommands",wasCommands);
    set_string(L"Phrasing",wasPhrasing.c_str());
    printf(g_fail?"\n%d check(s) FAILED\n":"\nall resident host checks pass\n",
           g_fail);
    return g_fail?1:0;
}
