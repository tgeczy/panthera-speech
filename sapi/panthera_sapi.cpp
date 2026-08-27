/* Panthera's voices as a SAPI 5 engine, 32- and 64-bit.
 *
 * The engine process stays resident.  Until now every utterance spawned its
 * own panthera_host.exe and killed it at the end, which cost 25-30 ms of
 * cold start each time -- 36 ms to first sound for Fred and 41 for Alex,
 * against 11 warm -- and, less obviously, made the process itself the
 * answer to three awkward questions: cancel was TerminateProcess, a
 * settings change took effect because the next spawn read the environment
 * afresh, and an embedded command could not outlive the channel it was
 * sent to because neither outlived the utterance.
 *
 * Keeping the host re-opens all three, and each is answered where it
 * arises below.  Two of the answers are the NVDA driver's, which has been
 * resident since it was written; the third is not.  An interruption still
 * kills the host, because Panthera's cold start turned out to be cheaper
 * than its engine's own graceful cancel -- measured, and argued at the
 * abort path.
 *
 * What it buys, measured on Tomi's machine, request to first sound:
 *
 *     Tiger  Fred    19 ms cold -> 11 warm      Leopard Fred  29 -> 11
 *     Snow   Fred    26 ms cold -> 10 warm      Lion    Alex  47 -> 21
 *
 * An interrupted utterance costs exactly what it did before.  Everything
 * else got two to three times faster to first sound.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sapi.h>
#include <sapiddk.h>
#include <olectl.h>
#include <string>
#include <vector>
#include <cmath>
#include <cwctype>
#include <regex>
#include <cstdio>
#include <cstdarg>

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

/* The black box: one line per utterance in %TEMP%\panthera_sapi.log.
 *
 * outSPOKEN's afternoon of four COM-layer bugs was settled by exactly this
 * file and nothing else -- three theories died of it, and the log convicted
 * in one reading.  A resident host has more state to be wrong about than a
 * fresh process ever did, so it earns its place here before anyone needs
 * it.  Cheap enough to leave on. */
static void logline(const wchar_t *fmt, ...) {
    wchar_t path[MAX_PATH];
    DWORD n=GetEnvironmentVariableW(L"TEMP",path,MAX_PATH);
    if(!n||n>=MAX_PATH-24)return;
    lstrcatW(path,L"\\panthera_sapi.log");
    HANDLE f=CreateFileW(path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,
                         0,OPEN_ALWAYS,0,0);
    if(f==INVALID_HANDLE_VALUE)return;
    wchar_t line[512];
    va_list ap; va_start(ap,fmt);
    int len=_vsnwprintf_s(line,512,_TRUNCATE,fmt,ap);
    va_end(ap);
    if(len<0)len=511;
    char out[1100]; int m=WideCharToMultiByte(CP_UTF8,0,line,len,out,1060,0,0);
    SYSTEMTIME st; GetLocalTime(&st);
    char stamp[32];
    int sn=sprintf_s(stamp,32,"%02d:%02d:%02d.%03d ",st.wHour,st.wMinute,
                     st.wSecond,st.wMilliseconds);
    DWORD w;
    WriteFile(f,stamp,sn,&w,0);
    WriteFile(f,out,m,&w,0);
    WriteFile(f,"\r\n",2,&w,0);
    CloseHandle(f);
}

/* ---- the resident host ------------------------------------------------ */

static CRITICAL_SECTION g_hostLock;
static bool g_lockReady;
static HANDLE g_proc, g_in, g_out;
/* What the live host was started with.
 *
 * The engine reads TIGER_PARAMS and TIGER_NO_ABBREV once, in main, before
 * serve mode begins (tiger_host.c ~347) -- they are inherited at spawn and
 * a resident child cannot be told they changed.  Left alone, that makes the
 * Phrasing and Expand-abbreviations controls quietly stop working, which is
 * the one failure this project keeps meeting: a setting that does nothing.
 * So they are remembered here and a difference respawns the host, exactly
 * as pantheradriver.py's _restartHost does for the same two settings.  The
 * tree is here for the same reason -- a generation is a different engine
 * with different data, not a different argument. */
