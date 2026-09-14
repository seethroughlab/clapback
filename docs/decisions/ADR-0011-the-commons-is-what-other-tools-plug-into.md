# ADR-0011: The Commons Is What Other Tools Plug Into

Status: accepted

Date: 2026-09-13

Implementation:
- **Accepted 2026-09-13**, the day it was proposed. `ADR-0009`'s `Status:` line records the partial
  supersession.
- **Point 2 is built** (2026-09-13): `packages/client/`, distribution `clapback-client`, import
  `clapback_client`. Nine names — `Corpus` with `lookup`, `has` and `contribute`; `hash_fingerprint`,
  `canonical` and `fingerprint_file`; `mint_client_id` and `ensure_client_id`; the errors. The built
  wheel declares **no** `Requires-Dist` beyond the dev extras, installs cold into an empty venv with
  nothing else, and answers a live lookup against the deployed corpus with a 512-float row. Moved
  from the CLI with `git mv` so history follows, and the CLI now imports it — 26 CLI tests pass
  against the package, and the one test that asserted the checkpoint rule moved with the rule.
- **The extraction found a bug the CLI had shipped with.** After the last retry, a 429 fell through
  to the generic "contribute returned 429" error, and the "rate limited repeatedly" message below the
  loop was unreachable. Caught by writing the contract's tests against a scripted server rather than
  by reading the loop, which is the argument for publishing the contract as code: a plug-in author
  who wrote their own loop would have shipped the same bug or a different one.
- **`Corpus.lookup` returns the row, not just its presence**, because the row is the exchange.
  `has` was the CLI's need; a plug-in's need is the 512 floats, so that on a Raspberry Pi it does not
  run the model. `contribute` derives `clap_model_version` from the pipeline identity and defaults
  `analysis_version` to 1, so every plug-in gets the same rule for the two recorded columns the key
  no longer includes.
- **Release order is constrained, and the constraint is recorded in `ADR-0005`**: `clapback-client`
  goes to PyPI before the next `clapback-cli` tag. The `pypi` environment's deployment policy now
  admits `client-v*`, added by the same one-line call the CLI release needed and did not have.
- **Point 5's beets plugin is built** (2026-09-13): `packages/beets-clapback/`, distribution
  `beets-clapback`, shipping `beetsplug/clapback.py` and nothing else — no `__init__.py`, so the
  namespace stays open to every other plugin. `beet clapback [query]` looks up, else embeds, and
  contributes only under `contribute: yes`; `beet clapback-search` answers a description offline
  from a sidecar store. Two flexible attributes, `clapback_hash` and `clapback_status`, make the
  result queryable with `beet ls` like anything else. Tested through beets' own
  `PluginTestHelper` — the plugin loaded and the commands run as `beet` would — with only the
  corpus and the embedder stubbed: 12 tests, ruff clean.
- **The follow-up measurement came back clean, and the opposite of Familiar's.** A 99-track
  library was imported into beets 2.14.0, fingerprinted through `chroma`, and every stored
  `acoustid_fingerprint` compared with a fresh chromaprint run: **99 of 99 byte-identical, 0 in the
  escaped form, 0 hash mismatches.** beets stores the string chromaprint returned. The plugin hashes
  it through `canonical()` regardless — a test asserts the escaped and raw forms of one fingerprint
  yield one `clapback_hash` — because a column is where the last defect hid.
- **It never re-embeds what it already has.** A user who indexes with `contribute: no` and turns
  it on later gets every stored vector sent without the model running again; a test asserts the
  embed call count stays at one across the two runs. That is the "skip the recompute" exchange
  applied to the user's own past work, and on a laptop it is the difference between minutes and
  an afternoon.
- **What was and was not exercised live.** Against the deployed commons and the real library:
  the plugin loads, both commands register, `-p` enumerates correctly, and a real run stops at the
  first corpus miss with the plain "encoders are missing" message rather than a traceback. The
  `found` path could not be shown live because the corpus holds 0 of those 99 recordings, and the
  `contributed` path was not run live because contributing a test library under a fresh
  `client_id` would put a second "contributor" in the corpus that is not one — the thing
  `ADR-0004` point 4 is careful about. Both paths are covered by the harness tests, which is the
  same standard the reference client shipped under.
