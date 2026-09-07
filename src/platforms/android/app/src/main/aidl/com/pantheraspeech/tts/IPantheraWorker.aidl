package com.pantheraspeech.tts;
interface IPantheraWorker {
    int open(String engine, String dictionary, String phrasing, int inflection);
    void shutdown();
    int start(String voice, int creator, int voiceId, in byte[] text, int wpm,
              int volume, String generation, String numbers, boolean expandAbbreviations, int inflection);
    byte[] pull(int capacity);
    void finish();
}
