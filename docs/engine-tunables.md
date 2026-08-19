# The engine's tuning parameters

Leopard's MacinTalk asks for **283 named settings** while it speaks, and gets
silence for every one of them, because the host answers
`CFPreferencesCopyAppValue` with NULL. It then uses its compiled-in defaults.

This is the list, with the address of each name inside the i386 slice, and
whether Lion still has it. **Nothing here is Apple's code or data** — these are
names and offsets, the same kind of fact as a symbol table, and every one can
be regenerated from your own copy with the script at the bottom.

They matter because two of them decide the thing listeners complain about
most: where the voice breaks a phrase.

---

## How the engine reads them

The lookup is **override dictionary → CFPreferences → built-in default**, and
in this host the first two are shims (`src/tiger_host_cf.c`). The engine
type-checks what comes back — it imports `CFGetTypeID`, `CFNumberGetTypeID`,
`CFBooleanGetTypeID`, `CFNumberGetValue` and `CFBooleanGetValue` — so an answer
has to be a real `CFNumber` or `CFBoolean`, not a string.

Two preference domains are named in the binary:

```
com.apple.speech.MT3Engine
com.apple.speech.synthesis.debugparam
```

On a real Mac those are `defaults`-writable, which is what these parameters
are for: they are Apple's own tuning hooks, not a private interface.

**Leopard ships no values for them.** Searched every file on the 10.5 install
DVD — 430,328 files, 9.8 GB decompressed — and these names appear only inside
the MacinTalk binary itself. There is no plist to find.

## The ones that shape phrasing

| name | what it plausibly governs |
|---|---|
| `Boundaries.PhrThreshold` | how strong a candidate must be before it becomes a phrase boundary |
| `Boundaries.SilThreshold` | the same for inserting a silence |
| `Boundaries.Debug` | makes the engine report what it decided |
| `BreathIntake.PhraseLength` / `.PhraseSpacing` / `.PhraseLimit` | where breaths go inside a phrase |
| `BreathIntake.SentenceLength` / `.SentenceLimit` / `.ParagraphAdjustment` | the same at sentence and paragraph scale |
| `CommaBoundary`, `PeriodBoundary`, `FinalBoundary`, `QuestBoundary`, `WHQuestBoundary`, `ExclamBoundary` | strength per punctuation mark |
| `WordCost.KeepWithNext` / `.KeepWithPrev` | the engine's own name for "do not split these" |
| `Frontend.BoundaryModel`, `Frontend.PitchDWIM` | front-end model selection |

The reading in the right-hand column is inference from the names, not from
disassembly. Treat it as a starting point.

## Which engines have them

**Tiger's MacinTalk 3.3 has none of these** — no `__cfstring` section at all.
They arrived with Leopard, alongside Alex and the concatenative `meow` engine.

Lion adds 44 and drops 3 (`PitchDecrease.Linear`, `PitchDecrease.Target`,
`TopicTracking.Window`); the rest of its "dropped" entries are the dictionary
resources it renamed with an `Eng` suffix. Nothing in the boundary or breath
families changes between 10.5 and 10.7 — **Leopard already has every phrasing
knob Lion does.** What Lion genuinely adds is part-of-speech driven prosody
(`PartOfSpeech`, `ToBIPitch.DowngradeVerbs`, `ToBIPitch.VerbDowngradeFactor`)
and a set of WSOLA and amplitude parameters that Leopard hardcodes.

## Regenerating this list

Nothing in this file needs to be trusted; it can be rebuilt from the engine
you extracted yourself. The names live in the `__cfstring` section, a table of
16-byte records whose third word points at the C string:

```
py -3 tools/cfstrings.py \
    <your tree>/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk
```

Addresses below are for the i386 slice of Leopard 10.5's engine and will
differ for another build.

---

## The full list, by namespace

### (ungrouped)  (171)

