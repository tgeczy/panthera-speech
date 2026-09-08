/* SAPI COM adapter. Runtime/process ownership, typed settings, diagnostics
 * and lexical preparation live in separately compiled modules. Speak keeps
 * each fragment list whole so host scheduling and Alex's breaths are preserved.
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
#include <cstdio>
#include "runtime.h"
#include "settings.h"
#include "diagnostics.h"
#include "text.h"

using namespace panthera_sapi;
static long g_objects;
static const CLSID CLSID_Panthera = {0xc1f7fc55,0x3512,0x4f5d,{0xa6,0xeb,0xf5,0x32,0x20,0xbe,0x46,0x93}};
static const unsigned REQ_MAGIC_STREAM = 0x54475234, RSP_MAGIC = 0x54475253;
static const GUID PantheraWaveFormatEx = {0xc31adbae,0x527f,0x4ff5,{0xa2,0x30,0xf6,0x2b,0xb6,0x1f,0xf7,0x0c}};

static std::wstring token_string(ISpObjectToken *t, const wchar_t *name) {
    wchar_t *v=0; std::wstring r;
    if(t && SUCCEEDED(t->GetStringValue(name,&v)) && v) { r=v; CoTaskMemFree(v); }
    return r;
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
    /* **A voice whose data is not there must fail here, not go quiet later.**
     *
     * Each token carries a DataPath written once, at registration.  Move the
     * folder afterwards -- by hand, or with an installer -- and every token
     * still names the old one.  Until this check the engine took the voice
     * anyway, accepted the text, rendered nothing and returned, so all
     * ninety-six voices stayed in every program's list and every one of them
     * was silent.  Measured from Tomi's sign-in screen: 24 utterances, each
     * returning its bookmark in 21-23 ms flat whatever the words were, where
     * a working voice on the same screen took 216 to 2164 ms.  A constant is
     * not slow rendering, it is no rendering, and nothing said so.
     *
     * Failing the token is the honest answer: the caller is choosing a voice
     * that cannot speak, and a screen reader told "no" falls back to one that
     * can.  Silence is the one failure a screen reader cannot recover from.
     *
     * Only the engine binary is checked, and only for existence.  This runs
     * on every voice selection, so it may not be expensive, and a tree that
     * is present but broken is the host's business to report -- not a reason
     * to make choosing the voice impossible. */
    STDMETHODIMP SetObjectToken(ISpObjectToken *t){
        if(!t)return E_INVALIDARG;
        if(token)return E_UNEXPECTED;
        token=t;t->AddRef();
        std::wstring root=token_string(token,L"DataPath"),
                     gen=token_string(token,L"Generation");
        if(!root.empty()&&!gen.empty()){
            std::wstring mt=root+L"\\"+gen+
                L"\\Speech\\Synthesizers\\MacinTalk.SpeechSynthesizer"
                L"\\Contents\\MacOS\\MacinTalk";
            if(GetFileAttributesW(mt.c_str())==INVALID_FILE_ATTRIBUTES){
                logline(L"voice refused: no engine at %.160s",mt.c_str());
                token->Release();token=0;
                /* Not an SPERR: the SAPI-specific codes for "this token
                 * names something that is not there" are not all declared by
                 * every SDK, and a build that fails on somebody else's
                 * machine helps nobody.  0x80070002 says "the system cannot
                 * find the file specified", which is both true and legible
                 * wherever it surfaces. */
                return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
            }
        }
        return S_OK;
    }
    STDMETHODIMP GetObjectToken(ISpObjectToken **t){if(!t)return E_POINTER;*t=token;if(token)token->AddRef();return token?S_OK:S_FALSE;}
    STDMETHODIMP GetOutputFormat(const GUID*,const WAVEFORMATEX*,GUID *id,WAVEFORMATEX **wf){
        if(!id||!wf)return E_POINTER; *id=PantheraWaveFormatEx;
        WAVEFORMATEX f={WAVE_FORMAT_PCM,1,22050,44100,2,16,0};
        *wf=(WAVEFORMATEX*)CoTaskMemAlloc(sizeof f);if(!*wf)return E_OUTOFMEMORY;**wf=f;return S_OK;
    }
    STDMETHODIMP Speak(DWORD,REFGUID,const WAVEFORMATEX*,const SPVTEXTFRAG *frags,ISpTTSEngineSite *site){
        /* A COM method must never let an exception out: SAPI has no handler
         * for one and the client application dies of it.  That is not
         * hypothetical -- a desynced pipe once produced a frame count in the
         * billions, the resize threw bad_alloc, and a game crashed.  The
         * count is now clamped and the stream defended, but the guarantee
         * belongs at the boundary, whatever the cause. */
        try {
            return speakInner(frags,site);
        } catch(...) {
            if(g_lockReady){
                CsLock lock(&g_hostLock);
                host_drop();       /* mid-protocol unwind = desynced pipe */
            }
            return E_FAIL;
        }
    }
    HRESULT speakInner(const SPVTEXTFRAG *frags,ISpTTSEngineSite *site){
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
        bool expand=setting_dword(L"ExpandAbbreviations",1)!=0;
        std::wstring gen=token_string(token,L"Generation");
        text=prepare_text(text,setting_dword(L"AcceptCommands",0)!=0,expand,gen);
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
        std::wstring root=token_string(token,L"DataPath"), voice=token_string(token,L"EngineVoiceName");
        /* New registrations expose a generation-qualified VoiceName because
         * some clients incorrectly use it as the token identity.  Old tokens
         * and the resident test only have VoiceName, so retain that fallback. */
        if(voice.empty()) voice=token_string(token,L"VoiceName");
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
        std::wstring numberStyle=setting_string(L"NumberStyle",L"fix");
        unsigned request=REQ_MAGIC_STREAM;
        unsigned flags=numberStyle==L"fix"?2u:numberStyle==L"words"?4u:0u;
        CsLock lock(&g_hostLock);
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
            /* Response reads wait rather than block: exact_wait watches the
             * abort flag, the host's death and a no-progress deadline, so a
             * wedged host costs one failed utterance instead of the session.
             * An abort while waiting takes the same door as the mid-stream
             * one below -- kill the host, boot the replacement. */
            if(ok){
                ReadWait r=exact_wait(g_out,&magic,4,site);
                if(r==RW_OK)r=exact_wait(g_out,&status,4,site);
                if(r==RW_ABORT){
                    aborted=true;host_drop();
                    host_ensure(tree,mt,sd,vd,params,noAbbrev);
                }else if(r!=RW_OK||magic!=RSP_MAGIC)ok=false;
            }
            std::vector<BYTE> audio;
            /* Not `while(ok&&!status)`: the host answers every request with
             * a terminator, an errored one included, and a resident pipe
             * that skips those four bytes is desynced for good. */
            while(ok&&!aborted){
                unsigned frames=0;
                ReadWait r=exact_wait(g_out,&frames,4,site);
                if(r==RW_ABORT){
                    aborted=true;host_drop();
                    host_ensure(tree,mt,sd,vd,params,noAbbrev);
                    break;
                }
                if(r!=RW_OK){ok=false;break;}
                if(!frames)break;
                if(frames>MAX_CHUNK_FRAMES){
                    /* Two hundred times the host's own chunk cap is not a
                     * chunk, it is a desynced stream read as one; see the
                     * constant.  The pipe is unusable from here. */
                    logline(L"desync: frame count %u refused",frames);
                    ok=false;break;
                }
                unsigned bytes=frames*2; audio.resize(bytes);
                r=exact_wait(g_out,audio.data(),bytes,site);
                if(r==RW_ABORT){
                    aborted=true;host_drop();
                    host_ensure(tree,mt,sd,vd,params,noAbbrev);
                    break;
                }
                if(r!=RW_OK){ok=false;break;}
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
                /* SAPI owns the application slider; the engine applies its
                 * gain. Read it per chunk so a change during a paragraph
                 * does not restart synthesis or disturb Alex's breaths.
                 * Scaling the final PCM also preserves embedded volm commands. */
                USHORT volume=100;
                if(FAILED(site->GetVolume(&volume))){ok=false;break;}
                if(volume>100)volume=100;
                if(volume!=100){
                    short *samples=(short*)audio.data();
                    for(unsigned i=0;i<frames;i++)
                        samples[i]=(short)((int)samples[i]*volume/100);
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
        /* The measurements are what settled every bug this log has ever
         * settled.  The words are only ever asked for by name. */
        if(diagLevel()>=2)
            logline(L"speak: chars=%u marks=%u bytes=%u ok=%d status=%d "
                    L"aborted=%d voice=%.24s text=\"%.40s\"",
                    (unsigned)text.size(),(unsigned)marks.size(),(unsigned)total,
                    ok?1:0,status,aborted?1:0,voice.c_str(),text.c_str());
        else
            logline(L"speak: chars=%u marks=%u bytes=%u ok=%d status=%d "
                    L"aborted=%d voice=%.24s",
                    (unsigned)text.size(),(unsigned)marks.size(),(unsigned)total,
                    ok?1:0,status,aborted?1:0,voice.c_str());
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
