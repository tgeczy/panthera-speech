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

static int g_fail;
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
    std::vector<BYTE> audio; ULONG marks; DWORD abortAfter; bool abortNow;
    FakeSite(DWORD abortAfterBytes)
        :refs(1),marks(0),abortAfter(abortAfterBytes),abortNow(false){}
    STDMETHODIMP QueryInterface(REFIID,void**p){*p=this;AddRef();return S_OK;}
    STDMETHODIMP_(ULONG) AddRef(){return ++refs;}
    STDMETHODIMP_(ULONG) Release(){return --refs;}
    STDMETHODIMP AddEvents(const SPEVENT*,ULONG n){marks+=n;return S_OK;}
    STDMETHODIMP GetEventInterest(ULONGLONG *i){*i=SPEI_TTS_BOOKMARK;return S_OK;}
    STDMETHODIMP_(DWORD) GetActions(){
        if(abortNow)return SPVES_ABORT;
        return (abortAfter&&audio.size()>=abortAfter)?SPVES_ABORT:0;
    }
    STDMETHODIMP Write(const void *p,ULONG n,ULONG *w){
        audio.insert(audio.end(),(const BYTE*)p,(const BYTE*)p+n);
        if(w)*w=n; return S_OK;
    }
    STDMETHODIMP GetRate(long *r){*r=0;return S_OK;}
    STDMETHODIMP GetVolume(USHORT *v){*v=100;return S_OK;}
    STDMETHODIMP GetSkipInfo(SPVSKIPTYPE*,long*){return E_NOTIMPL;}
    STDMETHODIMP CompleteSkip(long){return E_NOTIMPL;}
};

/* ---- driving it ------------------------------------------------------- */

static std::wstring g_root, g_gen, g_voice;

static HRESULT say(Engine *e, const wchar_t *text, std::vector<BYTE> *out,
                   DWORD abortAfter) {
    FakeSite site(abortAfter);
    SPVTEXTFRAG frag; memset(&frag,0,sizeof frag);
    frag.State.eAction=SPVA_Speak;
    frag.pTextStart=text; frag.ulTextLen=(ULONG)wcslen(text);
    HRESULT hr=e->Speak(0,GUID_NULL,0,&frag,&site);
    if(out)*out=site.audio;
    return hr;
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

int wmain(int argc, wchar_t **argv) {
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