| address | name | in Lion |
|---|---|---|
| `0x0009786c` | `/` | **no** |
| `0x00097a9c` | `Accent` | yes |
| `0x00096f8c` | `BackupHighPhraseProm` | yes |
| `0x00096f9c` | `BackupLowPhraseProm` | yes |
| `0x000977ec` | `CartLite` | **no** |
| `0x000977fc` | `CartNames` | **no** |
| `0x00097a3c` | `Children` | yes |
| `0x00096fac` | `ClosestPhraseAccentHigh` | yes |
| `0x00096fbc` | `ClosestPhraseAccentLow` | yes |
| `0x00096fcc` | `CommaBoundary` | yes |
| `0x00097b8c` | `Compound` | yes |
| `0x000980fc` | `Default` | yes |
| `0x00096fdc` | `DownsteppedHighStarLegIntercept` | yes |
| `0x00096fec` | `DownsteppedHighStarLegSlope` | yes |
| `0x00097bac` | `End` | yes |
| `0x00096ffc` | `ExclamBoundary` | yes |
| `0x0009700c` | `ExclamLastAccentProm` | yes |
| `0x0009701c` | `ExplicitAccentProm` | yes |
| `0x00097b0c` | `FinalBoundary` | yes |
| `0x0009702c` | `FinalLoweringDuration` | yes |
| `0x0009703c` | `FinalLoweringRatio` | yes |
| `0x0009704c` | `FirstAccentProm` | yes |
| `0x0009705c` | `FirstAccentPromForTwoAccentsPhrase` | yes |
| `0x00097a4c` | `FirstPhoneme` | yes |
| `0x00097afc` | `H%` | yes |
| `0x00097b3c` | `H-` | yes |
| `0x0009706c` | `HighAlternatingProm` | yes |
| `0x0009707c` | `HighFinalBoundaryProm` | yes |
| `0x0009708c` | `HighFinalBoundaryWidth` | yes |
| `0x0009709c` | `HighPlusDownstepIntercept` | yes |
| `0x000970ac` | `HighPlusDownstepSlope` | yes |
| `0x000970bc` | `HighPlusDownsteppedHighStarWidth` | yes |
| `0x000970cc` | `HighPlusIntercept` | yes |
| `0x000970dc` | `HighPlusLowStarWidth` | yes |
| `0x000970ec` | `HighPlusSlope` | yes |
| `0x000970fc` | `HighStarLegIntercept` | yes |
| `0x0009710c` | `HighStarLegSlope` | yes |
| `0x0009711c` | `HighStarWidth` | yes |
| `0x0009780c` | `Homophones` | **no** |
| `0x00097a6c` | `Index` | yes |
| `0x00097acc` | `Instance` | yes |
| `0x0009712c` | `IntermediateNuclearTail` | yes |
| `0x00097b2c` | `IntermediatePhrase` | yes |
| `0x0009713c` | `IntonationalNuclearTail` | yes |
| `0x00097aec` | `IntonationalPhrase` | yes |
| `0x00097a2c` | `IsA` | yes |
| `0x00097b1c` | `L%` | yes |
| `0x00097b5c` | `L-` | yes |
| `0x0009714c` | `LastAccentProm` | yes |
| `0x00097aac` | `Left` | yes |
| `0x0009715c` | `LongURLBoost` | yes |
| `0x0009716c` | `LowAlternatingProm` | yes |
| `0x0009717c` | `LowPhraseAccentRecoverSlopeWidth` | yes |
| `0x0009718c` | `LowPlusHighStarWidth` | yes |
| `0x0009719c` | `LowPlusIntercept` | yes |
| `0x000971ac` | `LowPlusSlope` | yes |
| `0x000971bc` | `LowPlusWidth` | yes |
| `0x000971cc` | `LowStarLegIntercept` | yes |
| `0x000971dc` | `LowStarLegSlope` | yes |
| `0x000971ec` | `LowStarPlusHighWidth` | yes |
| `0x000971fc` | `LowStarPlusLegDelay` | yes |
| `0x0009720c` | `LowStarPlusLegIntercept` | yes |
| `0x0009721c` | `LowStarPlusLegSlope` | yes |
| `0x0009722c` | `LowStarWidth` | yes |
| `0x00097bcc` | `MEOW_DEBUG` | yes |
| `0x00097bdc` | `MTX_DEBUG` | yes |
| `0x00097a8c` | `Name` | yes |
| `0x0009723c` | `NonQuestPhraseProm` | yes |
| `0x0009784c` | `PCMWave` | yes |
| `0x0009724c` | `ParagraphInitialBoost` | yes |
| `0x0009725c` | `ParagraphRangePhrasalDownStep` | yes |
| `0x0009726c` | `ParenBoost` | yes |
| `0x0009727c` | `PeriodBoundary` | yes |
| `0x0009728c` | `PerturbConsNoStress` | yes |
| `0x0009729c` | `PerturbConsPrimaryStress` | yes |
| `0x000972ac` | `PerturbConsSecondaryStress` | yes |
| `0x000972bc` | `PerturbPostConsFrames` | yes |
| `0x000972cc` | `PerturbPreConsFrames` | yes |
| `0x000972dc` | `PerturbSonorClosureIntercept` | yes |
| `0x000972ec` | `PerturbSonorClosureSlope` | yes |
| `0x000972fc` | `PerturbSonorReleaseIntercept` | yes |
| `0x0009730c` | `PerturbSonorReleaseSlope` | yes |
| `0x0009731c` | `PerturbUObstClosureIntercept` | yes |
| `0x0009732c` | `PerturbUObstClosureSlope` | yes |
| `0x0009733c` | `PerturbUObstReleaseIntercept` | yes |
| `0x0009734c` | `PerturbUObstReleaseSlope` | yes |
| `0x0009735c` | `PerturbVObstClosureIntercept` | yes |
| `0x0009736c` | `PerturbVObstClosureSlope` | yes |
| `0x0009737c` | `PerturbVObstReleaseIntercept` | yes |
| `0x0009738c` | `PerturbVObstReleaseSlope` | yes |
| `0x0009739c` | `PerturbVowelNoStress` | yes |
| `0x000973ac` | `PerturbVowelPrimaryStress` | yes |
| `0x000973bc` | `PerturbVowelSecondaryStress` | yes |
| `0x000973cc` | `Perturb_AA` | yes |
| `0x000973dc` | `Perturb_AE` | yes |
| `0x000973ec` | `Perturb_AH` | yes |
| `0x000973fc` | `Perturb_AO` | yes |
| `0x0009740c` | `Perturb_AR` | yes |
| `0x0009741c` | `Perturb_AW` | yes |
| `0x0009742c` | `Perturb_AX` | yes |
| `0x0009743c` | `Perturb_AY` | yes |
| `0x0009744c` | `Perturb_EH` | yes |
| `0x0009745c` | `Perturb_EL` | yes |
| `0x0009746c` | `Perturb_EN` | yes |
| `0x0009747c` | `Perturb_ER` | yes |
| `0x0009748c` | `Perturb_EY` | yes |
| `0x0009749c` | `Perturb_IH` | yes |
| `0x000974ac` | `Perturb_IR` | yes |
| `0x000974bc` | `Perturb_IX` | yes |
| `0x000974cc` | `Perturb_IY` | yes |
| `0x000974dc` | `Perturb_LX` | yes |
| `0x000974ec` | `Perturb_OR` | yes |
| `0x000974fc` | `Perturb_OW` | yes |
| `0x0009750c` | `Perturb_OY` | yes |
| `0x0009751c` | `Perturb_RX` | yes |
| `0x0009752c` | `Perturb_UH` | yes |
| `0x0009753c` | `Perturb_UR` | yes |
| `0x0009754c` | `Perturb_UW` | yes |
| `0x0009755c` | `Perturb_XR` | yes |
| `0x0009756c` | `Perturb_YU` | yes |
| `0x00097a7c` | `Phoneme` | yes |
| `0x0009782c` | `PhonemeSymbols` | **no** |
| `0x00097b4c` | `PhraseAccent` | yes |
| `0x0009757c` | `PhraseAccentRangeHigh` | yes |
| `0x0009758c` | `PhraseAccentRangeLow` | yes |
| `0x0009759c` | `PitchSmoothWindow` | yes |
| `0x000975ac` | `PlusDownsteppedHighStarLegDelay` | yes |
| `0x000975bc` | `PlusHighIntercept` | yes |
| `0x000975cc` | `PlusHighSlope` | yes |
| `0x000975dc` | `PlusHighStarIntercept` | yes |
| `0x000975ec` | `PlusHighStarSlope` | yes |
| `0x000975fc` | `PlusHighStarWidth` | yes |
| `0x0009760c` | `PlusLowStarLegDelay` | yes |
| `0x0009761c` | `PlusLowStarLegIntercept` | yes |
| `0x0009762c` | `PlusLowStarLegSlope` | yes |
| `0x000977cc` | `PrefixDictionary` | **no** |
| `0x0009763c` | `QuestBoundary` | yes |
| `0x0009764c` | `QuestPhraseProm` | yes |
| `0x0009765c` | `QuoteBoost` | yes |
| `0x0009766c` | `RefProportion` | yes |
| `0x00097abc` | `Right` | yes |
| `0x00097a5c` | `SecondPhoneme` | yes |
| `0x00097adc` | `Sentence` | yes |
| `0x0009767c` | `Sep1Prom` | yes |
| `0x0009768c` | `Sep2Prom` | yes |
| `0x0009769c` | `Sep3Prom` | yes |
| `0x000976ac` | `Sep4Prom` | yes |
| `0x000976bc` | `Sep5Prom` | yes |
| `0x000976cc` | `Sep6Prom` | yes |
| `0x000976dc` | `Sep7Prom` | yes |
| `0x000976ec` | `ShortURLBoost` | yes |
| `0x000976fc` | `SlopeDelayRange` | yes |
| `0x0009770c` | `SlopeEarly` | yes |
| `0x0009771c` | `SlopeEarlyDelay` | yes |
| `0x0009772c` | `SlopeInterAccentDistance` | yes |
| `0x0009773c` | `SlopeNonIntonationalPhraseFinal` | yes |
| `0x0009774c` | `SlopeNonNuclear` | yes |
| `0x0009775c` | `SlopeRange` | yes |
| `0x0009776c` | `SlopeWordEnd` | yes |
| `0x00097b9c` | `Start` | yes |
| `0x00097bbc` | `Syllable` | yes |
| `0x000977dc` | `SymbolDictionary` | **no** |
| `0x00097b7c` | `Text` | yes |
| `0x0009781c` | `Tuples` | **no** |
| `0x0009777c` | `URLDownStep` | yes |
| `0x000980ec` | `Value` | yes |
| `0x0009783c` | `VoiceDescription` | yes |
| `0x0009778c` | `VoicePitchFloor` | yes |
| `0x0009779c` | `VoicePitchRange` | yes |
| `0x000977ac` | `WHQuestBoundary` | yes |
| `0x00097b6c` | `Word` | yes |

