/* File rendering through the same request loop as IPC. No second synthesis
 * pipeline: stdin is text here, while --serve retains its binary protocol. */
static void cli_help(void)
{
    fprintf(stdout,
        "Usage: tiger_host --render --tree DIR [options]\n"
        "  --voice NAME       Bundle name, without .SpeechVoice (default Fred)\n"
        "  --text TEXT        UTF-8 text; otherwise read UTF-8 from stdin\n"
        "  --input FILE       Read UTF-8 from a file (- means stdin)\n"
        "  --output FILE      PCM16 WAV destination (- means stdout)\n"
        "                     Default: tiger-out.wav\n"
        "  --rate WPM         1..1200, default 180\n"
        "  --pitch TENTHS     Semitone offset, -120..120, default 0\n"
        "  --volume PERCENT   0..100, default 100 (engine gain)\n"
        "  --numbers STYLE    off, fix (default), words\n"
        "  --help             Show this help\n"
        "DIR contains Speech/ and SpeechDictionary.framework/.\n"
        "For persistent streaming IPC: --serve ENGINE DICTIONARY VOICES\n"
        "Inspect codec availability with --capabilities or --aac-check.\n");
}

static int cli_integer(const char *s, int lo, int hi, int *out)
{
    char *end; long n;
    errno=0; n=strtol(s,&end,10);
    if(errno || !*s || *end || n<lo || n>hi)return 0;
    *out=(int)n; return 1;
}

/* Reuse the Unicode table used by CF strings in the other direction.
 * Reject malformed UTF-8; unsupported characters become spaces with a diagnostic. */
static char *cli_macroman(const char *input)
{
    const unsigned char *p=(const unsigned char *)input;
    char *out=(char *)malloc(strlen(input)+1), *w=out;
    unsigned lost=0;
    if(!out)return NULL;
    while(*p){
        unsigned cp=*p++, need=0, minimum=0, j;
        if(cp>=0x80){
            if(cp>=0xc2&&cp<=0xdf){cp&=31;need=1;minimum=0x80;}
            else if(cp>=0xe0&&cp<=0xef){cp&=15;need=2;minimum=0x800;}
            else if(cp>=0xf0&&cp<=0xf4){cp&=7;need=3;minimum=0x10000;}
            else goto invalid;
            while(need--){if((*p&0xc0)!=0x80)goto invalid;cp=(cp<<6)|(*p++&63);}
            if(cp<minimum||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff))goto invalid;
        }
        if(cp==0xfeff&&w==out)continue; /* optional UTF-8 BOM */
        if(cp<128){*w++=(char)cp;continue;}
        for(j=0;j<128&&CF_MACROMAN[j]!=cp;j++);
        if(j<128)*w++=(char)(j+128);
        else {*w++=' ';lost++;}
    }
    *w=0;
    if(lost)fprintf(stderr,"tiger_host: %u character(s) outside MacRoman replaced with spaces\n",lost);
    return out;
invalid:
    fprintf(stderr,"tiger_host: input must be valid UTF-8\n");free(out);return NULL;
}

