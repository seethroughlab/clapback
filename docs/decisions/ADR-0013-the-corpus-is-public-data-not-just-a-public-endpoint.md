# ADR-0013: The Corpus Is Public Data, Not Just a Public Endpoint

Status: accepted

Date: 2026-09-15

Implementation:
- **Accepted 2026-09-15**, the day it was proposed, with the licence-and-export coupling and the
  takedown tradeoff in point 6 accepted as written. Nothing is built: the licence statement
  (point 1), the contribution text (point 2), the export (points 3–6) and the import path
  (point 7) are all owed, in point 8's order.
- **Point 1 is stated** (2026-09-15): a `## Licence` section in the README, a licence line in the
  site footer on every page, and a sentence on `/api` beside the row counts — each naming CC0 for
  the data, MIT for the code, and this record. A site test asserts both pages carry it. Says
  nothing about the clients' switches, because point 2 is not built and the page must not claim it
  is. Deployed when the instance next pulls `main`.
- **Point 2 is written** (2026-09-15), one sentence, byte-identical in all four places so it
  cannot drift: *Everything sent is dedicated to the public domain under CC0 1.0, like every other
  row in the corpus, and may be republished in its public exports.* In `Corpus.contribute`'s
  docstring and the client README's rules (a fifth rule); printed by `clapback contribute` before
  anything is sent, dry run included; in `beets-clapback`'s docstring, its README's option comment
  and its "what leaves the machine" section; in the Picard plugin's description (what Options →
  Plugins shows) and the options-page note beside the *Contribute* checkbox. A test in each
  package asserts the text is where the switch is. Versions bumped — client 0.2.2, cli 0.1.2,
  beets 0.2.1, Picard 0.1.2 — and **not yet released**: the order is client → cli and beets →
  Picard (`ADR-0005`), and each tag is a decision to publish.
- **Points 3 to 6 are built and not yet deployed** (2026-09-15). `deploy/export.sh` runs
  `COPY` through `psql` in the existing Postgres container — one gzipped CSV per
  `pipeline_version`, `claims.csv.gz`, `manifest.json` — and uploads with `aws s3 cp` to a
  second bucket; `clapback-export.service`/`.timer` run it Sundays 05:12 UTC, an hour after the
  backup; `iam-export-policy.json` grants `PutObject` and `ListBucket` on three prefixes and no
  delete; `s3-export-bucket-policy.json` makes those prefixes public-read. `/export` renders the
  manifest — a copy the script leaves beside the compose project, mounted read-only into the
  container, so the page makes no outbound request — and says "decided, not yet published" when
  there is none; `/export/latest.json` redirects into the bucket, or 404s until one is
  configured. RUNBOOK section 10 has the bucket, the policies, the timer and the takedown step.
- **Two things came out differently from the Decision.** *Retention* (point 6) was written as
  "keep the last four weekly and the first of each month"; built, it is a 35-day S3 lifecycle
  rule on `exports/` plus a `monthly/YYYY-MM/` copy the script writes only when that prefix is
  empty. Same effect, but the host never holds `s3:DeleteObject` — the property the backup's
  IAM policy already had and this record would have been wrong to give up for a retention
  loop. *Column order* (point 3) is `fingerprint_hash, named, pipeline_version, contributor_count,
  created, embedding` rather than the order the Decision listed, so `scripts/build_map.py`'s
  reader — hash first, vector last — takes the published file unchanged; a `named` column was
  added for the same reason. *A takedown regenerating the export* is a runbook step
  (`systemctl start clapback-export.service`), not automation: the admin route runs in the
  container and the exporter on the host, and wiring one to the other is not worth a second
  channel for an event that has never happened.
- **The script was run against a real schema before it was committed, and that found a bug.**
  Against the dev compose stack, migrated to head and seeded with two pipeline identities (one
  containing a quote, to exercise the dollar-quoting) and overlapping claims, the first run
  exported one pipeline. `docker compose exec -T` inside the `while read` loop consumed the
  rest of the pipeline list as its own stdin. Fixed with `< /dev/null`; the second run produced
  both files, 512 floats a row, no `client_id` anywhere in the output, `named` and
  `claim_count` correct, and `build_map.py` read the file as-is. Tests pin the script's text:
  no private column or table in code, hash first and vector last, `HEADER`, day-precision
  dates, never the backup bucket, and a writer policy with no delete. Not exercised: S3
  itself, `aws` was a stub writing to a directory. That is what the runbook's "download the
  manifest from somewhere that is not the instance" step is for.