### Blending  (1)

| address | name | in Lion |
|---|---|---|
| `0x00097e3c` | `Blending.Unvoiced.Window` | yes |

### Boundaries  (3)

| address | name | in Lion |
|---|---|---|
| `0x0009810c` | `Boundaries.Debug` | yes |
| `0x0009811c` | `Boundaries.PhrThreshold` | yes |
| `0x0009812c` | `Boundaries.SilThreshold` | yes |

### BreathIntake  (6)

| address | name | in Lion |
|---|---|---|
| `0x00097fbc` | `BreathIntake.ParagraphAdjustment` | yes |
| `0x00097f7c` | `BreathIntake.PhraseLength` | yes |
| `0x00097fac` | `BreathIntake.PhraseLimit` | yes |
| `0x00097f9c` | `BreathIntake.PhraseSpacing` | yes |
| `0x00097f6c` | `BreathIntake.SentenceLength` | yes |
| `0x00097fcc` | `BreathIntake.SentenceLimit` | yes |

### Demi  (1)

| address | name | in Lion |
|---|---|---|
| `0x000980cc` | `Demi.Threshold` | yes |

### DemiCost  (2)

| address | name | in Lion |
|---|---|---|
| `0x00097fec` | `DemiCost.SpectralWeight` | yes |
| `0x00097fdc` | `DemiCost.UnitWeight` | yes |