- **beets' `chroma` did the hard part for free, as predicted.** Every one of the 99 items carried
  `acoustid_fingerprint` before the plugin ran, so the plugin's fingerprinting fallback was never
  needed. `pyacoustid` is a dev dependency only.
- **Release order lengthens by one**: `clapback-client` and `clapback-embed` before
  `beets-clapback`, since the wheel depends on both from the index. The `pypi` environment admits
  `beets-v*`. Not yet published; needs a pending publisher for `beets-clapback` and a
  `beets-v0.1.0` tag.
- **Point 4 shipped as `ADR-0012`** (2026-09-13), and its coverage came from Familiar's
  `ADR-0115` backfill (2026-09-14) rather than from the plug-ins, as `ADR-0102`'s Implementation
  block predicted the other way round.
- **Point 5's Picard plugin is built** (2026-09-14): `packages/picard-clapback/`, a Picard 2.6–2.13
  plugin shipped as a zip on a `picard-v*` GitHub release — a plugin is a file a person installs
  from Options → Plugins, not a distribution on PyPI. Two context-menu actions on tracks and
  files: *look up in the commons* (per file: held or not, named under *Contribute*, embedded and
  contributed if an embedder is present) and *what sounds like this* (a dialog of neighbours with
  MusicBrainz links for the named and hashes for the rest). An options page with *Contribute* off
  by default and a post-save hook off by default. Files without a fingerprint go through Picard's
  own fpcalc first — the same one *Scan* uses — so the plugin never fingerprints anything itself.
- **Lookup-only mode is the default and is the point.** Picard's bundled application cannot
  `pip install`, so `clapback-embed` is absent for nearly every user, exactly as this record
  predicted. The plugin says so in the options page, computes nothing, and still does the two
  things Picard is uniquely placed to do — **name** rows (every matched file carries the recording
  id) and **ask** (using the commons's own vector under the commons's own pipeline). For that,
  `clapback-client` 0.2.1 made `Corpus.lookup`'s pipeline filter optional: without a vector of
  your own there is no comparability question, and the row says which pipeline it came from.
- **The contract is copied in, not depended on.** `clapback/clapback_client/` is a verbatim copy
  of `packages/client`, for the same reason: nothing can be installed beside a bundled Picard.
  `scripts/sync_client.py` refreshes it, a test fails when the copy and the package differ, and CI
  runs on changes to either directory. This is the vendoring this record's point 2 made cheap by
  keeping the client stdlib-only — four files, no dependency to carry with them.
- **Tested against real Picard, not a stub.** Picard 2.13.3 installs from PyPI and imports
  headless (`QT_QPA_PLATFORM=offscreen`), so the tests load the plugin as `picard.plugins.clapback`
  and assert what is registered — both actions on both menus, the options page, the post-save
  hook — and round-trip the options page through a real config. The contract itself is tested
  with the corpus scripted, in the same shape as the beets plugin's tests: 26 tests. Exercised
  live in lookup-only mode against the deployed commons with a recording it holds: found, and
  six neighbours back, one named. Not exercised live: contributing, for the reason the beets
  entry gives.
- **Picard 3 is a port, deliberately deferred.** 3.0 was at rc3 on 2026-09-13 with a new plugin
  system — git-distributed, TOML manifest, PyQt6, a `PluginApi` object — whose own documentation
  says its migration tool converts 94.5% of 2.x plugins automatically. The registrations here are
  the standard module-level ones it handles. Porting before 3.0 ships and its registry exists
  would be porting to a moving target.
- Outreach — dj-track-similarity and KalinkaPlayer — is next, and lands on a site that now
  says the right thing (2026-09-14).

Extends [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) points 3 and 8, and
[ADR-0005](ADR-0005-the-repository-is-a-workspace-of-peers.md), whose "published for others to
depend on" this takes literally. **On acceptance it partially supersedes
[ADR-0009](ADR-0009-the-tool-is-useful-before-the-corpus-is.md)**: that record's account of *how a
second contributor arrives* — a stranger installs this project's tool because it is the best local
tool — is replaced by point 1 below. `ADR-0009` points 2 to 9, about what the tool does and refuses to
do, all stand; its point 1 is narrowed from "the tool" to "the reference client".

