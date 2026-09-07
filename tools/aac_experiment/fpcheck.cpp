#include <cfenv>
#include <cstdio>
#include <vector>
#include <cstring>
#include <iterator>
#include <fstream>
extern "C" void *pt_glint_open(void);
extern "C" void pt_glint_close(void *);
extern "C" int pt_glint_decode(void *, const unsigned char *, int, short *, unsigned, unsigned);
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    std::ifstream file(argv[1], std::ios::binary);
    std::vector<unsigned char> data((std::istreambuf_iterator<char>(file)), {});
    if (data.empty()) return 3;
    std::vector<short> ref;
    int failed = 0;
    for (int mode : {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO}) {
        std::fesetround(mode);
        void *d = pt_glint_open();
        if (!d || std::fegetround() != mode) return 4;
        std::vector<short> got;
        for (size_t offset=0; offset+7<=data.size();) {
            auto *h = data.data()+offset;
            int len = ((h[3]&3)<<11) | (h[4]<<3) | (h[5]>>5);
            if (len < 7 || offset+len>data.size()) return 5;
            short pcm[2048];
            int n=pt_glint_decode(d,h,len,pcm,22050,1);
            if(n!=1024 || std::fegetround()!=mode) return 6;
            got.insert(got.end(),pcm,pcm+n);
            offset+=len;
        }
        pt_glint_close(d);
        if(ref.empty()) ref=got;
        size_t count=0;
        for(size_t i=0;i<got.size();++i) if(got[i]!=ref[i]) ++count;
        printf("rounding=%d frames=%zu differences=%zu environment_restored=yes\n",mode,got.size(),count);
        failed |= count != 0;
    }
    return failed;
}
