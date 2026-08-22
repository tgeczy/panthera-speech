/* tiger_host_accel.c -- the handful of Accelerate routines Alex needs.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * Leopard's Alex is a concatenative voice, and changing its rate means
 * time-scaling recorded speech rather than re-running a formant model.
 * MTMBModRateWsola does that with WSOLA -- waveform similarity overlap-add --
 * and WSOLA is a search: for every output frame it slides a window over the
 * source looking for the best alignment. That search is where the vector maths
 * goes, and Apple sent it to Accelerate.
 *
 * Stubbed out, Alex opened, took the voice, accepted the text, ran to
 * completion and produced one frame of nothing. The counters gave it away:
 *
 *     58186903 x _vDSP_svemg
 *     58189323 x _vDSP_vmsb
 *       524218 x _vDSP_vmma
 *       524241 x _vmul
 *
 * Fifty-eight million calls into empty functions.
 *
 * **Every signature below was read out of the binary, not remembered.** The
 * argument counts come from counting stack slots at the call sites in
 * MTMBModRateWsola::ModifyRate, which is the only way to be sure: vDSP_vmsb
 * takes nine arguments and vDSP_vmma eleven, which is easy to get backwards,
 * and `vmul` is the older vecLib spelling with seven rather than the vDSP one
 * with five. A wrong guess here would not crash -- it would quietly produce
 * the wrong sound.
 *
 * Strides are honoured by walking pointers rather than by indexing, because a
 * vDSP stride is signed and may run backwards.
 */

typedef long          vdsp_stride;
typedef unsigned long vdsp_length;

/* vDSP_svemg(A, IA, C, N): *C = sum of |A[n]|.
 *
 * "Sum of vector element magnitudes" -- in ModifyRate this scores how well a
 * candidate window matches, so it runs once per candidate offset. */
/* Instrumentation, off unless TIGER_ACCEL_DEBUG is set.  Fifty-eight million
 * calls cannot be printed, but the first handful of each say whether the
 * lengths and strides are sane and whether the search scores vary at all -- a
 * score that never changes means the search is degenerate and every grain will
 * come from the same place, which sounds like a stutter. */
static int g_accel_debug = -1;
static int accel_debug(void)
{
    if (g_accel_debug < 0)
        g_accel_debug = getenv("TIGER_ACCEL_DEBUG") ? 1 : 0;
    return g_accel_debug;
}

static void __cdecl sh_vDSP_svemg(const float *A, vdsp_stride IA,
                                  float *C, vdsp_length N)
{
    float sum = 0.0f;
    vdsp_length n;
    const float *p = A;
    if (!A || !C) return;
    for (n = 0; n < N; n++, p += IA)
        sum += (*p < 0.0f) ? -*p : *p;
    *C = sum;
    if (accel_debug()) {
        static unsigned calls;
        static float lo = 1e30f, hi = -1e30f;
        if (sum < lo) lo = sum;
        if (sum > hi) hi = sum;
        if (++calls <= 6 || (calls % 10000000) == 0)
            printf("  [vDSP] svemg #%u N=%lu IA=%ld first=%g sum=%g "
                   "(range %g..%g)\n", calls, (unsigned long)N, (long)IA,
                   (double)A[0], (double)sum, (double)lo, (double)hi);
    }
}

/* vDSP_vmsb(A, IA, B, IB, C, IC, D, ID, N): D[n] = A[n]*B[n] - C[n].
 *
 * Nine arguments, not eleven: the subtrahend is a plain vector, not a second
 * product. Confirmed at MacinTalk + 0x5700c, which fills [esp] through
 * [esp+0x20] and no further. */
static void __cdecl sh_vDSP_vmsb(const float *A, vdsp_stride IA,
                                 const float *B, vdsp_stride IB,
                                 const float *C, vdsp_stride IC,
                                 float *D, vdsp_stride ID, vdsp_length N)
{
    vdsp_length n;
    static int variant = -1;
    if (!A || !B || !C || !D) return;
    /* TIGER_VMSB picks an alternative reading, so the one that is only
     * inferred can be tested rather than argued about. The engine feeds this
     * a window, a candidate and a windowed target and sums the magnitudes of
     * the result to score an alignment; a wrong reading does not crash, it
     * just scores badly and joins the grains in the wrong place. */
    if (variant < 0) {
        const char *v = getenv("TIGER_VMSB");
        variant = v ? atoi(v) : 0;
    }
    switch (variant) {
    case 1:     /* D = A - B*C */
        for (n = 0; n < N; n++, A += IA, B += IB, C += IC, D += ID)
            *D = *A - *B * *C;
        break;
    case 2:     /* D = (A - B) * C */
        for (n = 0; n < N; n++, A += IA, B += IB, C += IC, D += ID)
            *D = (*A - *B) * *C;
        break;
    default:    /* D = A*B - C, the documented vDSP_vmsb */
        for (n = 0; n < N; n++, A += IA, B += IB, C += IC, D += ID)
            *D = *A * *B - *C;
        break;
    }
}