static std::wstring g_hostTree, g_hostParams, g_hostAbbrev;
/* Inflection is an embedded [[pmod]] command, and once the channel outlives
 * the utterance, so does the command.  Sending nothing at the default
 * therefore does not mean "the default", it means "whatever was set last".
 * The driver learned that from a user whose volume went to zero and stayed
 * there -- its own comment calls it the worst failure it has had -- so the
 * return to the default is *said*, once, and then not again. */
static bool g_inflSent;

static void host_drop() {
    if(g_proc){TerminateProcess(g_proc,0);CloseHandle(g_proc);g_proc=0;}
    if(g_in){CloseHandle(g_in);g_in=0;}
    if(g_out){CloseHandle(g_out);g_out=0;}
    g_hostTree.clear();g_hostParams.clear();g_hostAbbrev.clear();
    g_inflSent=false;            /* a new channel starts at the default */
}
static bool host_alive() {
    if(!g_proc)return false;
    DWORD code=0;
    if(!GetExitCodeProcess(g_proc,&code)||code!=STILL_ACTIVE){host_drop();return false;}
    return true;
}
/* The host's diagnostics need somewhere that cannot fill up.
 *
 * The NVDA driver hands the child a pipe and spends a thread draining it; a
 * SAPI DLL has no thread to spare, and an undrained pipe stops the writer
 * dead the moment it fills.  For a process that lived one utterance that
 * was unreachable.  For one that lives all session it is a wedge waiting to
 * happen, and it would present as speech stopping for good.  A file never
 * blocks.  Named per client process, because a 32-bit and a 64-bit SAPI
 * client can be running at the same time and each has its own host. */
static HANDLE host_stderr() {
    wchar_t path[MAX_PATH];
    DWORD n=GetEnvironmentVariableW(L"TEMP",path,MAX_PATH);
    if(!n||n>=MAX_PATH-48)return INVALID_HANDLE_VALUE;
    wchar_t leaf[48];
    swprintf_s(leaf,48,L"\\panthera_sapi_host-%u.log",
               (unsigned)GetCurrentProcessId());
    lstrcatW(path,leaf);
    SECURITY_ATTRIBUTES sa={sizeof(sa),0,TRUE};
    return CreateFileW(path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,
                       &sa,OPEN_ALWAYS,0,0);
}
static bool host_ensure(const std::wstring &tree, const std::wstring &mt,
                        const std::wstring &sd, const std::wstring &vd,
                        const std::wstring &params, const std::wstring &abbrev) {
    if(host_alive()&&tree==g_hostTree&&params==g_hostParams&&abbrev==g_hostAbbrev)
        return true;
    host_drop();
    SetEnvironmentVariableW(L"TIGER_PARAMS",params.empty()?NULL:params.c_str());
    SetEnvironmentVariableW(L"TIGER_NO_ABBREV",abbrev.empty()?NULL:abbrev.c_str());
    std::wstring cmd=L"\""+module_dir()+L"\\panthera_host.exe\" --serve \""+mt+
                     L"\" \""+sd+L"\" \""+vd+L"\"";
    SECURITY_ATTRIBUTES sa={sizeof(sa),0,TRUE}; HANDLE inR,inW,outR,outW;
    if(!CreatePipe(&inR,&inW,&sa,0)||!CreatePipe(&outR,&outW,&sa,0))return false;
    SetHandleInformation(inW,HANDLE_FLAG_INHERIT,0);SetHandleInformation(outR,HANDLE_FLAG_INHERIT,0);
    HANDLE err=host_stderr();
    STARTUPINFOW si={sizeof(si)};si.dwFlags=STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW;si.wShowWindow=SW_HIDE;
    si.hStdInput=inR;si.hStdOutput=outW;
    si.hStdError=err!=INVALID_HANDLE_VALUE?err:GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION pi={}; std::vector<wchar_t> mutableCmd(cmd.begin(),cmd.end());mutableCmd.push_back(0);
    BOOL made=CreateProcessW(0,mutableCmd.data(),0,0,TRUE,CREATE_NO_WINDOW,0,module_dir().c_str(),&si,&pi);
    CloseHandle(inR);CloseHandle(outW);
    if(err!=INVALID_HANDLE_VALUE)CloseHandle(err);
    if(!made){CloseHandle(inW);CloseHandle(outR);return false;}
    CloseHandle(pi.hThread);
    g_proc=pi.hProcess;g_in=inW;g_out=outR;
    g_hostTree=tree;g_hostParams=params;g_hostAbbrev=abbrev;
    logline(L"host started: pid=%u params=\"%.40s\" abbrev=%s tree=\"%.80s\"",
            (unsigned)pi.dwProcessId,params.c_str(),
            abbrev.empty()?L"expand":L"OFF",tree.c_str());
    return true;
}