This is the first record about who the commons is *for* rather than how it works.

## Context

### What a week of building established

Between 2026-09-08 and 2026-09-13 the corpus was re-keyed twice. `ADR-0006` made the pipeline
identity half the key and deleted 47,486 rows that could not say what produced them; `ADR-0010`
made the other half a function of the audio and deleted 14,246 rows keyed on a fact about one
client's schema history. What is left — 25,515 rows — is the first corpus in this project's life
where two people who own the same recording compute the same key and can be told whether they
agree.

That property is the whole asset. It took two ADRs on this side, two on Familiar's, three long
paced runs, and a week. Nothing else this project has is comparably hard to reproduce.

### The tool is not scarce, measured 2026-09-13

`ADR-0009` rests on `ADR-0001` point 8 — "the tool must be worth running with the corpus empty" —
and reasons from there to building one. A survey of what exists shows the tool already exists,
several times, and at least one is more capable:

| project | what it does with CLAP | how it compares to `clapback-cli` |
|---|---|---|
| [dj-track-similarity](https://github.com/MeteorBurn/dj-track-similarity) | text-prompt search, seed-track search, audio dedup — across CLAP, MuQ-MuLan, MERT, MAEST and SONARA, plus classifiers | strictly more: six embedding families to our one, and classifiers |
| [KalinkaPlayer](https://github.com/madenvel/KalinkaPlayer) | self-hosted hi-fi system for Raspberry Pi and Linux; semantic search and similar-artist recommendation from mean-pooled CLAP embeddings, model resident in RAM | a player that does our two things as features |
| [Zero-Shot Crate Digging](https://arxiv.org/pdf/2411.12209) | open-source retrieval over a music library using the same LAION CLAP checkpoint | research code; the same idea, published first |
| Cyanite, AIMS | commercial tagging and sonic similarity for labels and sync | a market that already pays for this |

Every one of them computes embeddings, uses them locally, and discards them. None has a key another
tool could reproduce, a pinned pipeline, or any notion of agreement. That is not a gap in their
design; it is a different product. But it means `ADR-0009`'s implicit bet — that a better local tool
is the route to a second contributor — is a bet on winning a race that others started earlier with
more features, over ground that is not where this project's advantage lies.

### The commons has no competitor, and its only precedent left a hole in specific software

AcousticBrainz was the one shared resource of this shape.
[MetaBrainz ended it in 2022](https://blog.metabrainz.org/2022/02/16/acousticbrainz-making-a-hard-decision-to-end-the-project/)
for reasons `ADR-0001` records in detail, and no successor exists — MetaBrainz's own work has moved
to similarity from listening data, not audio. The community's response has been to run Essentia
locally and keep the results, which is exactly `ADR-0001` point 8's "passive accumulation does not
happen" measured from the other side.

What matters here is that the hole has a shape. Two of the most widely used open-source music
library tools shipped *exactly this pair of operations* and removed them when the service died:

- **beets** had an [`acousticbrainz` plugin](https://beets.readthedocs.io/en/stable/plugins/acousticbrainz.html)
  to fetch and an [`absubmit` plugin](https://beets.readthedocs.io/en/stable/plugins/absubmit.html)
  to contribute. [Both were deprecated](https://github.com/beetbox/beets/issues/4627) with
  beets-xtractor — compute locally, keep it — as the suggested replacement.
- **Picard** [removed AcousticBrainz analysis and submission](https://picard.musicbrainz.org/changelog/)
  from the core application (PICARD-2422), and its two AcousticBrainz plugins went with it.

Those users ran a submit step for years for a project that gave them almost nothing back. They are
the most favourable population this project could hope to reach, and they have nowhere to submit to.

### The contradicted premise

`ADR-0009`'s Context says "this record is the only queued work whose output is a person who was not
already here." That was true of the queue and false of the world: the people who were not already
here were already running tools that compute the vector this corpus wants. The tool this project
needed to write was never the local half. It was the fifty lines that turn any of those tools into a
contributor.

### The contract is already written, and it is small

A tool that contributes has four obligations, each decided in an existing record:

1. Fingerprint the audio and hash it canonically — `ADR-0010` points 1 and 2.
2. Produce the vector through the reference pipeline, or declare its own — `ADR-0001` point 3,
   `ADR-0006` point 1.
3. Look up before contributing — `ADR-0008`'s precondition, learned by two clients now.
4. Send `client_id` and `pipeline_version` — `ADR-0004` point 1, `ADR-0006` point 4.

`packages/cli/src/clapback_cli/fingerprint.py` (127 lines) and `corpus.py` (133 lines) are that
contract, with no dependency beyond the standard library. Everything else — revocation, quotas, the
ceiling, agreement recording, the key itself — is code on the server's write path that a tool never
sees.

### What does not change: the cold start

A commons is worth exactly its coverage of the library asking. Familiar's 25,515 rows are one
person's taste, so the first tool to plug in gets a near-zero hit rate on lookups and nothing yet
from similarity or confirmation. `ADR-0009` tried to solve this by making the tool worth running
regardless. This record does not solve it either; it moves it. The question stops being "why would a
user install this" and becomes "why would a maintainer add this" — and the second question has a
better answer, because the cost is fifty lines and a dependency, and the benefit accrues to their
users without the maintainer doing anything further.

## Decision

1. **The commons is the product. `clapback-cli` is the reference client, not the acquisition
   channel.** It stays, it stays useful, and it stays the place the contract is exercised end to end
   — but the route to a second contributor is tools people already run, and the tool's job is to
   prove the contract works, not to win users from dj-track-similarity.

2. **The contract is published as its own package, `clapback-client`, with no dependency beyond
   the standard library.** Fingerprinting, canonical hashing, lookup, contribution, `client_id`
   management, and the backoff the server's rate limit requires. Extracted from the CLI's
   `fingerprint.py` and `corpus.py` rather than written fresh, so the reference client and the
   published contract cannot drift. It does **not** depend on `clapback-embed`: a tool that already
   has an embedder and only wants to contribute under its own pipeline identity must not have to
   install ONNX Runtime to do so. `ADR-0005`'s workspace gains a fourth peer.

3. **The commons stays a CLAP commons at 512 dimensions until someone asks otherwise, and says
   so.** The key already admits any number of pipelines
   (`(fingerprint_hash, pipeline_version)`, `ADR-0006`), so dj-track-similarity's MuQ vectors could
   sit beside Familiar's CLAP vectors without touching them. But `embeddings.embedding` is
   `Vector(512)` (`packages/server/app/db/models.py:57`), and MERT and MuQ are 768 to 1024 wide. A
   pipeline identity therefore implies a dimension, and a pipeline of a different dimension is a
   schema decision that needs its own record. This is stated so it is a decision rather than an
   accident someone discovers at insert time.

4. **The recording-id key moves from last in the queue to next.** `ADR-0001` deferred item 4 was
   "cheap, and last on purpose — nothing above depends on it." For a tool plugging in, it is the
   difference between `/v1/similar` being a curiosity that returns hashes and being the reason to
   integrate. `ADR-0002` point 4 already names it as the prerequisite for similarity being useful;
   this record makes it the prerequisite for outreach.

5. **The integrations, in the order they should be attempted, and what each needs.** Ranked by
   leverage divided by dependence on somebody else's cooperation.

   **First: beets, and this project writes the plugin.** The largest user base of anything on this
   list; a plugin is a single Python file; and its users are the ones who ran `absubmit`. Decisively,
   beets' [`chroma` plugin](https://beets.readthedocs.io/en/stable/plugins/chroma.html) already
   fingerprints through pyacoustid and *stores the fingerprint in the library database*, so a
   `beets-clapback` plugin gets the hardest input for free and its `beet clapback` command is
   lookup-or-embed-and-contribute over rows that already carry `acoustid_fingerprint`. Needs no
   maintainer's cooperation to exist; needs it only to be listed. The plugin must hash the stored
   fingerprint canonically — `ADR-0010` point 2 applies to it as to every client, and beets' column
   is the kind of place a re-encoding could hide.

   **Second: Picard, and this project writes the plugin.** AcoustID lookup is native to Picard, so
   every track it touches has a fingerprint before any plugin runs. Python, a plugin ecosystem with
   a listing, and a user base that lost AcousticBrainz submission from the core application. The
   plugin computes the embedding on save or on demand and contributes. The cost is that Picard runs
   on desktops where 614 MB of ONNX encoders is a real ask; the plugin should work in
   lookup-only mode without them and say so.

   **Third: dj-track-similarity, by proposal to its maintainer.** Closest in spirit and already
   producing CLAP vectors from the same checkpoint, so the ask is the smallest in code and the
   largest in principle: route CLAP through `clapback-embed` rather than its own front-end and
   pooling, so the vectors are comparable, and add contribution as an opt-in. Its other five
   embedding families stay local unless point 3 is revisited. The pitch is honest and specific —
   its users skip recomputing what the commons holds, and gain cross-library similarity once point 4
   lands — and it is a pull request, not a proposal, because fifty lines are easier to accept than
   an argument.

   **Fourth: KalinkaPlayer, by proposal to its maintainer.** It targets a Raspberry Pi 4 with 4 GB
   of RAM and keeps a ~285 MB model resident, which makes lookup-before-embed worth more here than
   anywhere else on this list: on that hardware, every embedding the commons already holds is minutes
   the Pi does not spend. Same ask as above; same shape of pull request. Its mean-pooled embeddings
   are close to `pool1` but are not `pool1`, so it contributes under `clapback-embed`'s identity or
   its own, and the difference should be measured rather than assumed before the first row lands.

   **Alongside: beets-xtractor.** The local-Essentia replacement the beets maintainers pointed
   deprecated users at. Not a competitor — it computes bpm, key and the descriptors `ADR-0001` point 4
   keeps outside this corpus's purpose — but the same users, and a plugin that runs beside it rather
   than instead of it is an easier install than one that replaces it.

   **Already plugged in: Familiar.** The existence proof, and the reason the contract is known to
   work end to end. It contributes under `clapback-embed`'s identity and has since `ADR-0006` phase 2.

   **Adopters of the embedder rather than plug-ins: research code.** Zero-Shot Crate Digging and
   its kind use the same checkpoint and would benefit from a pinned front-end for reproducibility,
   which is what `clapback-embed` is. They are not a route to contributors; they are a route to the
   embedder being the thing people cite.

   **Not plausible now, and why, so nobody re-evaluates them:**
   - *Mixxx* — C++, and its analysis pipeline is not reachable from a Python plugin. Its
     extensibility is MIDI and HID controllers, not analysers.
   - *Navidrome, Jellyfin* — no music-similarity surface to attach to. Jellyfin's intro-skipping plugin uses
     chromaprint on video, which is not this.
   - *Roon, Plexamp, Rekordbox, Serato* — closed, and their sonic analysis is a product they sell.
   - *Spotify, Apple, Tidal* — not libraries the user owns; the fingerprint is of a file.

6. **Every integration is opt-in and off by default.** `ADR-0001` point 11 for Familiar and
   `ADR-0009` point 4 for the tool both say nothing leaves the machine unless asked, and a plugin in
   somebody else's software has less standing to assume consent, not more. A beets user who enables
   `clapback` gets lookups; contribution is a second, separate setting, and the plugin says what it
   sends before the first time it sends it.

7. **What this project does not do.** It does not fork or vendor another tool to add contribution
   to it; it does not run a registry of integrations; and it does not accept a non-canonical key,
   a missing `pipeline_version`, or an undeclared dimension to make anyone's integration easier. The
   contract is small precisely so that it can be strict.

8. **Third-party contributions end the deferral of two guards.** `ADR-0004` point 9's third bound,
   per-client quotas, was deferrable with one client whose owner is also the operator. It is not
   deferrable with a beets plugin in the wild. And `ADR-0007`'s attestation was "deliberately
   unbuilt until a second contributor exists" — with contributors whose pipelines nobody here can
   audit by reading, the reference signal is the only check there is, and K=2 becomes reachable and
   necessary in the same moment.

9. **Execution order:** this record → point 2's package → point 5's beets plugin → point 4's
   recording-id key → point 5's Picard plugin → outreach to dj-track-similarity and KalinkaPlayer →
   point 8's quotas and attestation as contributions arrive. The beets plugin goes before the
   recording-id key because it can produce a contributor without it; outreach goes after, because a
   maintainer evaluating the pitch should be able to see similarity return something a person can
   read.

## Alternatives Considered

- **Keep `clapback-cli` as the flagship and out-feature the competition.** The path `ADR-0009`
  implies. Rejected because it is a race against dj-track-similarity's six embedding families and
  classifiers, run by a project whose advantage is a corpus design that none of them have. Winning it
  would take effort that does not improve the commons; losing it costs the one route the project
  had. Either outcome is worse than not running.

- **Build the commons around dj-track-similarity specifically.** The obvious reading of "the tool
  is not scarce." Rejected on three grounds: its CLAP vectors come through its own front-end and
  pooling, so they are not comparable with the corpus without going through `clapback-embed` — the
  drift `ADR-0006` exists to prevent; its users value six embedding families and the column holds
  one width; and it is one maintainer's project, so the commons would be a satellite of a roadmap it
  does not control. It is the third integration here rather than the shape of the record.

- **Fold the client into `clapback-embed` rather than a fourth package.** One dependency for a
  tool to add instead of two, and no new peer in the workspace. Rejected because it forces ONNX
  Runtime and 614 MB of encoders on a tool that has its own embedder and only wants to contribute.
  The fallback if a fourth package proves to be too much ceremony is a `[client]` extra on
  `clapback-embed`, which is worse but survivable.

- **Support every embedding family now.** Make the commons a home for MERT and MuQ vectors as well
  as CLAP, since the key already permits it. Rejected because it is a schema change with no
  demand yet, and because a commons that holds five incomparable populations is five small commons
  wearing one name. Point 3 keeps the door open and makes walking through it a decision.

- **Wait for a stranger to install `clapback-cli`.** It is on PyPI as of 2026-09-13 and it works.
  Rejected because `ADR-0001` has measured proof that passive accumulation does not happen, and
  because the people most likely to want this already have a tool they are not going to replace.

- **Talk to maintainers before building anything.** Cheaper if the answer is no. Rejected in
  order rather than in substance: a pull request that adds fifty lines and an opt-in setting is an
  easier thing to say yes to than a proposal, and the beets and Picard plugins need no permission
  to exist. The maintainers are approached with something that already works.

## Consequences

- **Positive** — the project's effort goes where its advantage is. Every hour on the commons
  design compounds; every hour on local features is spent matching what exists.

- **Positive** — the population reached is the right one. beets and Picard users ran a submission
  step for a project that returned almost nothing, for years. They are not being asked to want
  something new.

- **Positive** — the contract is exercised by more than one implementation from the start, which
  is the only thing that has ever found a defect in it. `ADR-0010` was found by building a second
  client; a third and fourth will find what the second missed.

- **Tradeoff** — the cold start is moved, not solved. The first plugin's users get near-zero lookup
  hits and nothing yet from similarity. The pitch has to be honest about that and the work in point 4
  is what shortens it.

- **Tradeoff** — writing plugins for other projects means maintaining code against other projects'
  release cadences, in their plugin APIs, under their review. Two plugins is a real ongoing cost for
  a project with one maintainer.

- **Tradeoff** — `ADR-0009`'s tool is demoted the week it shipped. That is the correct order — it
  had to exist to prove the contract — and it will read as a reversal. This record is the
  explanation.

- **Tradeoff** — third-party contributions bring point 8's guards forward, and neither quotas nor
  attestation is small.

- **Follow-up** — point 2's package needs a decision about how much of the CLI's `store.py` it
  takes: `client_id` persistence belongs in the client; the vector store does not.

- **Follow-up** — the beets plugin needs a measurement before it ships: hash beets' stored
  `acoustid_fingerprint` canonically for a sample and confirm it matches what chromaprint returns
  for the same files. `ADR-0010`'s defect came from a column, and beets has one.

- **Follow-up** — KalinkaPlayer's mean-pooling and `pool1` should be compared on a sample before
  either identity is claimed for its vectors. If they agree to `ADR-0006`'s `identical` band, its
  existing embeddings are contributable without recompute, which on a Raspberry Pi is the whole
  argument.

- **Follow-up** — once the first non-Familiar contributor exists, `ADR-0007` and `ADR-0008` stop
  being deferrable, and their `Implementation:` blocks should say the day that happens.
