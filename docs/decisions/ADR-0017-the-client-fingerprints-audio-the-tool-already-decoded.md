# ADR-0017: The Client Fingerprints Audio the Tool Already Decoded

Status: proposed

Date: 2026-09-15

Extends [ADR-0010](ADR-0010-the-corpus-key-is-a-function-of-the-audio.md) and
[ADR-0011](ADR-0011-the-commons-is-what-other-tools-plug-into.md) point 2. One of the five
records proposed together on 2026-09-15; the set and its order are in `ADR-0014` point 6.
**First in that order, because its measurement decides whether it exists.**

## Context

`ADR-0010` made the corpus key the SHA256 of an AcoustID fingerprint, because the key must be a
function of the audio so that a second client holding the same audio can confirm the row. It
follows that a tool with no fingerprint has no key, and KalinkaPlayer#128's first question was
exactly that: fingerprinting is optional there, many tracks have an MBID and no fingerprint, and
the maintainer "would prefer not to add audio decoding and fingerprinting solely to participate".

The premise in that sentence is what this record is about. Kalinka does not need to *add* audio
decoding: it decodes every track to PCM to run CLAP on it, and the maintainer named audio
preparation as the expensive step. Chromaprint fingerprints PCM — that is all `fpcalc` does after
its own decode — and the AcoustID fingerprint reads only the first 120 seconds. A tool that has
the PCM in hand can fingerprint it for a fraction of the cost it already paid. The question is not
whether that is cheap; it is whether the fingerprint that comes out is **the same string**
`fpcalc` would have produced from the file, because the key is its SHA256 and one bit of
difference is a different row.

### What `clapback-client` does today, as of 2026-09-15

`fingerprint_file` (`packages/client/src/clapback_client/fingerprint.py`) runs
`acoustid.fingerprint_file(path)` in a subprocess and hashes the string it returns. pyacoustid
decodes with whatever `audioread` finds — ffmpeg, GStreamer, Core Audio, MAD — or shells out to
`fpcalc`, which decodes with its own ffmpeg. So the key already depends on a decoder the corpus
does not control, on every client, and **nobody has measured whether two decoders yield one
string.** `ADR-0010` measured that beets' stored fingerprints matched a fresh chromaprint run 99
of 99 times — on one machine, one decoder. This record's measurement is the one `ADR-0010` did
not make, and it bears on the existing key whether or not the PCM path is built.

### The measurement this record waits on — not yet made as of 2026-09-15 23:58 UTC

On the 56 FLACs `ADR-0008`'s codec measurement used, three fingerprints per file:

- (a) `fpcalc` on the file — the reference;
- (b) `fpcalc` on a WAV written from PCM the tool decoded itself (soundfile → 48 kHz mono, as
  `clapback-embed` does), which is what this record proposes;
- (c) `acoustid.fingerprint_file` through pyacoustid's library path — what the client does today
  on a machine where `libchromaprint` is present and `fpcalc` is not.

The decision below is conditional on (b) = (a) for every file. If it holds, the record is
accepted as written. If (b) differs from (a) on any file, point 1 is **rejected**, and this
record's Status records that with the count. If (c) differs from (a), that is a defect in the
existing key regardless of this record, and it is filed against `ADR-0010` the same day.
`chromaprint` was not installed on the machine that would run this when the record was written;
the Implementation block will carry the result and its date.

## Decision

1. **`clapback-client` gains `fingerprint_pcm(samples, sample_rate, channels=1) -> str`**,
   which writes the first 120 seconds of the given PCM to a temporary 16-bit WAV with the
   standard library's `wave` module and runs the same out-of-process fingerprinting
   `fingerprint_file` runs. No new dependency: the client stays stdlib-only, and chromaprint
   stays the optional external it already is. The result is hashed with the existing
   `hash_fingerprint`, so the key is computed by the same code either way.

2. **The function is documented as producing the file's fingerprint, and that claim is
   measured, not assumed.** The README section that introduces it cites this record's
   measurement by date and count, and a test in the client fingerprints one committed audio
   fixture both ways and asserts equality. If chromaprint is absent the test skips and says so.

3. **The 120-second rule is stated where a tool would trip on it.** A tool that decodes only a
   window — Kalinka's three fragments, say — does not have the first 120 seconds and cannot use
   this. The docstring says: "the first 120 seconds of the track, from the start, at the original
   sample rate or a resampling of it; a fragment from the middle is not a fingerprint of the
   recording."

4. **This does not decide a second key type.** A tool with no PCM and no fingerprint still has
   no key. KalinkaPlayer#128's questions 1 and 2, taken literally, ask for a row keyed on an MBID
   alone; `ADR-0010`'s reason for refusing that stands, and this record's answer is that the
   population without a fingerprint is smaller than it looked once fingerprinting costs what a
   WAV write costs.

## Alternatives Considered

- **Bind `libchromaprint` in-process and feed it PCM directly.** Rejected twice over: it needs
  `ctypes` against a library the client cannot assume is present, which is the dependency
  `ADR-0011` point 2 forbids; and `ADR-0009` point 5's reason for running chromaprint out of
  process — it segfaults on some inputs — applies to PCM as much as to files.
- **Compute the fingerprint from the tool's mel features instead of PCM.** Rejected: the key is
  the *AcoustID* fingerprint so that AcoustID, MusicBrainz and every existing tool agree on what
  a recording is. A different fingerprint is a different key, which is `ADR-0010`'s decision
  reversed.
- **Accept MBID-keyed rows for tools without fingerprints.** Rejected in point 4, for
  `ADR-0010`'s reasons; and noted in `ADR-0014` point 5 as the conversation to have with a second
  population, not before.
- **Do nothing and let tools call `fpcalc` on the file.** This remains available and is what
  the beets and Picard plugins do. Rejected as the *only* answer because it decodes every track
  a second time on the machines where decoding is the expensive step.

## Consequences

- **Positive** — for a tool that already holds PCM, participation costs a WAV write per track,
  and the fingerprinted population becomes nearly the whole library.
- **Positive** — the measurement, whichever way it goes, closes a gap in `ADR-0010`: whether the
  key is reproducible across decoders is finally a number.
- **Tradeoff** — a 120-second 48 kHz 16-bit mono WAV is 11.5 MB written and read per track. On a
  Pi's SD card that is not nothing; the temporary file lives in `tmpfs` where one exists, and
  the docstring says so.
- **Tradeoff** — if the measurement fails, this record is a rejected record, and its value is
  that the reasoning is written down once rather than re-derived by the next tool author.
- **Follow-up** — if (c) ≠ (a), `ADR-0010` gains an Implementation entry and the client pins
  its fingerprinting to `fpcalc` where it can find one, preferring it over the library path.
