/* Experimental AAC-LC backend. Only compiled by the isolated research build. */
extern void *pt_glint_open(void);
extern void pt_glint_close(void *);
extern int pt_glint_decode(void *, const unsigned char *, int, short *, unsigned, unsigned);
static void *g_glint;
static unsigned g_glint_index;
static int aac_is_open(void) { return g_glint != NULL; }
static void aac_reset_decoder(void) {
    pt_glint_close(g_glint);
    g_glint = NULL;
}
static int aac_open(void) {
    static const unsigned rates[] = {96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350};
    unsigned object, channels, index;
    if (g_glint) return 1;
    if (g_sc.asclen != 2) return 0;
    object = g_sc.asc[0] >> 3;
    index = ((g_sc.asc[0] & 7) << 1) | (g_sc.asc[1] >> 7);
    channels = (g_sc.asc[1] >> 3) & 15;
    /* Reject unsupported ASC rather than silently reinterpret it as LC. */
    if (object != 2 || index >= 13 || (g_sc.asc[1] & 7) || channels != 1 ||
        channels != g_sc.channels || rates[index] != g_sc.rate) {
        fprintf(stderr, "[aac] Glint unsupported ASC %02x %02x / %u Hz %u ch\n", g_sc.asc[0], g_sc.asc[1], g_sc.rate, g_sc.channels);
        return 0;
    }
    g_glint_index = index;
    g_glint = pt_glint_open();
    return g_glint != NULL;
}
static int aac_feed(const unsigned char *data, unsigned len) {
    unsigned char packet[8192];
    short pcm[2048];
    unsigned size = len + 7;
    int n;
    double t0;
    if (!g_glint || !len || len > 8184) return 0;
    packet[0] = 0xff; packet[1] = 0xf1;
    packet[2] = (unsigned char)((1 << 6) | (g_glint_index << 2));
    packet[3] = (unsigned char)((1 << 6) | ((size >> 11) & 3));
    packet[4] = (unsigned char)(size >> 3);
    packet[5] = (unsigned char)(((size & 7) << 5) | 31);
    packet[6] = 0xfc;
    memcpy(packet + 7, data, len);
    t0 = wall_ms();
    ++g_aac_units;
    n = pt_glint_decode(g_glint, packet, (int)size, pcm, g_sc.rate, g_sc.channels);
    g_aac_ms += wall_ms() - t0;
    if (n < 0) {
        if (!g_sc.quiet++) fprintf(stderr, "[aac] Glint refused AAC packet (%d)\n", n);
        return 0;
    }
    pcm_append((const unsigned char *)pcm, (unsigned)n * 2);
    return 1;
}
static void aac_drain(void) { }
static void aac_end_stream(void) { }
static void aac_flush_now(void) {
    if (!g_glint) return;
    aac_reset_decoder();
    (void)aac_open();
}
static void aac_begin(void) {
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    aac_flush_now();
}
static int aac_check(void) {
    void *p = pt_glint_open();
    if (!p) return 2;
    pt_glint_close(p);
    fprintf(stderr, "RESULT: experimental Glint decoder linked.\n");
    return 0;
}