/* vDSP_vmma(A, IA, B, IB, C, IC, D, ID, E, IE, N):
 *     E[n] = A[n]*B[n] + C[n]*D[n]
 *
 * Eleven arguments; the call at MacinTalk + 0x56f65 fills [esp] through
 * [esp+0x28]. This is the cross-fade: the tail of one window multiplied by a
 * falling ramp, plus the head of the next by a rising one. */
static void __cdecl sh_vDSP_vmma(const float *A, vdsp_stride IA,
                                 const float *B, vdsp_stride IB,
                                 const float *C, vdsp_stride IC,
                                 const float *D, vdsp_stride ID,
                                 float *E, vdsp_stride IE, vdsp_length N)
{
    vdsp_length n;
    int variant = wsola_variant();
    if (!A || !B || !C || !D || !E) return;
    if (variant == 1) {          /* windows paired the other way round */
        for (n = 0; n < N; n++, A += IA, B += IB, C += IC, D += ID, E += IE)
            *E = *C * *B + *A * *D;
        return;
    }
    if (variant == 2) {          /* no cross-fade at all: hard splice */
        for (n = 0; n < N; n++, B += IB, D += ID, E += IE)
            *E = *D;
        return;
    }
    if (variant == 3) {          /* the other side of the splice */
        for (n = 0; n < N; n++, B += IB, D += ID, E += IE)
            *E = *B;
        return;
    }
    /* The two weights here are the same Hann window read from two places --
     * A is &w[hop] and C is &w[0] -- so for the overlap-add to be transparent
     * they must sum to one at every n. That is only true when the hop is
     * exactly half the window length. If WSOLA varies its hop, a plain Hann
     * pair no longer sums to unity and every join gets an amplitude step,
     * which is what a crackle laid over otherwise clean speech is.
     *
     * So measure it rather than assume it. */
    if (accel_debug()) {
        static unsigned calls;
        static double worst;
        double dev = 0.0;
        vdsp_length k;
        for (k = 0; k < N; k++) {
            double sum = (double)A[k * IA] + (double)C[k * IC];
            double e = sum - 1.0;
            if (e < 0) e = -e;
            if (e > dev) dev = e;
        }
        if (dev > worst) worst = dev;
        if (++calls <= 6 || (calls % 100000) == 0)
            printf("  [vDSP] vmma #%u N=%lu  weights sum to 1 +/- %.4f "
                   "(worst %.4f)\n", calls, (unsigned long)N, dev, worst);
    }
    for (n = 0; n < N; n++, A += IA, B += IB, C += IC, D += ID, E += IE)
        *E = *A * *B + *C * *D;
}

/* vmul(A, I, B, J, C, K, N): C[n] = A[n]*B[n].
 *
 * vecLib's original spelling, seven arguments -- not vDSP_vmul, which takes
 * the same shape but is a different symbol. The call at MacinTalk + 0x573b3
 * fills [esp] through [esp+0x18]; N is a signed count there, so it is taken
 * as one. */
static void __cdecl sh_vmul(const float *A, int I, const float *B, int J,
                            float *C, int K, int N)
{
    int n;
    if (!A || !B || !C) return;
    for (n = 0; n < N; n++, A += I, B += J, C += K)
        *C = *A * *B;
}

/* TIGER_WSOLA selects a variant of the two calls that actually PRODUCE
 * samples, as opposed to the search.  Vicki never runs any of this and sounds
 * right; Alex runs all of it and does not, so these are the calls left to
 * doubt, and no measurement available here can tell a good Alex from a bad one
 * -- only a listener can.  Rendering the variants side by side spends one
 * listening pass instead of one per idea. */
static int wsola_variant(void)
{
    static int v = -1;
    if (v < 0) { const char *e = getenv("TIGER_WSOLA"); v = e ? atoi(e) : 0; }
    return v;
}