static int cli_render(int argc, char **argv)
{
    const char *tree=NULL,*voice="Fred",*text=NULL,*input=NULL,*output="tiger-out.wav";
    int rate=180,pitch=0,volume=100,i,rc=2,status;
    unsigned flags=2,magic=REQ_MAGIC,namelen,textlen,nframes;
    char engine[CFPATH],dictionary[CFPATH],voices[CFPATH],prefix[40];
    char *encoded=NULL;
    numbuf source;
    FILE *in=NULL,*request=NULL,*response=NULL,*wav=NULL;
    memset(&source,0,sizeof source);
    for(i=2;i<argc;i++){
        const char *option=argv[i],*value;
        if(!strcmp(option,"--help")){cli_help();return 0;}
        if(++i>=argc){fprintf(stderr,"missing value for %s\n",option);return 2;}
        value=argv[i];
        if(!strcmp(option,"--tree"))tree=value;
        else if(!strcmp(option,"--voice"))voice=value;
        else if(!strcmp(option,"--text"))text=value;
        else if(!strcmp(option,"--input"))input=value;
        else if(!strcmp(option,"--output"))output=value;
        else if(!strcmp(option,"--rate")&&cli_integer(value,1,1200,&rate)){}
        else if(!strcmp(option,"--pitch")&&cli_integer(value,-120,120,&pitch)){}
        else if(!strcmp(option,"--volume")&&cli_integer(value,0,100,&volume)){}
        else if(!strcmp(option,"--numbers")&&(!strcmp(value,"off")||!strcmp(value,"fix")||!strcmp(value,"words")))
            flags=!strcmp(value,"fix")?2:!strcmp(value,"words")?4:0;
        else {fprintf(stderr,"unknown option or invalid value: %s %s\n",option,value);return 2;}
    }
    if(!tree||(text&&input)||!*voice||strlen(voice)>=128||strpbrk(voice,"/\\")){
        fprintf(stderr,"--tree and a bundle name are required; choose --text OR --input\n");return 2;
    }
    if(strlen(tree)+100>=CFPATH){fprintf(stderr,"engine tree path is too long\n");return 2;}
    _snprintf(engine,sizeof engine,"%s/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk",tree);
    _snprintf(dictionary,sizeof dictionary,"%s/SpeechDictionary.framework/Versions/A/SpeechDictionary",tree);
    _snprintf(voices,sizeof voices,"%s/Speech/Voices",tree);
    if(!text){
        char block[4096]; size_t got;
        in=input&&strcmp(input,"-")?fopen(input,"rb"):stdin;
        if(!in){perror(input);goto done;}
        while((got=fread(block,1,sizeof block,in))!=0){
            if(source.len+got>1024*1024||memchr(block,0,got)){
                fprintf(stderr,"text exceeds 1 MiB or contains NUL\n");goto done;
            }
            nb_add(&source,block,got);if(source.failed)goto done;
        }
        if(ferror(in)){perror("reading text");goto done;}
        text=source.buf?source.buf:"";
    }
    if(!*text||strlen(text)>1024*1024){fprintf(stderr,"text must contain 1..1048576 bytes\n");goto done;}
    encoded=cli_macroman(text);if(!encoded)goto done;
    if(!strcmp(output,"-")){
        int fd=_dup(_fileno(stdout));
        if(fd<0)goto done;
        wav=_fdopen(fd,"wb");
        if(!wav){_close(fd);goto done;}
    }
    /* A diagnostic from any guest shim must not enter a piped WAV. */
    fflush(stdout);
    if(_dup2(_fileno(stderr),_fileno(stdout))<0)goto done;
    request=tmpfile();response=tmpfile();
    if(!request||!response){perror("temporary render stream");goto done;}
    _snprintf(prefix,sizeof prefix,"[[volm %d.%03d]]",volume/100,(volume%100)*10);
    namelen=(unsigned)strlen(voice);textlen=(unsigned)(strlen(prefix)+strlen(encoded));
    fwrite(&magic,4,1,request);fwrite(&rate,4,1,request);fwrite(&pitch,4,1,request);
    fwrite(&flags,4,1,request);fwrite(&namelen,4,1,request);fwrite(&textlen,4,1,request);
    fwrite(voice,1,namelen,request);fwrite(prefix,1,strlen(prefix),request);
    fwrite(encoded,1,strlen(encoded),request);
    if(fflush(request)||ferror(request))goto done;
    rewind(request);
    if(host_open(engine,dictionary))goto done;
    g_in=request;g_out=response;
    if(serve(&g_mt,g_chan,voices)||fflush(response))goto done;
    rewind(response);
    if(!read_all(response,&magic,4))goto done;
    if(magic==0x59445254u&&!read_all(response,&magic,4))goto done;
    if(magic!=RSP_MAGIC||!read_all(response,&status,4)||status||
       !read_all(response,&nframes,4)||!nframes)goto done;
    if(nframes>=PCM_CAP){
        fprintf(stderr,"utterance reached the four-minute buffer; split at paragraph boundaries\n");goto done;
    }
    if(!wav)wav=fopen(output,"wb");
    if(!wav){perror(output);goto done;}
    _setmode(_fileno(wav),_O_BINARY);
    {
        unsigned bytes=nframes*2,riff=bytes+36,fmtSize=16,hz=22050,byteRate=44100;
        unsigned short format=1,channels=1,align=2,bits=16;
        char block[8192];
        fwrite("RIFF",1,4,wav);fwrite(&riff,4,1,wav);fwrite("WAVEfmt ",1,8,wav);
        fwrite(&fmtSize,4,1,wav);fwrite(&format,2,1,wav);fwrite(&channels,2,1,wav);
        fwrite(&hz,4,1,wav);fwrite(&byteRate,4,1,wav);fwrite(&align,2,1,wav);
        fwrite(&bits,2,1,wav);fwrite("data",1,4,wav);fwrite(&bytes,4,1,wav);
        while(bytes){
            unsigned n=bytes>sizeof block?sizeof block:bytes;
            if(!read_all(response,block,n)||fwrite(block,1,n,wav)!=n)goto done;
            bytes-=n;
        }
    }
    if(!fflush(wav)&&!ferror(wav))rc=0;
done:
    if(in&&in!=stdin)fclose(in);
    if(request)fclose(request);
    if(response)fclose(response);
    if(wav&&fclose(wav))rc=2;
    free(encoded);free(source.buf);
    if(rc)fprintf(stderr,"tiger_host: render failed\n");
    return rc;
}
