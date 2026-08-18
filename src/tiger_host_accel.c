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
    if (!A || !B || !C || !D || !E) return;
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
    const double scale = (flag & VDSP_HANN_NORM) ? 0.8165 : 0.5;
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
