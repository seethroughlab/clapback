# ADR-0012: A Contribution Can Name Its Recording

Status: accepted

Date: 2026-09-13

Implementation:
- **Accepted 2026-09-13**, the day it was proposed, with one clarification worth keeping: the
  Familiar backfill in point 10 — resolving its fingerprints to MBIDs through AcoustID and claiming
  them for the 25,515 rows it already contributed — matters more than the record's tone suggests,
  because until it runs the corpus is one library with no ids and similarity returns hashes for
  everything regardless of what the plug-ins send. It comes fourth in the order, after the claims
  endpoint exists to receive it, and it is owed on Familiar's side as its `ADR-0102` point 5.
- **The server side is built** (2026-09-13): migration `012`, the `recording_claims` table, both
  write paths, all three read paths, and deletion covering claims. Additive — the first migration
  since `011` that moves no data. 25 tests, contract-level like the rest of the suite.
- **Familiar's `musicbrainz_track_id` is the recording entity, confirmed 2026-09-13.** Three ids
  drawn at random resolved at `musicbrainz.org/ws/2/recording/{id}` as recordings ("Gemini",
  "Orion Megalith", "Die 4 You"). Same misnomer as beets' `mb_trackid`, same referent. The
  follow-up that gated Familiar sending its 1,791 ids is closed; the backfill for the rest is not.
- **Point 11's client and plugin steps shipped 2026-09-13**: `clapback-client` 0.2.0 (`claim`,
  `similar`, `recording`, and `recording_mbid` on `contribute`) and `beets-clapback` 0.2.0 (sends
  `mb_trackid`, adds `beet clapback-similar`), both on PyPI.
- **The first coverage number, measured 2026-09-14 00:00 UTC.** Familiar claimed the ids it already
  held — `scripts/claim_recordings.py`, its `ADR-0102`'s cheap half — through
  `POST /v1/recordings/claims`: **1,748 considered, 1,745 claimed, 3 not found**, zero lost to
  retries. The corpus's 25,515 rows now carry **1,745 claims, 6.8%** — the figure point 8
  predicted — every one under a single client id, so `recording_claims` is 1 on all of them:
  evidence of nothing yet, shown rather than hidden as `ADR-0008` asks. The three 404s are
  tracks Familiar has fingerprinted but whose hash the corpus does not hold; they are not a
  claim-path failure.
  Worth recording how the number was reached: the first attempt paced at 200/min against the
  30/min contribution limit and silently lost 45 of its first 500 claims to exhausted retries on
  `429` — the client gives up after three attempts and the script's tally called them "refused".
  It was stopped at ~470, Familiar's #304 set the pace to 25/min, and the rerun was idempotent:
  every already-claimed row returned `201` again. A claim is a write and shares the write limit;
  a backfill script must pace under it, not at the rate the limit names.
- **The backfill ran** — Familiar's `ADR-0115`, built and enabled 2026-09-14, resolution complete
  2026-09-15 08:48 UTC. Measured at 08:49 UTC: **23,196 of the corpus's 25,886 rows are named,
  89.6%**, from 6.8% the day before; every claim still under one client id, so `recording_claims`
  is 1 on all of them. 103 claims the corpus could not take are rows Familiar fingerprinted but
  never contributed. A second contributor's similarity results now resolve for nine rows in ten
  rather than one in fifteen, which is the state `ADR-0002` point 4 was waiting for.

