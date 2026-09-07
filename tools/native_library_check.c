/* Public-API client, linked against libpanthera rather than host internals.
 * Run: native_library_check TREE VOICE. Engine data stays on the user's box. */
#include <panthera.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void require(int ok, const char *message)
{
    if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(1);}
}

static short *stream(const char *voice, unsigned creator, int id,
                     const char *text, unsigned *frames, int cancel)
{
    short chunk[4096],*pcm=NULL; int n;
    *frames=0;
    require(panthera_speak_start(voice,creator,id,text,180)==0,"start stream");
    while((n=panthera_pull(chunk,4096))>0){
        short *grown=realloc(pcm,(*frames+(unsigned)n)*sizeof(short));
        require(grown!=NULL,"allocate client PCM");pcm=grown;
        memcpy(pcm+*frames,chunk,(size_t)n*sizeof(short));*frames+=(unsigned)n;
        if(cancel){panthera_stop();break;}
    }
    require(n>=0,"pull stream");
    if(cancel)panthera_finish();
    return pcm;
}

int main(int argc,char **argv)
{
    char mt[4096],sd[4096],voice[4096];
    unsigned creator,frames,againFrames,bufferedFrames; int id;
    short *first,*again,*buffered=NULL;
    const char *text="The quick brown fox jumps over the lazy dog.";
    if(argc==2&&!strcmp(argv[1],"--check")){
        require(panthera_voice_spec("/nonexistent/panthera-voice",&creator,&id)==0,"missing voice is rejected");
        require(panthera_set_phrasing("invalid")==-50,"invalid phrase policy is rejected");
        require(panthera_set_phrasing("leopard")==0,"configure before init");
        panthera_set_expand_abbreviations(1);
        panthera_set_number_style("fix");
        panthera_set_volume(-1,"");
        panthera_stop();panthera_finish();
        require(panthera_speak_start("",0,0,"",180)==-1,"stream before init is rejected");
        require(panthera_render("",0,0,"",180,&buffered,&bufferedFrames)==-1,"render before init is rejected");
        puts("libpanthera: public C API links and configures without engine data");
        return 0;
    }
    if(argc!=3){fprintf(stderr,"Usage: native_library_check TREE VOICE\n");return 2;}
    snprintf(mt,sizeof mt,"%s/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk",argv[1]);
    snprintf(sd,sizeof sd,"%s/SpeechDictionary.framework/Versions/A/SpeechDictionary",argv[1]);
    snprintf(voice,sizeof voice,"%s/Speech/Voices/%s.SpeechVoice",argv[1],argv[2]);
    require(panthera_voice_spec(voice,&creator,&id)==1,"read voice identity");
    require(panthera_set_phrasing("leopard")==0,"set initial phrase policy");
    require(panthera_init(mt,sd)==0,"initialize shared library");
    require(panthera_sample_rate()==22050,"sample rate");
    panthera_set_volume(-1,"");
    panthera_set_number_style("off");
    first=stream(voice,creator,id,text,&frames,0);
    require(frames>22050,"full sentence duration");
    require(panthera_render(voice,creator,id,text,180,&buffered,&bufferedFrames)==0,"buffered render");
    require(bufferedFrames==frames&&!memcmp(first,buffered,frames*sizeof(short)),"buffered and streamed PCM agree");
    free(buffered);
    again=stream(voice,creator,id,"This is a long sentence that the client interrupts while audio is still being produced. Another sentence follows it.",&againFrames,1);
    require(againFrames>0&&againFrames<=4096,"stop after the first chunk");free(again);
    again=stream(voice,creator,id,text,&againFrames,0);
    require(againFrames==frames&&!memcmp(first,again,frames*sizeof(short)),"reference-correct recovery after cancellation");
    free(again);
    again=stream(voice,creator,id,"There are twelve people.",&againFrames,0);
    panthera_set_number_style("words");
    require(panthera_render(voice,creator,id,"There are 12 people.",180,&buffered,&bufferedFrames)==0,"native number style");
    require(bufferedFrames==againFrames&&!memcmp(again,buffered,againFrames*sizeof(short)),"number setting takes effect");
    free(again);free(buffered);free(first);
    printf("libpanthera: %s PASS, %u frames; render/stream/cancel/numbers\n",argv[2],frames);
    return 0;
}