### Diphone  (1)

| address | name | in Lion |
|---|---|---|
| `0x000980dc` | `Diphone.Threshold` | yes |

### DiphoneCost  (2)

| address | name | in Lion |
|---|---|---|
| `0x0009800c` | `DiphoneCost.SpectralWeight` | yes |
| `0x00097ffc` | `DiphoneCost.UnitWeight` | yes |

### DiphoneGlue  (1)

| address | name | in Lion |
|---|---|---|
| `0x00097f8c` | `DiphoneGlue.ForceAtBoundary` | yes |

### Frontend  (3)

| address | name | in Lion |
|---|---|---|
| `0x0009785c` | `Frontend.BoundaryModel` | yes |
| `0x0009787c` | `Frontend.DebugPOS` | yes |
| `0x0009788c` | `Frontend.PitchDWIM` | yes |

### PitchAssembly  (14)

| address | name | in Lion |
|---|---|---|
| `0x00097eec` | `PitchAssembly.Bandwidth` | yes |
| `0x00097e5c` | `PitchAssembly.EnforceTunes` | yes |
| `0x00097f0c` | `PitchAssembly.FinalSlack` | yes |
| `0x00097e9c` | `PitchAssembly.LinearPitchUseMedian` | yes |
| `0x00097e7c` | `PitchAssembly.LinearSlack` | yes |
| `0x00097efc` | `PitchAssembly.LinearSlack` | yes |
| `0x00097ebc` | `PitchAssembly.NumPPConsidered` | yes |
| `0x00097eac` | `PitchAssembly.PPRangeThreshold` | yes |
| `0x00097ecc` | `PitchAssembly.PPThreshold` | yes |
| `0x00097e4c` | `PitchAssembly.PowerThreshold` | yes |
| `0x00097edc` | `PitchAssembly.PowerThreshold` | yes |
| `0x00097f1c` | `PitchAssembly.SilenceConform` | yes |
| `0x00097e6c` | `PitchAssembly.SilenceGlue` | yes |
| `0x00097f2c` | `PitchAssembly.UnvoicedBackoff` | yes |

