/* Windows AAC selection. Choose at decoder initialization, before accepting
 * packets; never switch in the middle of an AAC stream and lose overlap.
 * Native Windows decoding stays preferred. A missing platform or unusable
 * transform selects the bundled Glint decoder for this host process.
 *
 * Namespace the two implementations at their existing narrow seam. Neither
 * needs to know about selection or duplicate converter/priming logic. */
#define aac_open mf_aac_open
#define aac_begin mf_aac_begin
#define aac_feed mf_aac_feed
#define aac_drain mf_aac_drain
#define aac_end_stream mf_aac_end_stream
#define aac_check mf_aac_check
#define aac_is_open mf_aac_is_open
#define aac_reset_decoder mf_aac_reset_decoder
#define aac_flush_now mf_aac_flush_now
#include "tiger_host_aac_mf.c"
#undef aac_open
#undef aac_begin
#undef aac_feed
#undef aac_drain
#undef aac_end_stream
#undef aac_check
#undef aac_is_open
#undef aac_reset_decoder
#undef aac_flush_now

#define aac_open glint_aac_open
#define aac_begin glint_aac_begin
#define aac_feed glint_aac_feed
#define aac_drain glint_aac_drain
#define aac_end_stream glint_aac_end_stream
#define aac_check glint_aac_check
#define aac_is_open glint_aac_is_open
#define aac_reset_decoder glint_aac_reset_decoder
#define aac_flush_now glint_aac_flush_now
#include "tiger_host_aac_glint.c"
#undef aac_open
#undef aac_begin
#undef aac_feed
#undef aac_drain
#undef aac_end_stream
#undef aac_check
#undef aac_is_open
#undef aac_reset_decoder
#undef aac_flush_now

typedef struct {
    int (*open)(void);
    void (*begin)(void);
    int (*feed)(const unsigned char *, unsigned);
    void (*drain)(void);
    void (*end_stream)(void);
    int (*is_open)(void);
    void (*reset)(void);
    void (*flush)(void);
    const char *name;
} aac_backend;

static const aac_backend mf_backend = {
    mf_aac_open, mf_aac_begin, mf_aac_feed, mf_aac_drain,
    mf_aac_end_stream, mf_aac_is_open, mf_aac_reset_decoder, mf_aac_flush_now,
    "media-foundation"
};
static const aac_backend glint_backend = {
    glint_aac_open, glint_aac_begin, glint_aac_feed, glint_aac_drain,
    glint_aac_end_stream, glint_aac_is_open, glint_aac_reset_decoder,
    glint_aac_flush_now, "glint"
};
static const aac_backend *g_aac_backend;
static int g_mf_unavailable;

static int aac_open(void)
{
    if (g_aac_backend) return g_aac_backend->open();
    /* Missing configuration is not evidence that Media Foundation is absent. */
    if (g_sc.asclen < 2) return 0;
    if (!g_mf_unavailable && mf_aac_open()) {
        g_aac_backend = &mf_backend;
        return 1;
    }
    mf_aac_reset_decoder(); /* Also release a partially configured transform. */
    g_mf_unavailable = 1;
    if (!glint_aac_open()) return 0;
    g_aac_backend = &glint_backend;
    if (g_verbose) fprintf(stderr, "  [aac] using bundled Glint fallback\n");
    return 1;
}
static int aac_is_open(void)
{ return g_aac_backend && g_aac_backend->is_open(); }
static void aac_reset_decoder(void)
{
    if (g_aac_backend) g_aac_backend->reset();
    g_aac_backend = NULL;
}
static void aac_begin(void) { g_aac_backend->begin(); }
static int aac_feed(const unsigned char *p, unsigned n)
{ return g_aac_backend->feed(p, n); }
static void aac_drain(void) { g_aac_backend->drain(); }
static void aac_end_stream(void) { g_aac_backend->end_stream(); }
static void aac_flush_now(void)
{ if (g_aac_backend) g_aac_backend->flush(); }
static const char *aac_backend_name(void)
{ return g_aac_backend ? g_aac_backend->name : "none"; }
static int aac_check(void)
{
    static const unsigned char asc[] = {0x13, 0x88};
    g_sc.rate = 22050;
    g_sc.channels = 1;
    memcpy(g_sc.asc, asc, sizeof asc);
    g_sc.asclen = sizeof asc;
    if (!aac_open()) {
        fprintf(stderr, "RESULT: no usable AAC decoder.\n");
        return 1;
    }
    fprintf(stderr, "RESULT: %s configured for 22050 Hz mono AAC-LC.\n",
            aac_backend_name());
    return 0;
}

