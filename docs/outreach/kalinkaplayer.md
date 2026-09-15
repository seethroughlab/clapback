# KalinkaPlayer — an issue proposing lookup-before-embed and opt-in contribution

**Where:** a GitHub issue on `madenvel/KalinkaPlayer`, per its CONTRIBUTING (fork, feature
branch, `make test`, PR describing the change and the affected package). The issue proposes; the
PR follows if the maintainer is open. **Their CONTRIBUTING discloses AI assistance at repository
level and asks for no per-commit attribution — do not put co-author trailers on anything sent
there.**

**What was checked.** `packages/kalinka-plugin-localfiles/.../embedder/clap_onnx.py`: the same
`music_audioset_epoch_15_esc_90.14` checkpoint as dj-track-similarity, exported to ONNX, run on
ONNX Runtime only; 48 kHz; one to three 10 s fragments at 25/50/75% depending on track length;
fragment mean, L2 by the caller; 512 dims. The audio encoder is ~285 MB and stays resident;
comments budget the embedder at ~600–800 MB "within the 4 GB Pi budget". `db_schema.py`: tracks,
albums and artists carry `mbid`; `recording_identity` stores per-track AcoustID / MusicBrainz
matches; `track_evidence.fingerprint` holds the chromaprint. AcoustID enrichment is gated on a
configured API key — "no key means the plugin isn't loaded" — which is the pattern an opt-in
commons lookup would follow. GPL-3.0-or-later; `clapback-client` is MIT, compatible.

**What that means for the ask.** Three things line up better here than anywhere else in the
list: the tool already has the fingerprint (so the key is free), already has the recording id (so
naming is free), and runs on a Pi 4 where a model run is the most expensive thing it does. The
pipeline is theirs, not ours — same caveat as dj-track-similarity, stated the same way. The two
projects share a checkpoint and a window length, which is worth mentioning to both without
promising anything.

## Title

    Proposal: look up CLAP embeddings before computing them, and contribute them — opt-in, via a public commons keyed on the AcoustID fingerprint the plugin already has

## Body

> **In one paragraph.** [clapback](https://clapback.seethroughlab.com) is a public commons of CLAP
> audio embeddings: one 512-float vector per recording, keyed on the SHA256 of the AcoustID
> fingerprint and on an identity string naming the pipeline that produced it, with MusicBrainz
> recording ids attached by contributors who hold the audio. A tool looks a track up before running
> the model, contributes what it computes if the user has opted in, and can ask what sounds like a
> vector across every library that has contributed. Reads need no key; contribution is off by
> default. The contract is a stdlib-only package, [`clapback-client`](https://pypi.org/project/clapback-client/)
> (MIT) — no new runtime beside the ONNX one you already carry.
>
> **Why Kalinka specifically.** I read `clap_onnx.py`, `db_schema.py` and the enrichment code
> before writing this, and three things line up:
> - The plugin already computes the AcoustID fingerprint (`track_evidence.fingerprint`) and
>   already resolves recording ids (`recording_identity`, `tracks.mbid`). The commons's key and its
>   naming are both things you have.
> - On a Pi 4 the CLAP pass is the expensive step — a ~285 MB encoder resident and a budget you
>   comment on in the code. A recording another Kalinka install has already analysed becomes a
>   512-float HTTP lookup instead of a model run; a new library's first index is the case where
>   that matters most.
> - Your AcoustID enrichment is gated on a configured key and otherwise absent. A commons lookup
>   would follow the same shape: a config switch, off by default, nothing sent without it.
>
> **The pipeline caveat, up front.** Your CLAP is `music_audioset_epoch_15_esc_90.14` over one to
> three 10-second fragments, fragment-mean, L2. The commons's current rows are a different
> pipeline (`laion/clap-htsat-unfused`, whole-track mean). **I am not proposing you change yours.**
> The corpus keys on the pipeline identity and admits any at 512 dimensions, so Kalinka would
> contribute under its own — `lukewys/laion_clap:music_audioset_epoch_15_esc_90.14+frag3x10s+mean+l2+fp32`
> or whatever you would call it — and the comparability, and the saved model runs, would be among
> Kalinka installs. (dj-track-similarity uses the same checkpoint with a slightly different
> window rule; if the two of you ever wanted one shared identity, the commons would document it.
> Not something to decide here.)
>
> **What it would look like** in `kalinka-plugin-localfiles`, behind a setting that defaults to
> off:
> 1. before `get_audio_embedding`, `Corpus().lookup(sha256(fingerprint), PIPELINE_ID)` — a hit is
>    the vector and the encoder does not run; a miss runs the existing path unchanged;
> 2. after a miss, if contribution is on, `Corpus().contribute(...)` with the hash, the vector, the
>    identity string, a per-install random `client_id`, and the recording id you already hold;
> 3. optionally, `Corpus().similar(vector)` as a source for "similar artists / recordings" that
>    reaches beyond the local library — results carry MusicBrainz ids where rows are named.
>
> Roughly fifty lines in the embedder and the settings, no schema change, no new required
> dependency, and what leaves the machine is a one-way hash, a vector and a MusicBrainz id —
> never audio, paths or other tags. I would write it against your CONTRIBUTING flow (feature
> branch, `make test`, PR naming the package) if you are open to it.
>
> **What the commons holds today, honestly:** 25,886 rows from one contributor, 89.6% named.
> Kalinka would be the second population, under its own identity, and the first on a Pi.
>
> — Jeff (seethroughlab). The decisions behind this are written down:
> <https://github.com/seethroughlab/clapback/tree/main/docs/decisions>.