Answers [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) deferred item 4 — "the
recording-id key, `ADR-0102`'s substance, and the reason other applications would query this at
all" — and discharges the dependency [ADR-0002](ADR-0002-the-corpus-answers-similarity-queries.md)
point 4 named: *do not ship the endpoint and call the capability delivered.* Ordered next by
[ADR-0011](ADR-0011-the-commons-is-what-other-tools-plug-into.md) point 4. It adopts the substance
of Familiar's
[`ADR-0102`](https://github.com/seethroughlab/familiar/blob/main/docs/decisions/ADR-0102-the-community-cache-gains-a-recording-key.md)
and changes its shape, for reasons the Context gives.

## Context

### Similarity works and is useless, by design and on purpose

`/v1/similar` has answered in about 3 ms since `ADR-0002` shipped. It returns fingerprint hashes.
A hash is SHA256 of an AcoustID fingerprint and is one-way — `ADR-0010` spent a week making sure
every client computes the same one — so a neighbour's hash tells the caller nothing unless they
already hold the audio. `ADR-0002` point 4 said so at the time and said not to call the capability
delivered. It has not been.

This is the missing half of the exchange a plug-in makes. `ADR-0011` lists what a tool gets for
plugging in: skip the recompute, similarity across libraries it does not own, and confirmation. The
first is live and worth little until coverage exists; the third waits on a second contributor. The
second is the one a beets user would install a plugin *for* — "what sounds like this, that I do not
have?" — and it is the one that returns an opaque string.

### The identifier, and where it actually is, measured 2026-09-13

The identifier is the MusicBrainz **recording** MBID, as `ADR-0102` chose. It is what the tools
that would query this already hold, and it resolves to something a person can act on: a title, an
artist, a release, a page.

| client | recording MBIDs | notes |
|---|---|---|
| beets | typically most of a library | `mb_trackid`, which despite the name is the recording id: `beetsplug/musicbrainz.py:447` sets `track_id=recording["id"]`. `mb_releasetrackid` is the *track* entity and is not this. |
| Picard | every tagged file | MusicBrainz-native |
| Familiar | **1,791 of 26,518 — 6.8%** | `tracks.musicbrainz_track_id`; the same 6.8% `ADR-0102` measured in August. Its point 5 backfill was never built. |
| `clapback-cli` | none | reads no tags |

**The premise `ADR-0102` was written under has inverted.** It assumed Familiar would supply the
ids and backfill its way from 6.8% upward. Familiar has not moved, and the beets plugin
`ADR-0011` shipped is a client whose users tag against MusicBrainz as a matter of course. The
recording ids will come from the plug-ins, not from the first contributor.

### Why the shape changes: an id is a claim, and the server cannot check it

`ADR-0102` point 1 puts an optional `recording_mbid` column on the row. Three things decided since
argue against a column:

- **The server cannot verify it** (`ADR-0102` point 2, and correct: it holds a hash, cannot ask
  AcoustID, and calling MusicBrainz would be a new external dependency `ADR-0003` would have to
  weigh). So the id is an assertion by whoever sent it. `ADR-0008` decided the corpus serves
  agreement, not a verdict, and that applies to an assertion about identity exactly as it does to
  one about a vector.
- **Two clients can disagree.** Mis-tagged files exist, MusicBrainz merges recordings, and two rips
  of one recording can legitimately carry different ids after a merge. A single column keeps the
  first and loses the disagreement, which is the information `ADR-0008` says is worth most.
- **`ADR-0004` made submissions attributable so they could be revoked.** An id written into a
  shared row by one client cannot be removed for that client alone. A claim keyed by `client_id`
  can.

So the id is stored the way agreement already is — one row per assertion, per client — and the
row's "recording" is derived from the claims rather than written into it.

### What this discloses, stated plainly

The hash was one-way so that contributing says "I hold *a* recording" without saying which. A
claim undoes that for the rows that carry one: the operator can see that client X asserts recording
Y. Nobody else can — the public read paths expose recording ↔ vector and never a client — but the
operator can, and `ADR-0102` point 3 weighed this and kept contribution opt-in for it. This record
keeps that and says it where a user will read it.

## Decision

1. **A recording id is a claim, not a column.** A new table, `recording_claims`, holds
   `(fingerprint_hash, recording_mbid, client_id, created_at)` with the first three as its key. The
   `embeddings` table is unchanged. A row's recording is whichever MBID the most *distinct clients*
   have claimed for its hash, and is null when none have.

2. **The identifier is the MusicBrainz recording MBID, validated as a UUID and nothing more.** Not
   a track id, not a release id, not an AcoustID id. The server checks the shape and never the
   referent: it does not call MusicBrainz, now or later, because it cannot verify what it would be
   told and because `ADR-0003` runs on one small box with no room for a dependency that rate-limits.

3. **Contributors supply it; the server never derives it.** `ADR-0102` point 2, unchanged.

4. **Two ways to make a claim, and the second exists so nobody re-sends a vector to attach an id.**
   `POST /v1/embeddings` accepts an optional `recording_mbid` and records a claim alongside the
   contribution. `POST /v1/recordings/claims` accepts `{fingerprint_hash, recording_mbid,
   client_id}` for a row that already exists and records the claim alone. Without the second, a
   client backfilling ids would have to POST the vector again, and a repeat POST increments
   `contributor_count` and writes an agreement row — one installation agreeing with itself, which
   `ADR-0008` is built to measure and every client since has been taught not to cause.

5. **Reads carry the recording where they carry the hash.** `/v1/similar` neighbours gain
   `recording_mbid` and `recording_claims` (the count of distinct clients behind it); the lookup
   gains the same; and `GET /v1/recordings/{mbid}` returns the rows claimed under an id, per
   pipeline, so a client can ask "what does recording X sound like" without holding X — which was
   `ADR-0102`'s whole purpose.

6. **A claim goes out with contribution, under the same switch, and the switch's documentation
   says so.** Not a third setting. The disclosure is to the operator alone, the public surface
   exposes no client, and a user who turns contribution on has already accepted that the operator
   sees what they send. What changes is that what they send is now legible, and the README of every
   client says that in the paragraph titled "what leaves the machine".

7. **A missing id is not an error.** `ADR-0102` point 6. Most rows will have none for a long time;
   every existing path behaves exactly as it does today for them.

8. **Where ids come from is a per-client statement, recorded here so the coverage is not a
   surprise.** The beets plugin sends `mb_trackid` when present. Picard's plugin, when written,
   sends the recording id natively. Familiar sends `musicbrainz_track_id` for the 6.8% it has, once
   its column is confirmed to be the recording entity rather than the track entity. The CLI sends
   none and says so.

9. **Deletion covers claims.** `ADR-0004` point 7's takedown by hash removes the row's claims with
   it; deletion by client removes that client's claims and nothing else. A claim that outlived its
   row would be an identity for a vector the corpus no longer holds.

10. **Not decided here.** How an unowned *release* is ranked when the corpus holds embeddings per
    recording — `ADR-0102` point 7, and harder than it looks. Whether a claim is ever verified
    against a reference — that is `ADR-0007`'s shape and its precondition. And nothing about
    Familiar's backfill, which is Familiar's `ADR-0102` point 5 and is owed there.

11. **Execution order:** server — migration `012`, the claims table, the two write paths, the three
    read paths; then `clapback-client` — `recording_mbid` on `contribute`, a `claim` method, and
    `recording_mbid` in what `lookup` and `similar` return; then the beets plugin sends
    `mb_trackid`; then this record's Implementation block carries the first real coverage number.

## Alternatives Considered

- **A nullable `recording_mbid` column on `embeddings`, first-write-wins** — `ADR-0102`'s shape,
  and the smallest change. Rejected for the three reasons in the Context: it stores an unverifiable
  assertion as if it were a fact, it loses every disagreement, and it cannot be revoked per client.
  The claims table costs one migration and a join, and buys consistency with how the corpus already
  treats everything it cannot verify.

- **Make the recording id part of the primary key.** Rejected because most rows have none and
  would keep having none, and because many hashes to one recording is the normal case — every rip
  and every master of a recording is a distinct fingerprint — so the id is not a key to a row, it is
  a name shared by several.

- **Resolve ids server-side, from the hash, via AcoustID.** Impossible rather than unwise: the
  server holds a digest, not a fingerprint, and AcoustID needs the fingerprint. Stated because it is
  the first place a reader looks and because the impossibility is a consequence of the privacy
  property, not a limitation to fix.

- **Use the AcoustID track id instead of the MusicBrainz recording id.** It is what AcoustID
  returns first and it clusters fingerprints the way this corpus needs. Rejected because no client
  that would query this stores it — beets and Picard store the MusicBrainz id it maps to — and
  because an AcoustID id resolves to nothing a person can read without a second lookup.

- **A third opt-in switch for identification, separate from contribution.** More consent is
  rarely wrong. Rejected because the disclosure is to the operator alone, which is already true of
  everything a contributor sends, and because a switch most users will not understand is a switch
  most users will leave off — which leaves similarity returning hashes forever. Point 6's answer is
  to say it plainly instead.

- **Wait for Familiar's backfill to raise coverage first.** It was the plan in August. Rejected
  because it has not moved from 6.8% in two weeks and the plug-ins that now exist carry the ids
  already. The order in point 11 lets the beets plugin populate this before Familiar contributes a
  single claim.

## Consequences

- **Positive** — similarity search becomes a capability rather than an endpoint. A neighbour with a
  recording id is a title, an artist and a page; a neighbour without one is still a hash, and the
  response says which is which.

- **Positive** — the beets plugin gains the reason to install it that `ADR-0011` could only
  promise: what sounds like this, that I do not own.

- **Positive** — the corpus's stance on things it cannot verify stays uniform. Vectors, agreement,
  and now identity are all per-client assertions counted rather than a single value trusted.

- **Tradeoff** — a claim makes a contribution legible to the operator. `ADR-0102` accepted this and
  so does this record; what it adds is the sentence in every client's README.

- **Tradeoff** — with one contributor, every claim is unanimous by construction, and
  `recording_claims` of 1 carries no more weight than 0 did. The count becomes evidence only when a
  second client claims the same hash, which is the same precondition everything else has.

- **Tradeoff** — one more table, one more join on the similarity path, and a `GET` that fans out
  from one id to several rows. All small at this corpus's scale and none free.

- **Follow-up** — Familiar's `musicbrainz_track_id` must be confirmed as the recording entity
  before Familiar sends it. beets' equivalent field was, and its name was wrong; Familiar's may be
  too.

- **Follow-up** — the first coverage number after the beets plugin sends ids belongs in the
  Implementation block, dated, because it is the number that says whether this record did what it
  set out to.

- **Follow-up** — release-level ranking, `ADR-0102` point 7, becomes askable once this exists and
  should be asked with data rather than decided in advance.