/* vDSP_hann_window(C, N, flag): fill C with a Hann window.
 *
 * Apple's window is periodic -- 2*pi*n/N, not 2*pi*n/(N-1) -- which matters
 * for overlap-add: the symmetric form does not sum to a constant and would
 * leave a slow tremor across every join. MacinTalk passes flag 0, which is
 * vDSP_HANN_DENORM, so that is the only path this actually takes; the other
 * two are written out because they are two lines and guessing later would be
 * worse.
 */
#define VDSP_HALF_WINDOW  1
#define VDSP_HANN_NORM    2

static void __cdecl sh_vDSP_hann_window(float *C, vdsp_length N, int flag)
{
    /* 0.8165 is sqrt(2/3), Apple's normalisation constant. */
    const double scale = (wsola_variant() == 4) ? 0.8165
                       : ((flag & VDSP_HANN_NORM) ? 0.8165 : 0.5);
    vdsp_length count = (flag & VDSP_HALF_WINDOW) ? (N + 1) / 2 : N;
    vdsp_length n;
    if (!C || !N) return;
    for (n = 0; n < count; n++)
        C[n] = (float)(scale * (1.0 - cos(6.2831853071795864769 * n / N)));
    if (accel_debug())
        printf("  [vDSP] hann N=%lu flag=%d -> C[0]=%g C[N/4]=%g C[N/2]=%g\n",
               (unsigned long)N, flag, (double)C[0],
               (double)C[N / 4], (double)C[N / 2]);
}

/* lrintf: round to nearest, ties to even, which is what the default rounding
 * mode gives. Reached twice, so nothing here is worth optimising. */
static long __cdecl sh_lrintf(float x)
{
    double d = (double)x;
    double r = floor(d + 0.5);
    if (r - d == 0.5) {                 /* a tie: go to even */
        double half = r / 2.0;
        if (half != floor(half)) r -= 1.0;
    }
    return (long)r;
}


/* ---- 10.7's FFT ---------------------------------------------------------
 *
 * Lion's WSOLA finds its overlap point by **cross-correlation in the
 * frequency domain** where Leopard's worked in the time domain: transform
 * both windows, multiply one by the conjugate of the other, come back, and
 * take the index of the peak.  That is eleven Accelerate functions Leopard
 * never imports, and without them Alex, Bruce and Agnes all die at the same
 * instruction in `MTMBModRateWsola::ModifyRate`.
 *
 * Ten of them are a line each.  The FFT is not, and it is the one worth being
 * careful about: an FFT that is subtly wrong produces plausible numbers, and
 * plausible numbers in a correlation put the peak in the wrong place -- a
 * voice that sounds slightly off rather than a crash.  So it is checked
 * against numpy through `--vdsp-check`; see panthera/tests/test_vdsp.py.
 */

/* A DSPSplitComplex is two pointers: the reals and the imaginaries kept apart
 * rather than interleaved, so each can be walked with its own stride. */
typedef struct { float *realp; float *imagp; } split_complex;

#define FFT_FORWARD  1
#define FFT_INVERSE (-1)
#define ACCEL_PI 3.14159265358979323846

/* Apple's FFTSetup is opaque; ours only has to be something `fft_zrip` can
 * read back and `destroy_fftsetup` can free. */
typedef struct { unsigned log2n, n; } fft_setup;

static void * __cdecl sh_create_fftsetup(vdsp_length log2n, int radix)
{
    fft_setup *s;
    if (radix != 0) {                    /* kFFTRadix2 is the only one used */
        fprintf(stderr, "tiger_host: create_fftsetup asked for radix %d, "
                        "which this does not implement\n", radix);
        return NULL;
    }
    if (log2n > 20) return NULL;
    s = (fft_setup *)calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->log2n = (unsigned)log2n;
    s->n = 1u << log2n;
    return s;
}

static void __cdecl sh_destroy_fftsetup(void *setup)
{
    free(setup);
}

/* An in-place complex FFT of `m` points, iterative radix-2.
 *
 * `sign` is -1 for the forward transform, matching e^(-2*pi*i*kn/N), which is
 * what both vDSP and numpy mean by forward.  No scaling is applied; the
 * callers below do whatever their convention wants. */