### PitchChange  (1)

| address | name | in Lion |
|---|---|---|
| `0x00097e2c` | `PitchChange.DetectExcitation` | yes |

### PitchDecrease  (4)

| address | name | in Lion |
|---|---|---|
| `0x00097dec` | `PitchDecrease.Linear` | **no** |
| `0x00097e1c` | `PitchDecrease.MinWin` | yes |
| `0x00097ddc` | `PitchDecrease.Target` | **no** |
| `0x00097e0c` | `PitchDecrease.Window` | yes |

### PitchIncrease  (1)

| address | name | in Lion |
|---|---|---|
| `0x00097dfc` | `PitchIncrease.Window` | yes |

### SVDDistance  (1)

| address | name | in Lion |
|---|---|---|
| `0x000979dc` | `SVDDistance.NumPitchPeriodsForSVDDistance` | yes |

### Search  (2)

| address | name | in Lion |
|---|---|---|
| `0x000979bc` | `Search.Asynchronous` | yes |
| `0x000979cc` | `Search.UseDiphoneGlue` | yes |

### SegmentAssembly  (7)

| address | name | in Lion |
|---|---|---|
| `0x00097dbc` | `SegmentAssembly.DurationSlack` | yes |
| `0x00097f3c` | `SegmentAssembly.LinearPitch` | yes |
| `0x00097f5c` | `SegmentAssembly.PitchSlack` | yes |
| `0x0009789c` | `SegmentAssembly.PostProcessDurationModification` | yes |
| `0x00097f4c` | `SegmentAssembly.SlackWindow` | yes |
| `0x00097dcc` | `SegmentAssembly.SmoothDuration` | yes |
| `0x00097e8c` | `SegmentAssembly.WordLinearPitch` | yes |

### ToBIPitch  (16)

