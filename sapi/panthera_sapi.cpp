/* SAPI COM adapter. Runtime/process ownership, typed settings, diagnostics
 * and lexical preparation live in separately compiled modules. Speak keeps
 * text in one state whole -- one engine utterance -- so host scheduling and
 * Alex's breaths are preserved, and starts a new one only where the
 * application's markup changes the rate, pitch, volume or spelling.
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
#include <algorithm>
#include <cstdio>
#include "runtime.h"
#include "settings.h"
#include "diagnostics.h"
#include "text.h"

using namespace panthera_sapi;
static long g_objects;
static const CLSID CLSID_Panthera = {0xc1f7fc55,0x3512,0x4f5d,{0xa6,0xeb,0xf5,0x32,0x20,0xbe,0x46,0x93}};
static const unsigned REQ_MAGIC_STREAM = 0x54475234, REQ_MAGIC_MARKERS = 0x54475235, RSP_MAGIC = 0x54475253;
/* In a 'TGR5' stream: a sync the engine reached, then an error that ends it. */
static const unsigned STREAM_MARKER = 0x80000001u, STREAM_ERROR = 0x80000002u;
static const int MARKERS_UNAVAILABLE = -32001;
/* The syncs this engine adds to stand for bookmarks, numbered from here. */
static const unsigned SYNC_BASE = 0x7a000000u;
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
    /* **A fragment list is regrouped into runs, one host request each.**
     *
     * SAPI hands the engine its text as fragments, each with its own state:
     * the rate, pitch and volume the application's markup asked for, and
     * whether it is to be spelled.  A run is the longest stretch of
     * fragments that agree on all four, so plain text -- every fragment in
     * the same state -- is still one run, one request and one utterance,
     * and Alex keeps his breaths.  A change of state is where the
     * application asked for a change, and that is where the NVDA driver
     * flushes too (speech_pipeline.py, on its pitch, rate and volume
     * commands): one engine utterance per setting, which is the only way
     * the host can give one.
     *
     * Bookmarks keep their place in the run they came in.  A mark with words
     * on both sides becomes the engine's own [[sync]], and the host reports
     * the frame it fell on (the 'TGR5' stream), so the event goes out at its
     * own offset, ahead of the audio it belongs to.  A mark with nothing
     * spoken before it in its run goes out before the run's audio, and one
     * with nothing after it, after -- never as a sync, because a sync at the
     * very end can change Lion's closing prosody (marker_pipeline.py). */
    struct Mark { std::wstring name; size_t pos; };
    struct Run {
        long rateAdj, pitchAdj; USHORT volume; bool spell; ULONG silenceMs;
        std::wstring text; std::vector<Mark> marks;
        /* Worked out once the settings are known: the text as it will be
         * read, where each mark falls in it, and where its sentences start
         * (the first of them numbered firstSentence in the whole call). */
        std::wstring body; std::vector<size_t> at, starts; size_t firstSentence=0;
        bool same(const SPVSTATE &s,bool sp) const {
            return !silenceMs&&spell==sp&&rateAdj==s.RateAdj&&
                   pitchAdj==s.PitchAdj.MiddleAdj&&volume==s.Volume;
        }
    };
    HRESULT speakInner(const SPVTEXTFRAG *frags,ISpTTSEngineSite *site){
        if(!token||!site)return E_UNEXPECTED;
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
        std::vector<Run> runs; std::vector<Mark> pending;
        int cur=-1;
        auto open_run=[&](const SPVSTATE &s,bool sp,ULONG silence){
            Run r; r.rateAdj=s.RateAdj; r.pitchAdj=s.PitchAdj.MiddleAdj;
            r.volume=s.Volume; r.spell=sp; r.silenceMs=silence;
            r.marks.swap(pending);           /* marks waiting for a start */
            for(auto &m:r.marks)m.pos=0;
            runs.push_back(r); cur=(int)runs.size()-1;
        };
        for(auto f=frags;f;f=f->pNext){
            const SPVSTATE &st=f->State;
            if(st.eAction==SPVA_Bookmark){
                if(f->pTextStart&&f->ulTextLen){
                    Mark m; m.name.assign(f->pTextStart,f->ulTextLen);
                    if(cur<0){m.pos=0;pending.push_back(m);}
                    else{
                        Run &r=runs[cur];
                        /* After a silence, a mark is after it. */
                        m.pos=r.silenceMs?(size_t)-1:r.text.size();
                        r.marks.push_back(m);
                    }
                }
                continue;
            }
            if(st.eAction==SPVA_Silence){
                /* <silence msec="..."/>: the pause itself, in the stream. */
                open_run(st,false,st.SilenceMSecs?st.SilenceMSecs:1);
                continue;
            }
            switch(st.eAction){
            case SPVA_Speak: case SPVA_SpellOut: case SPVA_Pronounce: break;
            default: continue;
            }
            if(!f->pTextStart||!f->ulTextLen)continue;
            bool spell=st.eAction==SPVA_SpellOut;
            if(cur<0||!runs[cur].same(st,spell))open_run(st,spell,0);
            Run &r=runs[cur];
            if(!r.text.empty()&&!iswspace(r.text.back())&&!iswspace(f->pTextStart[0]))
                r.text.push_back(L' ');
            r.text.append(f->pTextStart,f->ulTextLen);
        }
        /* Bookmarks are answered even when there is nothing to say.  A
         * fragment list of nothing but marks still carries indexes a client
         * is waiting on, and an index that never arrives is an utterance
         * the scheduler eventually purges -- the same contract, in the case
         * where it is cheapest to forget. */
        if(runs.empty()&&pending.empty())return S_OK;
        bool expand=setting_dword(L"ExpandAbbreviations",1)!=0;
        bool commands=setting_dword(L"AcceptCommands",0)!=0;
        std::wstring gen=token_string(token,L"Generation");
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
        long hostRate=0;
        site->GetRate(&hostRate);
        bool boost=setting_dword(L"RateBoost",0)!=0;
        std::wstring numberStyle=setting_string(L"NumberStyle",L"fix");
        unsigned numberFlags=numberStyle==L"fix"?2u:numberStyle==L"words"?4u:0u;
        bool speaks=false;
        for(auto &r:runs)if(!r.silenceMs&&!r.text.empty())speaks=true;

        CsLock lock(&g_hostLock);
        bool ok=true, aborted=false; int status=0;
        unsigned long long total=0;
        std::wstring pmod;
        if(speaks){
            ok=host_ensure(tree,mt,sd,vd,params,noAbbrev);
            /* Inflection, decided after the host is settled: a respawn is a
             * fresh channel sitting at the engine's own default, and
             * host_drop clears the latch to say so.  pmod is inflection
             * times two, exactly as the NVDA driver sends it, and nothing
             * at all goes out at the halfway default so an untouched
             * utterance stays byte-for-byte what Apple ships.  Prepended
             * after the stripping on purpose, to every request. */
            DWORD infl=setting_dword(L"Inflection",50);
            if(infl>100)infl=100;
            if(infl!=50){
                wchar_t cmd[32];
                swprintf_s(cmd,32,L"[[pmod %u]]",infl*2);
                pmod=cmd;g_inflSent=true;
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

        auto fire=[&](const Mark &m,unsigned long long at){
            SPEVENT ev;memset(&ev,0,sizeof ev);
            ev.eEventId=SPEI_TTS_BOOKMARK;
            ev.elParamType=SPET_LPARAM_IS_STRING;
            ev.ullAudioStreamOffset=at;
            ev.wParam=(WPARAM)_wtol(m.name.c_str());
            ev.lParam=(LPARAM)m.name.c_str();
            site->AddEvents(&ev,1);
        };
        /* Everything the host takes, it takes in full.  Write may accept
         * part of a block, or nothing at all while the audio device's buffer
         * is full -- Windows 7's SAPI does exactly that -- and what it did
         * not take is offered again after a short wait, the abort flag
         * checked between tries.  Before, the rest of the block was simply
         * dropped: on Windows 7 that was audio missing from the middle of
         * speech.  A skip request stops the writing too, where it may. */
        bool skipping=false;
        auto write_out=[&](const BYTE *p,ULONG n,bool mayskip)->bool{
            while(n){
                DWORD act=site->GetActions();
                if(act&SPVES_ABORT){aborted=true;return true;}
                if(mayskip&&(act&SPVES_SKIP)){skipping=true;return true;}
                ULONG took=0;
                if(FAILED(site->Write(p,n,&took)))return false;
                if(took>n)took=n;
                if(!took){Sleep(5);continue;}
                p+=took;n-=took;total+=took;
            }
            return true;
        };
        /* An interruption still kills the host, and that is a measurement
         * rather than an oversight.
         *
         * The engine has a graceful cancel -- a named event it polls
         * mid-render, which the NVDA driver uses -- and taking it here was
         * the plan until it was timed.  The host answers a cancel by
         * stopping the engine and waiting for its pacer to settle, and that
         * costs a flat ~47 ms whatever is in the pipe.  Panthera's whole
         * cold start is 21-52 ms.  So asking politely and then speaking
         * again came to 58-68 ms end to end, against 21-52 for killing it,
         * on every generation.
         *
         * outSPOKEN went the other way on the same question and was also
         * right: 158 ms of Python, driver, ROM and emulator meant it could
         * not afford to start again.  A host cheap enough to throw away is a
         * different problem, and this is the one place the two engines
         * deliberately disagree.
         *
         * The replacement starts here rather than at the next Speak, so
         * whatever gap the listener leaves is spent booting.
         * pantheradriver.py reaches for the same trick under the name of a
         * standby host. */
        auto interrupted=[&](){
            aborted=true;host_drop();
            host_ensure(tree,mt,sd,vd,params,noAbbrev);
        };

        /* What each run says once the settings are known: the document's
         * own commands gone unless the listener accepts them, the marks moved
         * with the words they stand beside, and where its sentences start.
         * A spelled run never carries commands: what is inside the tag is to
         * be read, character by character.
         *
         * A sentence starts with each run, and after a full stop, question
         * mark or exclamation mark followed by a space: what a skip request
         * counts in, and nothing about any one language. */
        size_t sentences=0;
        for(auto &r:runs){
            r.at.clear();
            for(auto &m:r.marks)r.at.push_back(m.pos);
            if(r.silenceMs)continue;
            r.body=(!commands||r.spell)?strip_commands(r.text,r.at):r.text;
            for(auto &p:r.at)if(p>r.body.size())p=r.body.size();
            r.firstSentence=sentences++;
            r.starts.assign(1,0);
            if(r.spell)continue;
            for(size_t i=0;i+1<r.body.size();i++){
                wchar_t c=r.body[i];
                if((c==L'.'||c==L'?'||c==L'!')&&iswspace(r.body[i+1])){
                    size_t j=i+1;
                    while(j<r.body.size()&&iswspace(r.body[j]))j++;
                    if(j<r.body.size()){r.starts.push_back(j);sentences++;}
                    i=j-1;
                }
            }
        }
        auto blank=[](const std::wstring &s,size_t from,size_t to){
            for(size_t i=from;i<to&&i<s.size();i++)if(!iswspace(s[i]))return false;
            return true;
        };
        /* The listener left this response behind for another place in the
         * text: the host's stream is abandoned the way an interruption
         * abandons it, and speech goes on from the new place. */
        auto leave=[&](){host_drop();host_ensure(tree,mt,sd,vd,params,noAbbrev);};
        size_t sentence=0;                      /* the one being spoken */
        /* One run, from `from` in its text: true to go on, false when a skip
         * request has to be answered first. */
        auto speak_run=[&](size_t ri,size_t from)->bool{
            Run &r=runs[ri];
            if(r.silenceMs){
                for(size_t i=0;i<r.marks.size();i++)if(r.at[i]!=(size_t)-1)fire(r.marks[i],total);
                std::vector<BYTE> zero((size_t)r.silenceMs*22050/1000*2);
                if(!write_out(zero.data(),(ULONG)zero.size(),true))ok=false;
                if(aborted){interrupted();return true;}
                if(skipping)return false;
                for(size_t i=0;i<r.marks.size();i++)if(r.at[i]==(size_t)-1)fire(r.marks[i],total);
                return true;
            }
            const std::wstring &body=r.body;
            sentence=r.firstSentence;
            for(size_t k=1;k<r.starts.size();k++)if(r.starts[k]<=from)sentence=r.firstSentence+k;
            /* -1: passed over by a skip, 0: before anything said,
             *  1: between words, 2: after it all */
            std::vector<int> where(r.marks.size());
            for(size_t i=0;i<r.marks.size();i++)
                where[i]=r.at[i]<from?-1:blank(body,from,r.at[i])?0:
                         blank(body,r.at[i],body.size())?2:1;
            /* A sync wherever something has to be known: a mark between
             * words, or a sentence starting. */
            struct Sync { size_t pos; std::vector<size_t> marks; long sentence; };
            std::vector<Sync> syncs;
            {
                std::vector<size_t> places;
                for(size_t i=0;i<r.marks.size();i++)if(where[i]==1)places.push_back(r.at[i]);
                for(size_t k=1;k<r.starts.size();k++)if(r.starts[k]>from)places.push_back(r.starts[k]);
                std::sort(places.begin(),places.end());
                places.erase(std::unique(places.begin(),places.end()),places.end());
                for(size_t p:places){
                    Sync s; s.pos=p; s.sentence=-1;
                    for(size_t k=1;k<r.starts.size();k++)if(r.starts[k]==p)s.sentence=(long)(r.firstSentence+k);
                    for(size_t i=0;i<r.marks.size();i++)if(where[i]==1&&r.at[i]==p)s.marks.push_back(i);
                    syncs.push_back(s);
                }
            }
            auto build=[&](const std::vector<bool> &use){
                std::wstring m; size_t prev=from;
                for(size_t k=0;k<syncs.size();k++){
                    if(!use[k])continue;
                    m.append(body,prev,syncs[k].pos-prev);
                    wchar_t cmd[32];
                    swprintf_s(cmd,32,L"[[sync 0x%x]]",SYNC_BASE+(unsigned)k+1);
                    m+=cmd; prev=syncs[k].pos;
                }
                m.append(body,prev,std::wstring::npos);
                return m;
            };
            auto unsynced=[](std::wstring s){
                for(size_t at;(at=s.find(L"[[sync 0x7a"))!=std::wstring::npos;){
                    size_t end=s.find(L"]]",at);
                    if(end==std::wstring::npos)break;
                    s.erase(at,end+2-at);
                }
                return s;
            };
            /* **A sync is a fence to the text rules, and must not become
             * one.**  The abbreviation rewrites read across the space after
             * a word -- "Dr." before a name is Doctor -- and a sync placed
             * there (a sentence start after "Dr.", or JAWS's mark between
             * every word) splits the prose they run on, so "Phycologist Dr.
             * Kirk" was read as a street.  So the text is prepared with the
             * syncs and without, as the NVDA driver does (host.py, "Commands
             * are lexical fences"), and a sync that changes what the rules
             * make of the words is left out: its marks wait for the next
             * sync that is kept, or for the estimate after the run. */
            std::vector<bool> use(syncs.size(),true);
            if(!r.spell&&!syncs.empty()){
                std::wstring control=prepare_text(body.substr(from),true,expand,gen);
                if(unsynced(prepare_text(build(use),true,expand,gen))!=control){
                    for(size_t k=0;k<syncs.size();k++){
                        std::vector<bool> one(syncs.size(),false); one[k]=true;
                        if(unsynced(prepare_text(build(one),true,expand,gen))!=control)use[k]=false;
                    }
                    if(unsynced(prepare_text(build(use),true,expand,gen))!=control)
                        use.assign(syncs.size(),false);
                }
            }
            std::wstring marked=build(use);
            bool anySync=false;
            for(bool u:use)if(u)anySync=true;
            unsigned long long runStart=total, last=total;   /* the latest mark sent */
            std::vector<bool> fired(r.marks.size(),false);
            for(size_t i=0;i<r.marks.size();i++)
                if(where[i]==0){fire(r.marks[i],total);fired[i]=true;}

            /* SAPI asks for the two to combine: the application's rate is
             * the base and the markup moves it, inside SAPI's -10..+10, and
             * the volume markup is a share of the application's volume
             * (Speech API 5.4, TTS Engine Vendor Porting Guide).
             *
             * SAPI's rate is logarithmic: zero is the engine default and
             * ten steps span roughly a factor of three in either direction.
             * Rate boost raises only the top -- the engine honours 1200 wpm
             * without a stumble, and it was measured doing so -- and never
             * the bottom, because a boost that also made slow slower would
             * be a different setting wearing this one's name. */
            long sapiRate=hostRate+r.rateAdj;
            if(sapiRate < -10)sapiRate=-10;if(sapiRate > 10)sapiRate=10;
            double top=(boost&&sapiRate>0)?6.667:3.0;
            int rate=(int)(180.0*pow(top,(double)sapiRate/10.0)+0.5);
            /* The pitch markup of this run; the request's pitch field is an
             * offset in tenths of a semitone from the voice's own, so one
             * SAPI step is a bit over a semitone and the ten-step ends land
             * an octave out, matching the NVDA slider's ends. */
            long pa=r.pitchAdj;
            if(pa<-10)pa=-10;if(pa>10)pa=10;
            int pitch=(int)(pa*12);
            /* A spelled run uses the engine's own literal mode, which names
             * each character -- and its numbers are digits, not amounts. */
            unsigned flags=r.spell?0u:numberFlags;
            bool markers=anySync;
            for(int attempt=0;attempt<2&&ok&&!aborted;attempt++){
                std::wstring text=markers?marked:body.substr(from);
                if(r.spell)text=L"[[char LTRL]]"+text+L"[[char NORM]]";
                else text=prepare_text(text,true,expand,gen);
                if(blank(text,0,text.size()))break;    /* nothing to say */
                text=pmod+text;
                unsigned request=markers?REQ_MAGIC_MARKERS:REQ_MAGIC_STREAM;
                std::string v=utf8(voice), u=utf8(text);
                unsigned nv=(unsigned)v.size(),nt=(unsigned)u.size();
                ok=host_ensure(tree,mt,sd,vd,params,noAbbrev)&&
                   exact(g_in,&request,4,true)&&exact(g_in,&rate,4,true)&&exact(g_in,&pitch,4,true)&&exact(g_in,&flags,4,true)&&exact(g_in,&nv,4,true)&&exact(g_in,&nt,4,true)&&exact(g_in,(void*)v.data(),nv,true)&&exact(g_in,(void*)u.data(),nt,true);
                unsigned magic=0;status=-1;
                /* Response reads wait rather than block: exact_wait watches the
                 * abort flag, the host's death and a no-progress deadline, so a
                 * wedged host costs one failed utterance instead of the session.
                 * An abort while waiting takes the same door as the mid-stream
                 * one below -- kill the host, boot the replacement. */
                if(ok){
                    ReadWait w=exact_wait(g_out,&magic,4,site);
                    if(w==RW_OK)w=exact_wait(g_out,&status,4,site);
                    if(w==RW_ABORT){interrupted();break;}
                    else if(w!=RW_OK||magic!=RSP_MAGIC)ok=false;
                }
                std::vector<BYTE> audio;
                /* Not `while(ok&&!status)`: the host answers every request with
                 * a terminator, an errored one included, and a resident pipe
                 * that skips those four bytes is desynced for good. */
                while(ok&&!aborted&&!skipping){
                    unsigned frames=0;
                    ReadWait w=exact_wait(g_out,&frames,4,site);
                    if(w==RW_ABORT){interrupted();break;}
                    if(w!=RW_OK){ok=false;break;}
                    if(!frames)break;
                    if(markers&&frames==STREAM_MARKER){
                        unsigned idf[2];
                        w=exact_wait(g_out,idf,8,site);
                        if(w==RW_ABORT){interrupted();break;}
                        if(w!=RW_OK){ok=false;break;}
                        /* A sync the engine swallowed -- it does, beside a
                         * dash -- must not let a later mark overtake it:
                         * everything up to this id goes out here, in order. */
                        unsigned id=idf[0]-SYNC_BASE;
                        unsigned long long at=runStart+(unsigned long long)idf[1]*2;
                        if(id>=1&&id<=syncs.size())
                            for(unsigned k=0;k<id;k++){
                                for(size_t i:syncs[k].marks)
                                    if(!fired[i]){fire(r.marks[i],at);fired[i]=true;last=at;}
                                if(syncs[k].sentence>=0)sentence=(size_t)syncs[k].sentence;
                            }
                        continue;
                    }
                    if(markers&&frames==STREAM_ERROR){
                        int code=0;
                        w=exact_wait(g_out,&code,4,site);
                        if(w==RW_ABORT){interrupted();break;}
                        if(w!=RW_OK){ok=false;break;}
                        continue;               /* the unreached fire below */
                    }
                    if(frames>MAX_CHUNK_FRAMES){
                        /* Two hundred times the host's own chunk cap is not a
                         * chunk, it is a desynced stream read as one; see the
                         * constant.  The pipe is unusable from here. */
                        logline(L"desync: frame count %u refused",frames);
                        ok=false;break;
                    }
                    unsigned bytes=frames*2; audio.resize(bytes);
                    w=exact_wait(g_out,audio.data(),bytes,site);
                    if(w==RW_ABORT){interrupted();break;}
                    if(w!=RW_OK){ok=false;break;}
                    /* SAPI owns the application slider; the engine applies its
                     * gain. Read it per chunk so a change during a paragraph
                     * does not restart synthesis or disturb Alex's breaths.
                     * Scaling the final PCM also preserves embedded volm
                     * commands, and the run's own volume markup is a share
                     * of the slider. */
                    USHORT volume=100;
                    if(FAILED(site->GetVolume(&volume))){ok=false;break;}
                    if(volume>100)volume=100;
                    unsigned gain=(unsigned)volume*(r.volume>100?100:r.volume);
                    if(gain!=10000){
                        short *samples=(short*)audio.data();
                        for(unsigned i=0;i<frames;i++)
                            samples[i]=(short)((long long)samples[i]*gain/10000);
                    }
                    if(!write_out(audio.data(),bytes,true)){ok=false;break;}
                    if(aborted){interrupted();break;}
                }
                if(skipping&&!aborted){leave();return false;}
                /* A host without positioned syncs for this generation says so
                 * up front, before any audio; the run is asked for again
                 * plainly and its marks placed by estimate below. */
                if(ok&&!aborted&&markers&&status==MARKERS_UNAVAILABLE&&total==runStart){
                    markers=false;status=0;continue;
                }
                break;
            }
            if(aborted)return true;
            /* Marks the host never placed -- no positioned syncs here, or a
             * sync the engine swallowed -- go out by estimate, proportional
             * to their place in the text, before the run's last ones. */
            unsigned long long len=total-runStart;
            size_t span=body.size()>from?body.size()-from:0;
            for(size_t i=0;i<r.marks.size();i++)
                if(!fired[i]&&where[i]==1){
                    unsigned long long at=runStart+(span?
                        (unsigned long long)((double)(r.at[i]-from)/(double)span*(double)len)/2*2:0);
                    if(at<last)at=last;                 /* never behind one already sent */
                    fire(r.marks[i],at);fired[i]=true;last=at;
                }
            for(size_t i=0;i<r.marks.size();i++)
                if(!fired[i]&&where[i]!=-1){fire(r.marks[i],total);fired[i]=true;}
            return true;
        };

        for(auto &m:pending)fire(m,0);          /* marks and nothing else */
        size_t ri=0, from=0;
        bool stopped=false;                     /* a skip that ran out of text */
        while(ri<runs.size()&&ok&&status==0&&!aborted){
            if(speak_run(ri,from)){ri++;from=0;continue;}
            /* A skip request (ISpVoice::Skip): how many sentences, from the
             * one being spoken, and in which direction; 0 is the start of
             * this one.  The number that could be skipped is reported, and
             * speech goes on from there if it was the number asked for --
             * or, when the text has fewer sentences than that, ends.  A host
             * that cannot say what it wants is answered with nothing
             * skipped, and the call ends. */
            skipping=false;
            SPVSKIPTYPE type=SPVST_SENTENCE; long n=0;
            if(FAILED(site->GetSkipInfo(&type,&n))||type!=SPVST_SENTENCE){
                site->CompleteSkip(0);stopped=true;break;
            }
            long target=(long)sentence+n;
            if(target<0||target>=(long)sentences){
                site->CompleteSkip(target<0?-(long)sentence:(long)sentences-1-(long)sentence);
                stopped=true;break;
            }
            site->CompleteSkip(n);
            for(size_t j=0;j<runs.size();j++){
                Run &q=runs[j];
                if(q.silenceMs)continue;
                if((size_t)target>=q.firstSentence&&(size_t)target<q.firstSentence+q.starts.size()){
                    ri=j;from=q.starts[(size_t)target-q.firstSentence];break;
                }
            }
        }
        if(!aborted&&!stopped){
            /* A run that failed still answers the marks after it: an index
             * that never arrives is a stuck utterance to the client. */
            for(size_t j=ri;j<runs.size();j++)
                for(auto &m:runs[j].marks)fire(m,total);
        }
        if(!aborted){
            /* And the tail.  The engines end at their last sample -- 0 ms of
             * silence measured on Tiger, Leopard and Snow Leopard, 5 on
             * Lion's Alex -- and some hosts (Balabolka) stop the device the
             * moment Speak returns, before it has drained.  50 ms of silence
             * is what they cut instead. */
            if(total&&ok&&status==0){
                static const BYTE pad[2204]={0};
                if(!write_out(pad,sizeof pad,false))ok=false;
                if(aborted)interrupted();
            }
        }
        /* A desynced pipe is never reused: whatever went wrong, the next
         * request would read this one's leftovers as its own. */
        if(!ok)host_drop();
        /* The measurements are what settled every bug this log has ever
         * settled.  The words are only ever asked for by name. */
        size_t chars=0,marks=pending.size();
        for(auto &r:runs){chars+=r.text.size();marks+=r.marks.size();}
        if(diagLevel()>=2)
            logline(L"speak: runs=%u chars=%u marks=%u bytes=%u ok=%d status=%d "
                    L"aborted=%d voice=%.24s text=\"%.40s\"",
                    (unsigned)runs.size(),(unsigned)chars,(unsigned)marks,(unsigned)total,
                    ok?1:0,status,aborted?1:0,voice.c_str(),
                    runs.empty()?L"":runs[0].text.c_str());
        else
            logline(L"speak: runs=%u chars=%u marks=%u bytes=%u ok=%d status=%d "
                    L"aborted=%d voice=%.24s",
                    (unsigned)runs.size(),(unsigned)chars,(unsigned)marks,(unsigned)total,
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