Extends [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 1,
[ADR-0003](ADR-0003-the-commons-runs-on-one-small-server.md) point 6 and
[ADR-0004](ADR-0004-contributors-are-identified-but-not-accounts.md) points 5, 7 and 8.

## Context

`ADR-0001` point 1 calls this project a public commons. As of 2026-09-15, "public" means one thing:
`ADR-0004` point 8's unauthenticated reads over HTTP — anyone can look up a hash, ask what a vector
is near, or browse a row. Nobody can take the corpus away with them. There is no export, no download
URL, and no statement of what licence the data is under; the repository's `LICENSE` is MIT and covers
the code. A commons whose data can only be reached one row at a time, through one operator's server,
under no licence, is a service with a generous API, not a commons.

**This was a premise nobody examined, and a maintainer found it.** The question arrived on
2026-09-15 in the first substantive reply to `ADR-0011` point 5's outreach
([madenvel/KalinkaPlayer#128](https://github.com/madenvel/KalinkaPlayer/issues/128)): *how do you
plan to sustain the public service, and would corpus exports be available for independent use or
self-hosting?* The honest answer was "one small box, nightly private backups, and no" — and the
first prospective second contributor asking it before contributing anything is the right order.
A tool author deciding whether to send their users' embeddings somewhere is entitled to know that the
data will outlive the box, that they could run the same corpus themselves, and under what terms.

### What exists to build on, as of 2026-09-15

- **A nightly dump that is private for good reason.** `packages/server/deploy/backup.sh` writes a
  plain `pg_dump --clean --if-exists` of the whole database, gzipped, to `s3://clapback-backup` on a
  systemd timer (`ADR-0003` point 6). It is a backup, not an export: it carries every table,
  including `ip_stats` (addresses), `banned_ips`, and the `client_id` on every embedding, claim and
  agreement row. It must never be public, and nothing here changes that.
- **One export that already has a reader.** `packages/server/scripts/build_map.py` reads a
  `COPY ... TO STDOUT WITH (FORMAT csv)` of `(fingerprint_hash, named, embedding::text)` for one
  `pipeline_version`, produced by hand on the instance and copied off. The map on the landing page is
  built from it. So the shape of a useful export is known, and the fact that it is produced by hand
  and never published is the gap.
- **A row that is safe to publish, and columns that are not.** An `embeddings` row is
  `(fingerprint_hash, pipeline_version, embedding, analysis_version, clap_model_version,
  contributor_count, created_at, last_accessed_at, client_id)` (`app/db/models.py`). The hash is a
  one-way digest of a fingerprint; the vector is 512 floats; the pipeline identity is a public string.
  `client_id` is a random per-installation token that `ADR-0004` point 5 says must never become a
  person — but the *set* of hashes one `client_id` contributed is a person's record collection,
  which is exactly the kind of profile that point exists to prevent. `recording_claims` is
  `(fingerprint_hash, recording_mbid, client_id, created_at)`, with the same column to strip.
- **Prior art that already answered the licence question.** MusicBrainz's core data is CC0
  ("effectively placing the data into the Public Domain"), and AcousticBrainz — the project
  `ADR-0001` is written against — stated that "all of the data contained in AcousticBrainz is
  licensed under the CC0 license". Both verified 2026-09-15 from their own sites. A corpus keyed on
  AcoustID fingerprints and named with MusicBrainz ids, offered to the same community, has no reason
  to be more restrictive than the data it points at.
- **Size, measured 2026-09-15.** 25,886 embedding rows (the landing page's live count), all under
  one pipeline identity, from one contributing installation. 512 × float4 is 53 MB raw; as
  `COPY` text it is roughly three times that before compression. Weekly, that is a rounding error
  against `ADR-0003`'s budget.
- **Consent is not retroactive here, by luck rather than design.** Every row in the corpus today
  came from one installation: Familiar, run by this project's author (`ADR-0011`'s Implementation
  block records that neither plug-in has contributed live). There is nobody to ask. That will stop
  being true the day a second contributor arrives, which is why the licence has to be stated
  *before* that day and shown at the point of contribution, not inferred afterwards.

### The tension this record has to hold

`ADR-0004` point 7 requires a delete path — takedown for legal requests, retraction for poisoned
rows — and the admin API has one. A published, CC0 snapshot cannot be recalled from anyone who has
already downloaded it. `ADR-0001` already tells the takedown story to "assume [inversion] may improve
rather than assert it safe". A public export makes that a permanent condition rather than a
theoretical one, and this record has to say so rather than pretend a snapshot can be un-published.

## Decision

1. **The corpus data is dedicated to the public domain under CC0 1.0.** Every row a client can read
   — embeddings, pipeline identities, recording claims, contributor counts — is CC0. The code stays
   MIT; the two licences cover different things and are stated separately. CC0 rather than an
   attribution or share-alike licence because there is nobody to attribute: contributors are
   installations, not people (`ADR-0004` point 5), and a licence whose conditions cannot be met is
   a licence nobody can comply with.

2. **Contribution is a CC0 dedication, and the client says so where the switch is.** `clapback-client`'s
   `contribute` docstring, the CLI's `contribute` subcommand help, `beets-clapback`'s `contribute:` option
   documentation and the Picard plugin's *Contribute* checkbox text each state, in one sentence, that
   what is sent is dedicated to the public domain. This is the only place consent can be collected
   from a self-issued, unregistered client, so it is collected there. A client that predates this
   text is not retroactively bound; as of this record's date there is none that has contributed.

3. **A weekly export is published, and it is not the backup.** A second script beside
   `backup.sh`, on its own timer, writes per-`pipeline_version` CSV files in the exact shape
   `build_map.py` already reads — `COPY (SELECT fingerprint_hash, pipeline_version,
   embedding::text, contributor_count, created_at::date ...) TO STDOUT WITH (FORMAT csv)` — plus one
   CSV of `(fingerprint_hash, recording_mbid, claim_count)` and a `manifest.json` carrying the export
   date, row counts per pipeline, the licence, and a schema version. Produced by `psql` inside the
   existing Postgres container, so it adds nothing to the deployed image.

4. **`client_id` never appears in an export, and neither does anything from `ip_stats`,
   `banned_ips` or `submission_agreement`.** Agreement is published as `contributor_count` on the
   row — the count, never the parties. `created_at` is exported at day precision, so a batch cannot
   be reassembled into one installation's contribution session either. This is `ADR-0004` point 5
   applied to the export: the commons publishes what is known about recordings, and nothing about who
   knew it.

5. **The export lives in its own public bucket, never the backup bucket.** `s3://clapback-export`
   (or its successor), public-read, written by credentials that can write nothing else — the same
   posture as `ADR-0003`'s `iam-backup-policy.json`, for a second principal. `clapback-backup` holds
   the dump with every private column in it and stays private. The application serves
   `/export` as a page and `/export/latest.json` as a redirect to the current manifest, so the URL
   people link to is on the commons's own domain and the bucket can move.

6. **Retention is bounded, and a deletion propagates to the next export.** The bucket keeps the
   last four weekly exports and the first export of each month; older ones are removed. A row
   deleted through `ADR-0004` point 7's admin path is absent from the next export and, if the
   deletion is a takedown rather than a retraction, the current export is regenerated immediately
   rather than at the next tick. What this cannot do is recall a copy already downloaded, and the
   `/export` page says so in plain words.

7. **Self-hosting is documented, and it is the one sanctioned direct-database write.** `ADR-0005`
   point 12 makes the API the only way into *this* corpus. An operator loading an export into
   *their own* instance is a different case: they own the box, the export carries
   `contributor_count` so the row's meaning survives, and there is no write-path guarantee to bypass
   because none of the guarantees are about them. `scripts/import_export.py` loads a manifest's files
   with `COPY`, and the README gains a "run your own" section pointing at it and at
   `deploy/RUNBOOK.md`. A mirror is another instance with the same key and the same identity
   strings; it is not a federation, and no sync protocol is decided here.

8. **Execution order:** point 1's licence statement on the site and in the README (a sentence;
   nothing depends on code) → point 2's contribution text in the client and both plug-ins → points 3
   to 6's export script, bucket, timer and `/export` page → point 7's import script and README
   section. The licence goes first because it is what the KalinkaPlayer thread is owed and because a
   second contributor could arrive before the export ships; the export can follow at the pace of
   `ADR-0003`'s one-small-box budget.

## Alternatives Considered

- **A paginated export endpoint (`GET /v1/embeddings?after=…`) instead of files.** Rejected. A
  full scan of the `embeddings` table on the `ADR-0003` instance competes with the lookups the
  corpus exists to answer — `ADR-0011`'s site rebuild removed three full-table scans from the landing
  page for exactly that reason — and a mirror wants a file it can load, not 26,000 requests. An
  endpoint could come later for incremental sync; it is not the first thing.
- **Publishing the nightly `pg_dump` and calling it the export.** Rejected, and this is the one that
  would have been easy. The dump carries `client_id` on every row, IP addresses in `ip_stats`, and
  `banned_ips`; stripping them from a plain-SQL dump is a text transformation on a file that also
  carries the schema, and one mistake publishes a contributor's library or an address list. The
  export is a separate query that selects what is public rather than a dump that removes what is not.
- **Parquet.** Better for the vectors — typed, columnar, a fifth of the size — and rejected for now
  because producing it means a Python dependency in the server image or a second container, and
  `CLAUDE.md`'s rule is that the server keeps its own dependencies and nothing added to the
  repository lands in the deployed image by accident.
  `psql`'s `COPY` needs nothing. Revisit when the corpus is large enough that a CSV export is a
  problem for the people downloading it; at 25,886 rows it is not.
- **CC BY 4.0 or ODbL instead of CC0.** Rejected for the reason in point 1: attribution requires
  someone to attribute, and `ADR-0004` point 5 forbids a contributor from being anyone. ODbL's
  share-alike would also make embedding a copy of the corpus inside a tool a licensing question for
  that tool, which cuts directly against `ADR-0011`'s premise that the commons is what tools plug
  into. Both MusicBrainz and AcousticBrainz reached the same conclusion for the same data.
- **Cloudflare R2 for the bucket, for free egress.** Deferred rather than rejected. `ADR-0003` put
  everything on one AWS account on purpose, and at this size a hundred full downloads a month cost
  under two dollars in S3 egress. If exports become popular enough for egress to matter, moving the
  bucket is a one-line change behind the `/export/latest.json` redirect point 5 puts in front of it.
- **Not publishing exports until there is a second contributor.** Rejected. The first prospective
  second contributor asked for exports as a condition of deciding, and the licence question has to be
  settled before their rows arrive, not after. A commons that promises to become open once it is
  worth opening has the order backwards.

## Consequences

- **Positive** — "public commons" becomes literally true: the data can leave the server, under terms
  anyone can rely on, in a shape that already has a reader in this repository.
- **Positive** — a prospective contributor's sustainability question has a concrete answer: if the
  box dies, the corpus exists in a public bucket and anyone with the repository can stand it back up.
- **Positive** — the licence is decided while there is exactly one contributor and no consent to
  collect retroactively. A week later that may not be true.
- **Tradeoff** — a published CC0 snapshot cannot be recalled. `ADR-0004` point 7's takedown becomes
  "absent from the next export", not "gone". `ADR-0001` already assumed inversion may improve; this
  record accepts that the corpus's answer to a takedown request is bounded by what was already
  downloaded, and says so on the export page rather than in a footnote.
- **Tradeoff** — an export without `client_id` cannot carry `ADR-0008`'s agreement records, only
  the count. A mirror knows *how many* installations confirmed a row, never which; a mirror cannot
  therefore apply `ADR-0004` point 6's revocation to imported rows. That is the price of point 4 and
  it is the right one.
- **Tradeoff** — a second timer, a second bucket, a second IAM principal on a box whose entire
  design is that there is little to babysit. `ADR-0004` point 9's disk alert took six days to
  actually deliver; the export timer needs the same "a green timer is not evidence anyone would
  notice" scrutiny, and the manifest date on `/export` is what makes a stalled export visible.
- **Follow-up** — the contribution text in point 2 is four small doc changes across three packages
  and a Picard options page; each is a release of that package (`ADR-0005`'s release order applies).
- **Follow-up** — `build_map.py` should read the published export rather than a hand-made one, so
  regenerating the map no longer requires a shell on the instance.
- **Follow-up** — an incremental sync endpoint, if a mirror ever wants to stay current rather than
  reload weekly. Not decided here; it is the federation question, and it is premature with one
  contributor.
