#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <string.h>
#include "tiger_host_jni.h"
static double ms(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000.0+t.tv_nsec/1e6;}
int main(int argc,char **argv) {
    if(argc<5 || argc>6 || (argc==6 && strcmp(argv[5],"cancel"))) {
        fprintf(stderr,"usage: bench engine dictionary voice output.pcm [cancel]\n");return 2;
    }
    unsigned creator;int voice;
    if(!panthera_voice_spec(argv[3],&creator,&voice))return 3;
    panthera_set_phrasing("fewest");panthera_set_volume(90,"leopard");
    double t=ms();int rc=panthera_init(argv[1],argv[2]);
    fprintf(stdout,"INIT %d %.1fms voice=%08x/%d\n",rc,ms()-t,creator,voice);
    if(rc)return 4;
    short pcm[4096];
    for(int k=0;k<8;k++) {
        if(argc==6) {
            char paragraph[4096]="";
            for(int j=0;j<24;j++) strcat(paragraph,"The quick brown fox jumps over the lazy dog. This is a longer paragraph to interrupt. ");
            rc=panthera_speak_start(argv[3],creator,voice,paragraph,387);
            if(rc || panthera_pull(pcm,4096)<=0)return 8;
            double stop=ms();panthera_stop();panthera_finish();
            fprintf(stdout,"CANCEL %d settle=%.1fms\n",k,ms()-stop);fflush(stdout);
        }
        char path[1024];snprintf(path,sizeof(path),"%s.%d",argv[4],k); FILE *f=fopen(path,"wb");if(!f)return 5;
        double begin=ms(),first=-1;unsigned frames=0;
        rc=panthera_speak_start(argv[3],creator,voice,k%2?"Restart with debug logging enabled.":"Seven",387);
        if(rc)return 6;
        int n;while((n=panthera_pull(pcm,4096))>0) {
            if(first<0)first=ms()-begin;
            frames+=n;fwrite(pcm,2,n,f);
        }
        double done=ms()-begin;fclose(f);panthera_finish();
        fprintf(stdout,"RENDER %d first=%.1f done=%.1f frames=%u status=%d\n",k,first,done,frames,n);
        fflush(stdout);if(n<0 || frames<2000)return 7;
    }
    return 0;
}
