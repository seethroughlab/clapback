# dj-track-similarity — an issue proposing opt-in contribution, with a PR offered

**Where:** a GitHub issue on `MeteorBurn/dj-track-similarity`. `ADR-0011` point 5 said "a pull
request, not a proposal". The code survey (2026-09-15) argues for the issue first: the repository
has no licence and no CONTRIBUTING, `AGENTS.md` sets "Strict Simplicity & Anti-Bloat" rules and
names `dev` as the working branch, and the only merged PR is the owner's. A cold PR against those
norms reads as presumption; an issue that is concrete enough to be a PR, with the PR offered,
reads as respect for them. The PR follows the moment they say yes.

**What was checked.** `src/dj_track_similarity/embedding/clap.py`: checkpoint
`lukewys/laion_clap` / `music_audioset_epoch_15_esc_90.14.pt`, HTSAT-base, non-fusion; 48 kHz;
consecutive end-aligned 10 s windows over the full track; per-window L2, window mean, L2; 512
dims, float32 in SQLite (`clap_embeddings.embedding_blob`). `torch`/`transformers`/`laion-clap`
live under the optional `ml` extra; the base install has no ML dependency. There is no AcoustID
fingerprint anywhere — dedup uses SONARA's own fingerprint. The embedding families are registered
in `embedding/registry.py` (`clap`, `maest`, `mert`, `mert_v2`, `muq`, `mulan`).

**What that means for the ask.** Their CLAP pipeline is not ours. The commons keys every row on
`(fingerprint_hash, pipeline_version)` and admits any identity at 512 dimensions, so their vectors
would sit beside the existing rows under their own identity and never collide — and comparability
with the existing 25,886 rows is *not* on offer without a checkpoint change nobody should ask of
them. The value is among their own users, and it is real: a track another install has already
analysed is a lookup instead of a model run, and `/v1/similar` works across every library that
contributed under that identity. The one new input they need is an AcoustID fingerprint, which
`fpcalc` computes in about a second per track.

## Title

    Proposal: opt-in lookup and contribution of CLAP embeddings to a public commons (keyed so two installs of this tool can share work)

## Body

> **In one paragraph.** [clapback](https://clapback.seethroughlab.com) is a public commons of CLAP
> audio embeddings: one 512-float vector per recording, keyed on the SHA256 of the recording's
> AcoustID fingerprint and on an identity string naming the pipeline that produced it. A tool looks a
> track up before running the model, contributes what it computes if the user has opted in, and can
> ask what sounds like a vector across every library that has contributed. Reads need no key;
> contribution is opt-in and off by default everywhere. The contract is a stdlib-only package,
> [`clapback-client`](https://pypi.org/project/clapback-client/) (MIT), so it adds no ML dependency.
>
> **What your users would get.** Two installs of dj-track-similarity that hold the same recording
> compute the same key. So once one has analysed it, the other's `clap` pass is a 512-float lookup
> instead of a model run — the first thing this tool does on a new library is the expensive thing,
> and it is the thing the commons exists to do once. And `/v1/similar` (HNSW, ~3 ms) answers seed-
> track search across every library that has contributed, not just the local one; results carry
> MusicBrainz recording ids where a contributor has named a row.
>
> **What it would cost — and what it would not.** I read `embedding/clap.py` and `db/ddl.py` before
> writing this. Your CLAP pipeline is `music_audioset_epoch_15_esc_90.14` over 10-second windows
> with per-window L2, window mean, L2. The commons's current rows are a different pipeline
> (`laion/clap-htsat-unfused`, whole-track mean), and **I am not proposing you change yours.** The
> key admits any pipeline identity at 512 dimensions; yours would be its own — something like
> `lukewys/laion_clap:music_audioset_epoch_15_esc_90.14+win10s+l2mean+l2+fp32` — and the corpus
> keeps it apart from everything else by construction. The comparability is among your own users,
> which is where the value is anyway.
>
> Concretely, behind an optional extra and a config switch that defaults to off:
> 1. an AcoustID fingerprint per track (`fpcalc`, ~1 s/track; `pyacoustid` as an optional dep —
>    you have no AcoustID today, and SONARA's fingerprint is a different thing);
> 2. in `ClapEmbeddingAdapter`, before the model runs: `Corpus().lookup(key, PIPELINE_ID)` — a hit
>    is the vector, a miss runs your existing path unchanged;
> 3. after a miss, if contribution is on: `Corpus().contribute(...)` with the hash, the vector, the
>    identity string and a per-install random `client_id`.
>
> About fifty lines in the adapter and the config, no change to the schema, no new required
> dependency, nothing sent unless the user turns it on, and never audio, paths or tags — a one-way
> hash and a vector. That fits the anti-bloat rules in `AGENTS.md` as I read them; if it does not,
> say where and I will cut it.
>
> **What the commons holds today, honestly:** 25,886 rows from one contributor, 89.6% of them
> resolving to a MusicBrainz recording. Your users would be the second population in it, under
> their own identity. I would write the PR against `dev` if you are open to it — or, if you would
> rather not carry it, say so and I will not push.
>
> — Jeff (seethroughlab). Records for the decisions behind this, if useful:
> <https://github.com/seethroughlab/clapback/tree/main/docs/decisions>.
