package com.pantheraspeech.tts;
interface IPantheraWorker {
    int open(String engine, String dictionary);
    int start(String voice, int creator, int voiceId, in byte[] text, int wpm,
              int volume, String generation, String numbers);
    byte[] pull(int capacity);
    void stop();
    void finish();
}
