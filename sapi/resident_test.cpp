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
    std::vector<BYTE> audio; ULONG marks; DWORD abortAfter;
    FakeSite(DWORD abortAfterBytes):refs(1),marks(0),abortAfter(abortAfterBytes){}
    STDMETHODIMP QueryInterface(REFIID,void**p){*p=this;AddRef();return S_OK;}
    STDMETHODIMP_(ULONG) AddRef(){return ++refs;}
    STDMETHODIMP_(ULONG) Release(){return --refs;}
    STDMETHODIMP AddEvents(const SPEVENT*,ULONG n){marks+=n;return S_OK;}
    STDMETHODIMP GetEventInterest(ULONGLONG *i){*i=SPEI_TTS_BOOKMARK;return S_OK;}
    STDMETHODIMP_(DWORD) GetActions(){
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

int wmain(int argc, wchar_t **argv) {
    InitializeCriticalSection(&g_hostLock); g_lockReady=true;
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
        return 0;
    }
    wprintf(L"resident host: %s / %s\n",g_gen.c_str(),g_voice.c_str());
    /* A known state, so the run does not depend on this machine's settings. */
    DWORD wasInfl=setting_dword(L"Inflection",50);
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
    set_dword(L"Inflection",wasInfl);
    set_string(L"Phrasing",wasPhrasing.c_str());
    printf(g_fail?"\n%d check(s) FAILED\n":"\nall resident host checks pass\n",
           g_fail);
    return g_fail?1:0;
}