| address | name | in Lion |
|---|---|---|
| `0x0009792c` | `ToBIPitch.DeclFinalRaisingDuration` | yes |
| `0x0009793c` | `ToBIPitch.DeclFinalRaisingRatio` | yes |
| `0x0009796c` | `ToBIPitch.ExclamFinalRaisingDuration` | yes |
| `0x0009797c` | `ToBIPitch.ExclamFinalRaisingRatio` | yes |
| `0x0009791c` | `ToBIPitch.ExclamPromBoost` | yes |
| `0x000978cc` | `ToBIPitch.HighBoundaryWidth` | yes |
| `0x000978dc` | `ToBIPitch.HighFinalProm` | yes |
| `0x000978fc` | `ToBIPitch.HighPhraseProm` | yes |
| `0x000978ec` | `ToBIPitch.LowFinalProm` | yes |
| `0x0009790c` | `ToBIPitch.LowPhraseProm` | yes |
| `0x000978ac` | `ToBIPitch.ParagraphInitialBoost` | yes |
| `0x000978bc` | `ToBIPitch.ParagraphRangePhrasalDownStep` | yes |
| `0x0009798c` | `ToBIPitch.QuestFinalRaisingDuration` | yes |
| `0x0009799c` | `ToBIPitch.QuestFinalRaisingRatio` | yes |
| `0x0009794c` | `ToBIPitch.WHQuestFinalRaisingDuration` | yes |
| `0x0009795c` | `ToBIPitch.WHQuestFinalRaisingRatio` | yes |

### TopicTracking  (1)

| address | name | in Lion |
|---|---|---|
| `0x000977bc` | `TopicTracking.Window` | **no** |

### UnitCost  (27)

| address | name | in Lion |
|---|---|---|
| `0x00097bfc` | `UnitCost.AccentCostWeight` | yes |
| `0x00097c6c` | `UnitCost.BreathProportion` | yes |
| `0x00097c4c` | `UnitCost.BreathWeight` | yes |
| `0x00097d4c` | `UnitCost.Duration.Exponent` | yes |
| `0x00097d5c` | `UnitCost.Duration.Pivot` | yes |
| `0x00097d6c` | `UnitCost.Duration.PowerStrategy` | yes |
| `0x00097c0c` | `UnitCost.DurationWeight` | yes |
| `0x00097c5c` | `UnitCost.ExcessiveBreath` | yes |
| `0x00097c7c` | `UnitCost.MaxBreathLength` | yes |
| `0x00097c3c` | `UnitCost.MissingWeight` | yes |
| `0x00097d7c` | `UnitCost.Pitch.Exponent` | yes |
| `0x00097d8c` | `UnitCost.Pitch.Pivot` | yes |
| `0x00097d9c` | `UnitCost.Pitch.PowerStrategy` | yes |
| `0x00097c9c` | `UnitCost.UnvoicedPenalty` | yes |
| `0x00097c8c` | `UnitCost.UnvoicedPitchCost` | yes |
| `0x00097c2c` | `UnitCost.UnvoicedWordCost` | yes |
| `0x00097d0c` | `UnitCost.UseRMS` | yes |
| `0x00097ccc` | `UnitCost.UseWindow` | yes |
| `0x00097c1c` | `UnitCost.VoicedPitchWeight` | yes |
| `0x00097cbc` | `UnitCost.WindowSlope` | yes |
| `0x00097cac` | `UnitCost.WindowTrough` | yes |
| `0x00097cdc` | `UnitCost.WordDuration.Exponent` | yes |
| `0x00097cec` | `UnitCost.WordDuration.Pivot` | yes |
| `0x00097cfc` | `UnitCost.WordDuration.PowerStrategy` | yes |
| `0x00097d1c` | `UnitCost.WordPitch.Exponent` | yes |
| `0x00097d2c` | `UnitCost.WordPitch.Pivot` | yes |
| `0x00097d3c` | `UnitCost.WordPitch.PowerStrategy` | yes |

### Voice  (5)

| address | name | in Lion |
|---|---|---|
| `0x000979ec` | `Voice.DebugHomographs` | yes |
| `0x00097dac` | `Voice.MapSamples` | yes |
| `0x00097a0c` | `Voice.Preload` | yes |
| `0x000979fc` | `Voice.PreloadDemis` | yes |
| `0x00097a1c` | `Voice.TrackDecodingRatio` | yes |

### Word  (1)

| address | name | in Lion |
|---|---|---|
| `0x000980ac` | `Word.Threshold` | yes |

### WordCost  (10)