static void fft_complex(double *re, double *im, unsigned m, int sign)
{
    unsigned i, j, len, half, k;
    for (i = 1, j = 0; i < m; i++) {         /* bit reversal */
        unsigned bit = m >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            double t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (len = 2; len <= m; len <<= 1) {
        half = len >> 1;
        for (i = 0; i < m; i += len) {
            for (k = 0; k < half; k++) {
                double ang = 2.0 * ACCEL_PI * k / (double)len;
                double c = cos(ang);
                double s = sin(ang) * (sign < 0 ? -1.0 : 1.0);
                unsigned a = i + k, b = a + half;
                double ur = re[a], ui = im[a];
                double vr = re[b] * c - im[b] * s;
                double vi = re[b] * s + im[b] * c;
                re[a] = ur + vr; im[a] = ui + vi;
                re[b] = ur - vr; im[b] = ui - vi;
            }
        }
    }
}

/* vDSP_fft_zrip(setup, ioData, stride, log2n, direction): a **packed real**
 * FFT, in place.
 *
 * Packed means N/2 slots hold what N/2+1 complex bins would, by putting the
 * two purely-real bins together: after a forward transform `realp[0]` is DC
 * and `imagp[0]` is Nyquist.  Bin zero's imaginary part is always zero for
 * real input, so that slot is free and the format wastes nothing.
 *
 * **The result is scaled by two** against the textbook DFT.  That is vDSP's
 * convention rather than an accident here.  For this engine it is invisible,
 * because the value goes to `vDSP_maxvi` and a uniform scale cannot move an
 * argmax -- but it is what any other caller would expect.
 */
static void __cdecl sh_fft_zrip(void *setup, split_complex *io,
                                vdsp_stride stride, vdsp_length log2n,
                                int direction)
{
    fft_setup *s = (fft_setup *)setup;
    unsigned n = 1u << log2n, h = n / 2, k;
    double *re, *im, *ar, *ai;

    if (!io || !io->realp || !io->imagp || h == 0) return;
    if (s && s->n < n) {
        fprintf(stderr, "tiger_host: fft_zrip asked for 2^%u with a setup "
                        "built for 2^%u\n", (unsigned)log2n, s->log2n);
        return;
    }
    re = (double *)malloc(sizeof(double) * h);
    im = (double *)malloc(sizeof(double) * h);
    ar = (double *)malloc(sizeof(double) * h);
    ai = (double *)malloc(sizeof(double) * h);
    if (!re || !im || !ar || !ai) {
        free(re); free(im); free(ar); free(ai);
        return;
    }
    for (k = 0; k < h; k++) {
        re[k] = io->realp[k * stride];
        im[k] = io->imagp[k * stride];
    }

    if (direction == FFT_FORWARD) {
        fft_complex(re, im, h, -1);
        /* The N/2-point spectrum of (even + i*odd) carries both halves; the
         * even part is the hermitian-symmetric piece and the odd part the
         * antisymmetric one, recombined with a half-bin twiddle. */
        ar[0] = re[0] + im[0];               /* DC */
        ai[0] = re[0] - im[0];               /* Nyquist, packed alongside */
        for (k = 1; k < h; k++) {
            unsigned m = h - k;
            double er = 0.5 * (re[k] + re[m]);
            double ei = 0.5 * (im[k] - im[m]);
            double orr = 0.5 * (im[k] + im[m]);
            double oi = -0.5 * (re[k] - re[m]);
            double c = cos(-2.0 * ACCEL_PI * k / (double)n);
            double sn = sin(-2.0 * ACCEL_PI * k / (double)n);
            ar[k] = er + (orr * c - oi * sn);
            ai[k] = ei + (orr * sn + oi * c);
        }
        for (k = 0; k < h; k++) {
            io->realp[k * stride] = (float)(2.0 * ar[k]);
            io->imagp[k * stride] = (float)(2.0 * ai[k]);
        }
    } else {
        /* The inverse is written the long way round on purpose.
         *
         * Folding it into an N/2-point transform the way the forward branch
         * does is a page of index algebra, and the first attempt was wrong in
         * the one bin that carries two values -- which showed up not as a
         * wrong level but as a **spectral tilt**, an error that still looks
         * like a signal. So: unpack the half-spectrum into the whole one
         * using the conjugate symmetry a real signal has, run an ordinary
         * N-point inverse, and interleave.
         *
         * Twice the work of the clever version, on a transform this engine
         * asks for about thirty times per utterance. That is not a trade
         * worth thinking about, and being able to see it is correct is.
         *
         * Unnormalised deliberately: the caller's convention is that forward,
         * inverse, and a division by 2N is the identity. Since the forward
         * pass already carries vDSP's factor of two, leaving the inverse
         * unscaled is exactly what makes that true. */
        double *gr = (double *)malloc(sizeof(double) * n);
        double *gi = (double *)malloc(sizeof(double) * n);
        if (!gr || !gi) { free(gr); free(gi);
                          free(re); free(im); free(ar); free(ai); return; }
        gr[0] = re[0]; gi[0] = 0.0;              /* DC, real */
        gr[h] = im[0]; gi[h] = 0.0;              /* Nyquist, unpacked */
        for (k = 1; k < h; k++) {
            gr[k] = re[k];      gi[k] = im[k];
            gr[n - k] = re[k];  gi[n - k] = -im[k];   /* conjugate half */
        }
        fft_complex(gr, gi, n, +1);
        for (k = 0; k < h; k++) {
            io->realp[k * stride] = (float)gr[2 * k];
            io->imagp[k * stride] = (float)gr[2 * k + 1];
        }
        free(gr); free(gi);
    }
    free(re); free(im); free(ar); free(ai);
}

/* ctoz/ztoc: interleaved to split and back.  `ctoz` reads an ordinary array
 * as a run of complex pairs, so an N-sample real signal becomes N/2 slots
 * with the even samples in realp and the odd in imagp -- exactly the packing
 * `fft_zrip` wants. */
static void __cdecl sh_ctoz(const float *C, vdsp_stride IC,
                            split_complex *Z, vdsp_stride IZ, vdsp_length N)
{
    vdsp_length i;
    for (i = 0; i < N; i++) {
        Z->realp[i * IZ] = C[i * IC];
        Z->imagp[i * IZ] = C[i * IC + 1];
    }
}

static void __cdecl sh_ztoc(const split_complex *Z, vdsp_stride IZ,
                            float *C, vdsp_stride IC, vdsp_length N)
{
    vdsp_length i;
    for (i = 0; i < N; i++) {
        C[i * IC]     = Z->realp[i * IZ];
        C[i * IC + 1] = Z->imagp[i * IZ];
    }
}

/* vDSP_zvcmul: C = conj(A) * B, elementwise.
 *
 * **The conjugate is the whole point.**  Multiplying two spectra convolves;
 * multiplying by a conjugate correlates, and correlation is what a WSOLA
 * search wants.  Backwards, it mirrors the result and puts the peak at minus
 * the lag -- which would not crash, and would sound like a stutter. */
static void __cdecl sh_vDSP_zvcmul(const split_complex *A, vdsp_stride IA,
                                   const split_complex *B, vdsp_stride IB,
                                   const split_complex *C, vdsp_stride IC,
                                   vdsp_length N)
{
    vdsp_length i;
    for (i = 0; i < N; i++) {
        float ar = A->realp[i * IA], ai = A->imagp[i * IA];
        float br = B->realp[i * IB], bi = B->imagp[i * IB];
        C->realp[i * IC] = ar * br + ai * bi;
        C->imagp[i * IC] = ar * bi - ai * br;
    }
}

/* vDSP_maxvi(A, IA, C, I, N): the maximum, and **where it is**.
 *
 * The index is the half that matters -- it is the lag the correlation peaked
 * at, and so where the next grain is taken from. */
static void __cdecl sh_vDSP_maxvi(const float *A, vdsp_stride IA, float *C,
                                  vdsp_length *I, vdsp_length N)
{
    vdsp_length n, best = 0;
    float top;
    if (!A || N == 0) return;
    top = A[0];
    for (n = 1; n < N; n++) {
        float v = A[n * IA];
        if (v > top) { top = v; best = n; }
    }
    if (C) *C = top;
    /* **The index vDSP returns is strided**: `n * IA`, not the loop counter.
     * At stride one the two are the same, which is exactly why a test written
     * with stride one cannot tell them apart -- so the test uses a stride of
     * two as well. Wrong, this hands the correlation peak back at a fraction
     * of its real lag: not a crash, a grain taken from the wrong place. */
    if (I) *I = best * (vdsp_length)(IA < 0 ? -IA : IA);
}

/* vDSP_vma(A,IA,B,IB,C,IC,D,ID,N): D = A*B + C. */
static void __cdecl sh_vDSP_vma(const float *A, vdsp_stride IA,
                                const float *B, vdsp_stride IB,
                                const float *C, vdsp_stride IC,
                                float *D, vdsp_stride ID, vdsp_length N)
{
    vdsp_length n;
    for (n = 0; n < N; n++)
        D[n * ID] = A[n * IA] * B[n * IB] + C[n * IC];
}

/* vDSP_sve(A,IA,C,N): *C = sum of A -- svemg without the magnitudes. */
static void __cdecl sh_vDSP_sve(const float *A, vdsp_stride IA, float *C,
                                vdsp_length N)
{
    vdsp_length n;
    double sum = 0.0;
    for (n = 0; n < N; n++) sum += A[n * IA];
    if (C) *C = (float)sum;
}

/* vDSP_vclip(A,IA,LO,HI,C,IC,N): clamp into [*LO, *HI]. */
static void __cdecl sh_vDSP_vclip(const float *A, vdsp_stride IA,
                                  const float *LO, const float *HI,
                                  float *C, vdsp_stride IC, vdsp_length N)
{
    vdsp_length n;
    float lo = LO ? *LO : 0.0f, hi = HI ? *HI : 0.0f;
    for (n = 0; n < N; n++) {
        float v = A[n * IA];
        C[n * IC] = v < lo ? lo : (v > hi ? hi : v);
    }
}

/* vDSP_vramp(A,B,C,IC,N): C[n] = *A + n * *B. */
static void __cdecl sh_vDSP_vramp(const float *A, const float *B,
                                  float *C, vdsp_stride IC, vdsp_length N)
{
    vdsp_length n;
    double v = A ? *A : 0.0, step = B ? *B : 0.0;
    for (n = 0; n < N; n++, v += step) C[n * IC] = (float)v;
}

/* catlas_sset(N, alpha, X, incX): fill.  The BLAS-adjacent spelling Apple
 * ships rather than a vDSP one. */
static void __cdecl sh_catlas_sset(int N, float alpha, float *X, int incX)
{
    int n;
    for (n = 0; n < N; n++) X[n * incX] = alpha;
}

static float __cdecl sh_exp2f(float x) { return (float)pow(2.0, (double)x); }

/* CFAbsoluteTimeGetCurrent: seconds since 2001, as a double.  The engine
 * times itself with it, so only differences matter -- the epoch is honest
 * rather than important. */
static double __cdecl sh_CFAbsoluteTimeGetCurrent(void)
{
    FILETIME ft;
    unsigned long long t;
    GetSystemTimeAsFileTime(&ft);
    t = ((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    return (double)t / 1e7 - 12622780800.0;   /* 1601 -> 2001 */
}


/* ---- --vdsp-check -------------------------------------------------------
 *
 * The Accelerate routines, on fixed inputs, printed for numpy to check.
 *
 * Written to **stdout** for the reason `--dyld-check` is: `printf` is
 * redirected to stderr because serve mode puts PCM on stdout, and in a check
 * the report *is* the data.  Both sides build the inputs from the same
 * formula, so nothing has to be parsed back out of here except results.
 */
static void vd_emit(const char *name, const float *v, int n)
{
    int i;
    fprintf(stdout, "[vdsp] %s", name);
    for (i = 0; i < n; i++) fprintf(stdout, " %.9g", (double)v[i]);
    fprintf(stdout, "\n");
}

/* sin(0.1 i) + 0.5 cos(0.37 i) -- deliberately not a single tone, which would
 * land all its energy in one bin and hide almost any error. */
static void vd_signal(float *out, int n, int skip)
{
    int i;
    for (i = 0; i < n; i++) {
        double t = (double)(i + skip);
        out[i] = (float)(sin(0.1 * t) + 0.5 * cos(0.37 * t));
    }
}

static int vdsp_check(void)
{
    enum { NMAX = 256 };
    float x[NMAX], y[NMAX], z[NMAX], tmp[NMAX * 2];
    float rp[NMAX], ip[NMAX];
    split_complex sc, sa, sb, scc;
    float ra[NMAX], ia[NMAX], rb[NMAX], ib[NMAX], rc[NMAX], ic[NMAX];
    vdsp_length idx = 0;
    float val = 0.0f;
    int sizes[4], si;

    sc.realp = rp; sc.imagp = ip;

    /* ctoz and ztoc */
    vd_signal(x, 16, 0);
    sh_ctoz(x, 2, &sc, 1, 8);
    {
        float both[16];
        int i;
        for (i = 0; i < 8; i++) { both[i] = rp[i]; both[8 + i] = ip[i]; }
        vd_emit("ctoz", both, 16);
    }
    sh_ztoc(&sc, 1, z, 2, 8);
    vd_emit("ztoc", z, 16);

    /* the FFT, forward and round trip */
    sizes[0] = 8; sizes[1] = 16; sizes[2] = 64; sizes[3] = 256;
    for (si = 0; si < 4; si++) {
        int n = sizes[si], h = n / 2, i, log2n = 0;
        char name[32];
        void *setup;
        while ((1 << log2n) < n) log2n++;
        setup = sh_create_fftsetup((vdsp_length)log2n, 0);

        vd_signal(x, n, 0);
        sh_ctoz(x, 2, &sc, 1, h);
        sh_fft_zrip(setup, &sc, 1, (vdsp_length)log2n, FFT_FORWARD);
        {
            float both[NMAX];
            for (i = 0; i < h; i++) { both[i] = rp[i]; both[h + i] = ip[i]; }
            _snprintf(name, sizeof(name), "fftfwd%d", n);
            name[sizeof(name) - 1] = 0;
            vd_emit(name, both, n);
        }
        /* and back: forward, inverse, divide by 2n */
        sh_fft_zrip(setup, &sc, 1, (vdsp_length)log2n, FFT_INVERSE);
        for (i = 0; i < h; i++) {
            rp[i] /= (float)(2 * n);
            ip[i] /= (float)(2 * n);
        }
        sh_ztoc(&sc, 1, tmp, 2, h);
        _snprintf(name, sizeof(name), "fftrt%d", n);
        name[sizeof(name) - 1] = 0;
        vd_emit(name, tmp, n);
        sh_destroy_fftsetup(setup);
    }

    /* zvcmul, on two different signals */
    vd_signal(x, 8, 0);
    vd_signal(y, 8, 3);
    sa.realp = ra; sa.imagp = ia;
    sb.realp = rb; sb.imagp = ib;
    scc.realp = rc; scc.imagp = ic;
    sh_ctoz(x, 2, &sa, 1, 4);
    sh_ctoz(y, 2, &sb, 1, 4);
    sh_vDSP_zvcmul(&sa, 1, &sb, 1, &scc, 1, 4);
    {
        float both[8];
        int i;
        for (i = 0; i < 4; i++) { both[i] = rc[i]; both[4 + i] = ic[i]; }
        vd_emit("zvcmul", both, 8);
    }

    /* maxvi */
    vd_signal(x, 32, 0);
    sh_vDSP_maxvi(x, 1, &val, &idx, 32);
    {
        float pair[2];
        pair[0] = val;
        pair[1] = (float)idx;
        vd_emit("maxvi", pair, 2);
    }

    /* maxvi again, with a stride of two: the index vDSP returns is
     * n*IA, and at stride one that is indistinguishable from n. */
    {
        float pair[2];
        vd_signal(x, 64, 0);
        sh_vDSP_maxvi(x, 2, &val, &idx, 32);
        pair[0] = val;
        pair[1] = (float)idx;
        vd_emit("maxvi2", pair, 2);
    }

    /* vma: D = A*B + C */
    vd_signal(x, 8, 0);
    vd_signal(y, 8, 1);
    vd_signal(z, 8, 2);
    sh_vDSP_vma(x, 1, y, 1, z, 1, tmp, 1, 8);
    vd_emit("vma", tmp, 8);

    /* sve */
    vd_signal(x, 16, 0);
    sh_vDSP_sve(x, 1, &val, 16);
    vd_emit("sve", &val, 1);

    /* vclip */
    {
        float lo = -0.25f, hi = 0.75f;
        vd_signal(x, 8, 0);
        sh_vDSP_vclip(x, 1, &lo, &hi, tmp, 1, 8);
        vd_emit("vclip", tmp, 8);
    }

    /* vramp */
    {
        float start = 1.5f, step = 0.25f;
        sh_vDSP_vramp(&start, &step, tmp, 1, 8);
        vd_emit("vramp", tmp, 8);
    }

    /* catlas_sset */
    sh_catlas_sset(8, 3.5f, tmp, 1);
    vd_emit("sset", tmp, 8);

    return 0;
}
