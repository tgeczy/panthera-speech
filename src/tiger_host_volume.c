/* NVDA's measured volume scale, shared by in-process front ends.
 * Tables are checked against the Python driver by tools/volume_oracle.py.
 * Callers hold synthesis ownership when changing the preference. Existing
 * pipe clients keep sending their own volume commands without interference. */
static int g_volume_level = -1;
static char g_volume_generation[32];
static const struct { const char *gen, *voice; int norm; } volume_norms[] = {
    {"leopard", "Agnes", 100},
    {"leopard", "Albert", 170},
    {"leopard", "Alex", 180},
    {"leopard", "BadNews", 180},
    {"leopard", "Bahh", 170},
    {"leopard", "Bells", 170},
    {"leopard", "Boing", 170},
    {"leopard", "Bruce", 100},
    {"leopard", "Bubbles", 170},
    {"leopard", "Cellos", 170},
    {"leopard", "Deranged", 170},
    {"leopard", "Fred", 180},
    {"leopard", "GoodNews", 180},
    {"leopard", "Hysterical", 170},
    {"leopard", "Junior", 180},
    {"leopard", "Kathy", 173},
    {"leopard", "Organ", 170},
    {"leopard", "Princess", 170},
    {"leopard", "Ralph", 170},
    {"leopard", "Trinoids", 170},
    {"leopard", "Vicki", 120},
    {"leopard", "Victoria", 100},
    {"leopard", "Whisper", 180},
    {"leopard", "Zarvox", 170},
    {"snowleopard", "Agnes", 100},
    {"snowleopard", "Albert", 170},
    {"snowleopard", "Alex", 146},
    {"snowleopard", "BadNews", 180},
    {"snowleopard", "Bahh", 170},
    {"snowleopard", "Bells", 170},
    {"snowleopard", "Boing", 170},
    {"snowleopard", "Bruce", 100},
    {"snowleopard", "Bubbles", 170},
    {"snowleopard", "Cellos", 170},
    {"snowleopard", "Deranged", 170},
    {"snowleopard", "Fred", 180},
    {"snowleopard", "GoodNews", 180},
    {"snowleopard", "Hysterical", 170},
    {"snowleopard", "Junior", 180},
    {"snowleopard", "Kathy", 173},
    {"snowleopard", "Organ", 170},
    {"snowleopard", "Princess", 170},
    {"snowleopard", "Ralph", 171},
    {"snowleopard", "Trinoids", 170},
    {"snowleopard", "Vicki", 119},
    {"snowleopard", "Victoria", 100},
    {"snowleopard", "Whisper", 180},
    {"snowleopard", "Zarvox", 170},
    {"lion", "Agnes", 100},
    {"lion", "Albert", 170},
    {"lion", "Alex", 119},
    {"lion", "BadNews", 180},
    {"lion", "Bahh", 170},
    {"lion", "Bells", 170},
    {"lion", "Boing", 180},
    {"lion", "Bruce", 100},
    {"lion", "Bubbles", 170},
    {"lion", "Cellos", 175},
    {"lion", "Deranged", 170},
    {"lion", "Fred", 180},
    {"lion", "GoodNews", 169},
    {"lion", "Hysterical", 170},
    {"lion", "Junior", 177},
    {"lion", "Kathy", 172},
    {"lion", "Organ", 167},
    {"lion", "Princess", 165},
    {"lion", "Ralph", 174},
    {"lion", "Trinoids", 170},
    {"lion", "Vicki", 117},
    {"lion", "Victoria", 100},
    {"lion", "Whisper", 180},
    {"lion", "Zarvox", 170},
};
void panthera_set_volume(int level, const char *generation)
{
    g_volume_level = level < 0 ? -1 : (level > 100 ? 100 : level);
    _snprintf(g_volume_generation, sizeof(g_volume_generation), "%s",
              generation ? generation : "");
    g_volume_generation[sizeof(g_volume_generation)-1] = 0;
}
static int volume_milli(int level, const char *gen, const char *voice)
{
    unsigned i;
    int norm = 100, divisor = 90, value;
    if (level < 0) level = 0;
    if (level > 100) level = 100;
    if (!gen) gen = "";
    if (!strcmp(gen, "tiger")) divisor = 100;
    for (i = 0; i < sizeof(volume_norms)/sizeof(volume_norms[0]); i++)
        if (!strcmp(gen, volume_norms[i].gen) && !strcmp(voice, volume_norms[i].voice)) {
            norm = volume_norms[i].norm; break;
        }
    value = (norm * level * 10 + divisor/2) / divisor;
    return value > 2000 ? 2000 : value;
}
static int speak_with_volume(const speech_api *api, void *chan,
        const char *text, size_t len, const char *voiceDir)
{
    const char *voice = voiceDir, *p;
    char name[128];
    int milli, result;
    if (g_volume_level < 0) return speak_text(api, chan, text, len);
    for (p = voiceDir; *p; p++) if (*p == '/' || *p == '\\') voice = p+1;
    _snprintf(name, sizeof(name), "%s", voice);
    name[sizeof(name)-1] = 0;
    p = strstr(name, ".SpeechVoice");
    if (p) name[p-name] = 0;
    milli = volume_milli(g_volume_level, g_volume_generation, name);
    /* Use the Speech Manager's volume parameter, like rate. The embedded
     * volm command alone did not change PCM under emulation; the direct
     * parameter is read back correctly and passes mute/recovery tests.
     * Reapply every utterance, including after a voice change or mute. */
    result = set_param(api, chan, PARAM_VOLUME, (unsigned)(milli * 65536u / 1000));
    if (result) return result;
    return speak_text(api, chan, text, len);
}