| address | name | in Lion |
|---|---|---|
| `0x0009805c` | `WordCost.ContextSubst` | yes |
| `0x0009808c` | `WordCost.InnerMismatch` | yes |
| `0x0009809c` | `WordCost.InstanceMismatch` | yes |
| `0x0009804c` | `WordCost.KeepWithNext` | yes |
| `0x0009806c` | `WordCost.KeepWithPrev` | yes |
| `0x000980bc` | `WordCost.LeftBias` | yes |
| `0x0009802c` | `WordCost.LengthWeight` | yes |
| `0x0009807c` | `WordCost.OuterMismatch` | yes |
| `0x0009803c` | `WordCost.PhonMismatch` | yes |
| `0x0009801c` | `WordCost.UnitWeight` | yes |

### com  (2)

| address | name | in Lion |
|---|---|---|
| `0x000979ac` | `com.apple.speech.MT3Engine` | **no** |
| `0x00097bec` | `com.apple.speech.synthesis.debugparam` | yes |

## The 44 Lion adds

| address | name |
|---|---|
| `0x000aa7b8` | `AmplitudeNormalization` |
| `0x000aa898` | `AmplitudeNormalization.MaxScale` |
| `0x000aa7d8` | `AmplitudeNormalization.MinPP` |
| `0x000aa888` | `AmplitudeNormalization.MinScale` |
| `0x000aa7c8` | `AmplitudeNormalization.ScaleThreshold` |
| `0x000aa8c8` | `Blending.Log` |
| `0x000aaaa8` | `Cost.UseOptimalWeighting` |
| `0x000aaab8` | `DemiCost.OptimalWeight` |
| `0x000aaae8` | `DiphoneCost.OptimalWeight` |
| `0x000aaa18` | `DiphoneGlue.ForceAtQX` |
| `0x000aa8a8` | `DiphoneGlue.ForcePitch` |
| `0x000aaa98` | `DumpOptCosts` |
| `0x000aa1d8` | `Frontend.RhotacizeAX` |
| `0x000aa1c8` | `Frontend.SILPhrase` |
| `0x000aa1a8` | `Frontend.Singing.DebugDuration` |
| `0x000aa178` | `HomophonesEng` |
| `0x000aa498` | `MorphEnding` |
| `0x000aa4d8` | `PartOfSpeech` |
| `0x000a9be8` | `PerturbHClosureIntercept` |
| `0x000a9bf8` | `PerturbHClosureSlope` |
| `0x000a9c08` | `PerturbHReleaseIntercept` |
| `0x000a9c18` | `PerturbHReleaseSlope` |
| `0x000aa998` | `PitchAssembly.Debug` |
| `0x000aa948` | `PitchAssembly.LogBoundaryPitch` |
| `0x000aa8e8` | `PitchAssembly.SmoothPhonemeAmplitude` |
| `0x000aa838` | `PitchChange.HannProportion` |
| `0x000aa828` | `PitchChange.UseHann` |
| `0x000aa348` | `PostProcessDurationModification.Skip` |
| `0x000aa908` | `RateChange.UseFFT` |
| `0x000aaa88` | `Search.AllowHyphenatedWordUnits` |
| `0x000aaa78` | `Search.ConcurrencyTorture` |
| `0x000aa7f8` | `SegmentAssembly.WSOLAMaxScale` |
| `0x000aa7e8` | `SegmentAssembly.WSOLAMinSamples` |
| `0x000aa808` | `SegmentAssembly.WSOLAMinScale` |
| `0x000aa4e8` | `Tags` |
| `0x000aa288` | `ToBIPitch.DownStepMonosyllabicPhrases` |
| `0x000aa268` | `ToBIPitch.DowngradeVerbs` |
| `0x000aa278` | `ToBIPitch.VerbDowngradeFactor` |
| `0x000aa768` | `UnitCost.PowerClipMax` |
| `0x000aa388` | `Voice.UseHeapBasedDemis` |
| `0x000aab38` | `WordCost.OptimalWeight` |
| `0x000aa128` | `en_US` |
| `0x000aa168` | `opad` |
| `0x000aa158` | `opax` |