/* The user settings the NVDA driver has and SAPI users were living without,
 * kept in HKCU by the settings program and read afresh on every Speak, so a
 * change takes effect on the very next thing spoken.  The two the *engine*
 * reads rather than this code -- phrasing and abbreviations -- take effect
 * by replacing the engine; see host_ensure. */
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

/* The abbreviation rules, ported from pantheraabbrev.py -- that module and
 * its tests are the spec; nothing here decides anything the Python side has
 * not measured.  Authored without lookbehind on both sides, because
 * std::wregex has none.
 *
 * `regex_replace` cannot compute a replacement, so the spaced-letters
 * rewrites walk matches by hand. */
static void spell_out(std::wstring &t, const std::wregex &re, bool upper) {
    std::wstring out; out.reserve(t.size()+8);
    auto it=std::wsregex_iterator(t.begin(),t.end(),re), end=std::wsregex_iterator();
    size_t last=0;
    for(;it!=end;++it){
        out.append(t,last,it->position(1)-last);
        const std::wstring tok=it->str(1);
        for(size_t j=0;j<tok.size();++j){
            if(j)out.push_back(L' ');
            out.push_back(upper?towupper(tok[j]):tok[j]);
        }
        last=it->position(1)+it->length(1);
    }
    out.append(t,last,std::wstring::npos);
    t.swap(out);
}

/* The engine's measured wrong guesses, settled whichever way the setting
 * points: "<proper noun> Dr." read as a street, and "X's" after a
 * camel-case split read as the roman numeral ("SpaceX's" was
 * "space ten's").  The Doctor rewrite only with expansion on -- with it
 * off, despelling reads "Dr." as letters and writing "Doctor" would be an
 * expansion the user declined. */
static void disambiguate(std::wstring &t, bool expand) {
    static const std::wregex ex(L"\\bX(['\x2019]s)\\b");
    t=std::regex_replace(t,ex,L"ex$1");
    if(expand){
        static const std::wregex doc(L"\\bDr\\.(\\s+)(?=[A-Z][a-z])");
        t=std::regex_replace(t,doc,L"Doctor$1");
    }
}

/* "Expand abbreviations" off: the engine's own lexicon expands DR, Dr.,
 * St., and on 10.7 digit-adjacent units, none of which TIGER_NO_ABBREV
 * reaches -- so the abbreviation-shaped forms despell in the text.
 * Case-sensitive exactly as the Python side: lowercase prose ("vs",
 * "etc", "dr") is never touched. */
static void despell(std::wstring &t) {
    static const std::wregex acronyms(
        L"\\b(CT|DR|ETC|FT|JR|MRS?|RD|SR|ST|VS)\\b");
    static const std::wregex titles(
        L"\\b(Blvd|Capt|Prof|Mrs|Ave|Gen|Gov|Rep|Sen|Ct|Dr|Ft|Jr|Lt|Mr|Ms"
        L"|Rd|Sr|St)\\b");
    static const std::wregex units(L"\\b(\\d+) ?(mm|cm|km|kg|g|m)\\b");
    static const std::wregex roman(
        L"\\b(?=[MDCLXVI]{2,}\\b)"
        L"(M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\\b");
    spell_out(t,acronyms,false);
    spell_out(t,titles,true);
    {   /* units keep their number: "4mm" -> "4 M M" */
        std::wstring out; out.reserve(t.size()+8);
        auto it=std::wsregex_iterator(t.begin(),t.end(),units), end=std::wsregex_iterator();
        size_t last=0;
        for(;it!=end;++it){
            out.append(t,last,it->position(0)-last);
            out.append(it->str(1)); out.push_back(L' ');
            const std::wstring u=it->str(2);
            for(size_t j=0;j<u.size();++j){
                if(j)out.push_back(L' ');
                out.push_back(towupper(u[j]));
            }
            last=it->position(0)+it->length(0);
        }
        out.append(t,last,std::wstring::npos);
        t.swap(out);
    }
    {   /* MIX is M+IX, 1009, and the one English word the strict pattern
         * claims; everything else spaced out is the setting keeping its
         * word.  See the Python module for the whole argument. */
        std::wstring out; out.reserve(t.size()+8);
        auto it=std::wsregex_iterator(t.begin(),t.end(),roman), end=std::wsregex_iterator();
        size_t last=0;
        for(;it!=end;++it){
            out.append(t,last,it->position(1)-last);
            const std::wstring tok=it->str(1);
            if(tok==L"MIX")out.append(tok);
            else for(size_t j=0;j<tok.size();++j){
                if(j)out.push_back(L' ');
                out.push_back(tok[j]);
            }
            last=it->position(1)+it->length(1);
        }
        out.append(t,last,std::wstring::npos);
        t.swap(out);
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
        /* JAWS sends each word as its own SPVA_Speak fragment with an
         * SPVA_Bookmark between every pair, and a bookmark fragment's
         * text is its name.  Appending blindly reads the names aloud;
         * appending without a separator runs the words together.  Only
         * text meant to be heard goes in, with a space restored at the
         * seam when neither side brought one.
         *
         * The bookmarks themselves are the pacing contract for clients
         * that index -- NVDA's SAPI driver interleaves them with the text
         * and waits for TTS_BOOKMARK events to advance; an engine that
         * never posts them is one whose indexes never arrive, and the
         * scheduler eventually purges what it thinks is a stuck
         * utterance.  Learned in the outSPOKEN sibling, minutes after
         * its first real client. */
        struct Mark { std::wstring name; size_t chars; };
        std::vector<Mark> marks;
        for(auto f=frags;f;f=f->pNext){
            if(f->State.eAction==SPVA_Bookmark){
                if(f->pTextStart&&f->ulTextLen){
                    Mark m; m.name.assign(f->pTextStart,f->ulTextLen);
                    m.chars=text.size(); marks.push_back(m);
                }
                continue;
            }
            switch(f->State.eAction){
            case SPVA_Speak: case SPVA_SpellOut: case SPVA_Pronounce: break;
            default: continue;
            }
            if(!f->pTextStart||!f->ulTextLen)continue;
            if(!text.empty()&&!iswspace(text.back())&&!iswspace(f->pTextStart[0]))
                text.push_back(L' ');
            text.append(f->pTextStart,f->ulTextLen);
        }
        /* Bookmarks are answered even when there is nothing to say.  A
         * fragment list of nothing but marks still carries indexes a client
         * is waiting on, and an index that never arrives is an utterance
         * the scheduler eventually purges -- the same contract, in the case
         * where it is cheapest to forget. */
        if(text.empty()&&marks.empty())return S_OK;
        size_t textChars=text.size();
        if(!setting_dword(L"AcceptCommands",0))
            strip_commands(text);
        if(setting_string(L"NumberStyle",L"fix")==L"fix")
            fix_long_numbers(text);
        /* Same rules, same order as the NVDA driver: the wrong-guess
         * rewrites whichever way the abbreviations setting points, then
         * despelling only when it is off. */
        bool expand=setting_dword(L"ExpandAbbreviations",1)!=0;
        disambiguate(text,expand);
        if(!expand)
            despell(text);
        /* Phrasing rides the same TIGER_PARAMS the NVDA host reads, and
         * abbreviations the same TIGER_NO_ABBREV -- but the host reads its
         * environment once, at startup, so with a resident engine these are
         * not settings any more: they are part of *which host*.  Worked out
         * here, compared in host_ensure, and a change respawns rather than
         * being silently ignored. */
        std::wstring params, noAbbrev=expand?L"":L"1";
        {
            std::wstring ph=setting_string(L"Phrasing",L"fewest");
            const wchar_t *thr = ph==L"fewest"?L"-8":ph==L"fewer"?L"-4":
                                 ph==L"more"?L"0":ph==L"most"?L"5":NULL;
            if(thr)params=std::wstring(L"Boundaries.SilThreshold=")+thr;
        }
        std::wstring root=token_string(token,L"DataPath"), gen=token_string(token,L"Generation"), voice=token_string(token,L"VoiceName");
        std::wstring tree=root+L"\\"+gen, mt=tree+L"\\Speech\\Synthesizers\\MacinTalk.SpeechSynthesizer\\Contents\\MacOS\\MacinTalk";
        std::wstring sd=tree+L"\\SpeechDictionary.framework\\Versions\\A\\SpeechDictionary", vd=tree+L"\\Speech\\Voices";
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
        unsigned request=REQ_MAGIC_STREAM,flags=0;
        EnterCriticalSection(&g_hostLock);
        bool ok=true;int status=0;bool aborted=false;
        unsigned long long total=0;
        if(!text.empty()){
            ok=host_ensure(tree,mt,sd,vd,params,noAbbrev);
            /* Inflection, decided after the host is settled: a respawn is a
             * fresh channel sitting at the engine's own default, and
             * host_drop clears the latch to say so.  pmod is inflection
             * times two, exactly as the NVDA driver sends it, and nothing
             * at all goes out at the halfway default so an untouched
             * utterance stays byte-for-byte what Apple ships.  Prepended
             * after the stripping on purpose. */
            {
                DWORD infl=setting_dword(L"Inflection",50);
                if(infl>100)infl=100;
                if(infl!=50){
                    wchar_t cmd[32];
                    swprintf_s(cmd,32,L"[[pmod %u]]",infl*2);
                    text.insert(0,cmd);g_inflSent=true;
                } else if(g_inflSent){
                    /* Coming back to the middle is a new host, not a command.
                     *
                     * The obvious move is to say "[[pmod 100]]" once, which
                     * is what the NVDA driver does, and on the three older
                     * generations it is exactly right.  On Lion's Alex it is
                     * worse than doing nothing: measured, Alex ignores a
                     * raised pmod entirely and then *accepts* the 100 -- so
                     * the command sent to undo an inflection that never
                     * happened is the only thing that ever changes his
                     * voice, and it stays changed.  100 is simply not his
                     * default; pmod is per-voice.
                     *
                     * A channel that has just been opened is at whatever
                     * this voice's default is, whatever that is, on every
                     * engine.  Restarting costs one cold start, and only on
                     * the utterance where the listener puts the slider back. */
                    host_drop();
                    ok=ok&&host_ensure(tree,mt,sd,vd,params,noAbbrev);
                }
            }
            std::string v=utf8(voice), u=utf8(text);
            unsigned nv=(unsigned)v.size(),nt=(unsigned)u.size();
            ok=ok&&exact(g_in,&request,4,true)&&exact(g_in,&rate,4,true)&&exact(g_in,&pitch,4,true)&&exact(g_in,&flags,4,true)&&exact(g_in,&nv,4,true)&&exact(g_in,&nt,4,true)&&exact(g_in,(void*)v.data(),nv,true)&&exact(g_in,(void*)u.data(),nt,true);
            unsigned magic=0;status=-1;
            ok=ok&&exact(g_out,&magic,4,false)&&exact(g_out,&status,4,false)&&magic==RSP_MAGIC;
            std::vector<BYTE> audio;
            /* Not `while(ok&&!status)`: the host answers every request with
             * a terminator, an errored one included, and a resident pipe
             * that skips those four bytes is desynced for good. */
            while(ok){
                unsigned frames=0;
                if(!exact(g_out,&frames,4,false)){ok=false;break;}
                if(!frames)break;
                unsigned bytes=frames*2; audio.resize(bytes);
                if(!exact(g_out,audio.data(),bytes,false)){ok=false;break;}
                if(site->GetActions()&SPVES_ABORT){
                    /* An interruption still kills the host, and that is a
                     * measurement rather than an oversight.
                     *
                     * The engine has a graceful cancel -- a named event it
                     * polls mid-render, which the NVDA driver uses -- and
                     * taking it here was the plan until it was timed.  The
                     * host answers a cancel by stopping the engine and
                     * waiting for its pacer to settle, and that costs a flat
                     * ~47 ms whatever is in the pipe.  Panthera's whole cold
                     * start is 21-52 ms.  So asking politely and then
                     * speaking again came to 58-68 ms end to end, against
                     * 21-52 for killing it, on every generation.
                     *
                     * outSPOKEN went the other way on the same question and
                     * was also right: 158 ms of Python, driver, ROM and
                     * emulator meant it could not afford to start again.  A
                     * host cheap enough to throw away is a different
                     * problem, and this is the one place the two engines
                     * deliberately disagree.
                     *
                     * The replacement starts here rather than at the next
                     * Speak, so whatever gap the listener leaves is spent
                     * booting.  pantheradriver.py reaches for the same trick
                     * under the name of a standby host. */
                    aborted=true;
                    host_drop();
                    host_ensure(tree,mt,sd,vd,params,noAbbrev);
                    break;         /* g_out belongs to the replacement now */
                }
                ULONG wrote=0;if(FAILED(site->Write(audio.data(),bytes,&wrote))){ok=false;break;}
                total+=bytes;
            }
        }
        if(ok&&status==0&&!aborted){
            /* One TTS_BOOKMARK event per bookmark fragment, offsets as
             * proportional estimates by character position -- the audio
             * streamed as one utterance, and NVDA schedules the index at
             * its own player position when the event arrives. */
            for(size_t i=0;i<marks.size();i++){
                SPEVENT ev;memset(&ev,0,sizeof ev);
                ev.eEventId=SPEI_TTS_BOOKMARK;
                ev.elParamType=SPET_LPARAM_IS_STRING;
                ev.ullAudioStreamOffset=textChars?
                    (unsigned long long)((double)marks[i].chars/(double)textChars*(double)total):0;
                ev.wParam=(WPARAM)_wtol(marks[i].name.c_str());
                ev.lParam=(LPARAM)marks[i].name.c_str();
                site->AddEvents(&ev,1);
            }
        }
        /* A desynced pipe is never reused: whatever went wrong, the next
         * request would read this one's leftovers as its own. */
        if(!ok)host_drop();
        logline(L"speak: chars=%u marks=%u bytes=%u ok=%d status=%d aborted=%d "
                L"voice=%.24s text=\"%.40s\"",
                (unsigned)text.size(),(unsigned)marks.size(),(unsigned)total,
                ok?1:0,status,aborted?1:0,voice.c_str(),text.c_str());
        LeaveCriticalSection(&g_hostLock);
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
BOOL WINAPI DllMain(HINSTANCE h,DWORD why,LPVOID){
    if(why==DLL_PROCESS_ATTACH){g_module=h;DisableThreadLibraryCalls(h);
        if(!g_lockReady){InitializeCriticalSection(&g_hostLock);g_lockReady=true;}}
    /* The host would exit on its own when the client's write handle closed
     * -- its stdin read fails and serve mode returns.  This is the case
     * where the client did not get that far. */
    if(why==DLL_PROCESS_DETACH){if(g_proc){TerminateProcess(g_proc,0);CloseHandle(g_proc);g_proc=0;}}
    return TRUE;
}
