# ADR-0023: The Commons Closes, and Its Data Stays Published

Status: accepted

Date: 2026-11-02

Implementation:

- **Point 3, first half, done 2026-11-02.** The maintainer's Familiar has lookup and contribution
  switched off (Familiar `ADR-0159` point 6). No contribution arrives after this.
- **Point 3, second half, done 2026-11-03.** Familiar `ADR-0159` shipped in Familiar
  `v0.2.0-beta22` (released 2026-11-03 01:24 UTC). The maintainer's installation runs it.
- **Point 4 done 2026-11-03.** The final export ran by hand at 13:24 UTC: 26,401 embeddings and
  23,648 claims, matching `GET /v1/pipelines` (last contribution 2026-10-26). Each file was
  downloaded through its public URL and hashed, and the URLs and SHA256s are in the README. The
  five earlier weekly exports, the `monthly/` copies and `latest/` were deleted.
  **The point missed something:** both buckets had expiry lifecycle rules (`exports/` after 35
  days, dumps after 90), which would have deleted the "permanent" export around 2026-12-08. Both
  rules were replaced with ones that expire only noncurrent versions and stale delete markers.
  The export and backup timers were disabled before the instance went, so neither wrote again.
- **Point 5 done 2026-11-03.** `postgres/clapback-2026-11-03.sql.gz` (04:12 UTC, after the last
  contribution) is kept, checked by decompressing it in full: 8 tables. The 59 older dumps were
  deleted.
- **Bill confirmed 2026-11-03.** October's Lightsail charge was $13.73: $11.99 for the
  `small_3_0` bundle, $1.73 for snapshots. The two buckets cost cents.
- **Points 2 and 1 done 2026-11-03, in that order.** The `A` record `clapback` →
  `184.192.136.180` was deleted at Porkbun. All four Porkbun nameservers, 1.1.1.1 and 8.8.8.8
  answered `NXDOMAIN` before the instance was touched. Then the instance (with its seven automatic
  snapshots), the static IP `clapback-ip` and the manual snapshot
  `clapback-pre-hardening-2026-10-02` were deleted. Lightsail holds nothing.
- **Point 7 overstated what is kept.** It says the self-hosting path "still works" from
  `packages/server/`. `ADR-0013` point 7's import script was never written. What exists is the
  `COPY` SQL in `deploy/RUNBOOK.md`. A revival restores from point 5's dump, or writes the script.
- **Point 6 done 2026-11-03** in this repository: the README carries the final export and says
  the commons is closed, and the four server-dependent packages' READMEs say so. The GitHub
  repository description and the `picard-v0.2.0` release notes say the same.
- **Point 8 done 2026-11-03.** The maintainer posted both notes, written by him: on
  picard-plugins#436, which he closed, and on dj-track-similarity#2. **Every point is done.**

Supersedes [ADR-0003](ADR-0003-the-commons-runs-on-one-small-server.md),
[ADR-0011](ADR-0011-the-commons-is-what-other-tools-plug-into.md) point 1,
[ADR-0013](ADR-0013-the-corpus-is-public-data-not-just-a-public-endpoint.md) points 3 and 6, and
[ADR-0022](ADR-0022-the-commons-is-pitched-on-discovery-not-saved-compute.md). Rejects
[ADR-0021](ADR-0021-the-corpus-publishes-bridges-between-pipeline-identities.md). Executed together
with Familiar's `ADR-0159`, which goes first.

## Context

`ADR-0001` set out to build a public commons of CLAP embeddings: computed once, shared, worth
running while empty. Every accepted record since has been about making that commons correct,
legible and reachable. **It never got a second contributor.** The project's own notes have called
that the bottleneck since 2026-09-20: "the next step is somebody else replying."

**What the commons is, measured 2026-11-02.** From the public API and the weekly export:

| | |
|---|---|
| Rows | **26,401**, all one pipeline identity (`laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32`) |
| Contributors | One: Familiar, on the maintainer's library. The rows grew 25,930 → 26,401 since 2026-09-19, which is that library's new tracks |
| Last contribution | 2026-10-26 |
| Recording-keyed rows (`ADR-0020`) | **0**. The "first recording-keyed row" `ADR-0020` was waiting on never came |
| AcoustID-id claims (`ADR-0019` point 6) | none. `acoustid_claims.csv.gz` in the 2026-11-01 export is a 66-byte header |
| Picard plugin zip, `picard-v0.2.0` | **0 downloads** |
| GitHub | 1 star, 0 forks, no issues from anyone else, 30 of 30 PRs by the maintainer |
| PyPI, last month, mirrors excluded | `clapback-client` 884, `clapback-cli` 597, `beets-clapback` 493, `clapback-embed` 213 |

The PyPI figures are not users. A package with no installations reporting into the corpus,
and no second pipeline identity, still shows hundreds of monthly downloads from CI and indexers.
`clapback-embed`'s share is mostly Familiar's own builds. The corpus is the measure that counts,
and it has one contributor.

**What the outreach produced, read 2026-11-02.** Of `ADR-0011` point 5's four approaches:

- **beets#7032**: closed 2026-09-15, too new to list, and against beets' AI policy.
- **picard-plugins#436**: no reply since it was opened on 2026-09-15.
- **dj-track-similarity#2**: on 2026-09-16 the maintainer wrote "not saying no, but it is too
  early for me to take it". Nothing since.
- **KalinkaPlayer#128**, the prospective contributor who prompted `ADR-0013` to `ADR-0020`:
  **parked on 2026-09-21 and closed as not planned on 2026-10-29.** The project's notes still
  recorded "waiting on madenvel" six weeks after madenvel answered. That premise was wrong and is
  recorded here so nobody goes on waiting.

**madenvel's reasons are worth keeping, because they are structural and not about effort.** The
first is the cold start: a new pipeline identity starts at zero rows, and "the early ones pay for
the late ones". The second decided it. `export.sh` writes one file per pipeline identity, so **an
identity with one or two contributors exports a file that *is* one person's library**, joinable
to MBIDs through `claims.csv.gz` and dated to the day. The anonymity `ADR-0013` point 4 provides
comes from crowd size, and a new identity has no crowd. The period when contributing gives a user
nothing back is the period when it discloses the most about them. madenvel also asked for
contributions to be shuffled, and for a minimum contributor count before an identity is exported.
Neither exists.

**That argument applies to the corpus as it stands.** The one identity in the export has one
contributor, so the published CC0 file is the maintainer's library, every week. That is the
maintainer's own data and was published knowingly, which is why it is acceptable. It also means
the commons could not have offered a second contributor what madenvel asked for without new work.

**A guard `ADR-0013` relied on does not exist.** `packages/server/deploy/export.sh:58` says
"tests/test_export_script.py reads this file and fails if one appears", meaning a `client_id`
column. There is no `test_export_script.py` anywhere in the repository, and nothing else exercises
the script. madenvel found this. Checked 2026-11-02. The rule held because the script is correct,
not because anything enforced it.

**What it costs.** `ADR-0003` records the instance as Lightsail `small_3_0` at **$12 a month**
(bundle prices checked 2026-09-04). Backup and export storage is cents. The live bill was not read
for this record, because the AWS session had expired. The bundle should be confirmed with
`aws lightsail get-instances` before the instance is deleted.

**What depends on the server.** Familiar looks up every new track by default
(`community_cache_enabled = True`, URL hardcoded) and contributes on the maintainer's installation.
It falls back to local analysis on any failure. But a lookup to a host that **resolves and does not
answer** waits out a 10-second timeout three times (`MAX_RETRIES = 3` in Familiar's
`community_cache.py`), about 32 s per lookup and two lookups per track. That is roughly a minute
added to a 36.6 s median analysis (Familiar `ADR-0144`). A host that **does not resolve** fails instantly. How the server is
turned off decides which one every Familiar installation gets. The three plug-ins and
`clapback-cli` fail the same way and matter less: none has a measured user.

## Decision

1. **The public commons at `clapback.seethroughlab.com` is shut down.** The Lightsail instance is
   deleted, with its static IP, and any snapshots are removed. The site, the API, the backup,
   export and disk-alert timers all go with it.

2. **The DNS record is deleted, not left pointing anywhere.** The `A` record for `clapback` at
   Porkbun is removed in the same sitting as the instance, so every client fails at resolution,
   instantly, instead of timing out against an address that no longer answers. A released static IP
   can also be reassigned to somebody else's server. A client still configured with the old name
   would then send its requests, with a `client_id` and library hashes, to a stranger.

3. **Familiar stops calling the commons first** (Familiar `ADR-0159`). The maintainer's
   installation has lookup and contribution switched off through the settings API before anything
   on this side changes, so the last contribution is known before the final export is taken.

4. **One final export is taken and left published** (supersedes `ADR-0013` points 3 and 6).
   After point 3, `clapback-export.service` is run once more by hand. That export stays in
   `s3://clapback-export` permanently, and the weekly ones before it are deleted. Its file URLs and
   SHA256s go in the repository README, because the `/export` page that lists them today is on the
   instance. The CC0 dedication (`ADR-0013` points 1 and 2) stands. This is what keeps the promise
   `ADR-0013` was written to make to the first prospective contributor, that the data would outlive
   the box.

5. **One final `pg_dump` is kept in `s3://clapback-backup`**, private, and older dumps are
   deleted. It is the only copy carrying what the export strips (`client_id`, agreement history,
   `ip_stats`), and it is what a revival would restore from.

6. **The repository stays open, and `clapback-embed` stays maintained.** Familiar depends on it
   (`clapback-embed>=0.1.0,<0.2`, Familiar `ADR-0105`) and has no other CLAP runtime. **No package is
   yanked.** Yanking `clapback-embed` would break Familiar's installs, and yanking the others gains
   nothing. Their READMEs and the GitHub repository description say the commons is closed and point
   to the final export. `clapback-client`, `clapback-cli` and `beets-clapback` get no further
   releases. The Picard release's notes say the same.

7. **The server code stays in `packages/server/`, unbuilt and undeployed.** `ADR-0013` point 7's
   self-hosting path, restoring from an export, still works from it. That is the whole revival
   plan, and it costs nothing to keep.

8. **Notes to the two open threads are written by the maintainer** (picard-plugins#436 and
   dj-track-similarity#2), per `ADR-0011`'s Implementation block. KalinkaPlayer#128 is already
   closed and needs nothing, though madenvel's export finding deserves a thank-you if one is sent.

9. **Execution order:** point 3 (Familiar's settings now, Familiar `ADR-0159` built and released)
   → point 4's final export, and its URLs in the README → point 5 → confirm the bundle and the bill
   → points 2 and 1 in one sitting, DNS first → point 6's READMEs and repository description →
   point 8. `CLAUDE.md`'s "what is running" section is rewritten last, as "what was running".

## Alternatives Considered

**Keep running it at $12 a month and wait.** The commons costs little, the work is done, and
dj-track-similarity#2 is open, not refused. Rejected because waiting has been the plan since
2026-09-20 and has produced one refusal. The one prospective contributor who engaged in depth
named conditions this project has not built: a minimum contributor count before an identity is
exported, coarsened dates, shuffled contributions. Meeting them is new work for a population of
zero. A real second contributor can still restore the commons from point 5's dump, and would arrive
to a better-designed one.

**Shrink it rather than close it**: the cheapest Lightsail bundle, or scale-to-zero on another
host. Rejected because RAM is `ADR-0003` point 3's binding constraint, and the HNSW index wants
its pages resident. The 2 GB bundle is already the small end. A cold-starting similarity service
answers the one client it has more slowly, for a saving of a few dollars. It is still a server,
backups and an alert channel for one user. Familiar's cache hits are mostly its own work coming
back, which a local database already has.

**Fold the commons into Familiar's own server**, as a table beside its library. That is in effect
what it already is: one library's vectors, which Familiar keeps in `TrackAnalysis` anyway.
Rejected as a *decision* because there is nothing to fold. Familiar loses nothing it does not
already hold.

**Delete everything, the export included.** Simplest, and it removes the one public file that is
one person's library. Rejected because `ADR-0013` made a public promise that the data would outlive
the box, the data is CC0 and copies may already exist, and the person whose library it is chose to
publish it. If that choice changes, deleting the final export is one command and needs no record.

**Hand the commons to someone else to host.** Rejected for lack of a taker, not on principle.
Point 7 keeps it possible.

## Consequences

- **Positive:** About $12 a month, plus the maintenance of a public server (TLS, backups, the
  disk alert, Tailscale-only SSH) for an audience of one.
- **Positive:** The data outlives the box, as `ADR-0013` promised, in the one form that needs no
  server.
- **Positive:** Every Familiar installation fails fast once point 2 is done, including ones never
  updated to `ADR-0159`.
- **Tradeoff:** A cache hit, about 28 s saved per already-analysed track, is gone for Familiar. In
  practice that is the maintainer's own earlier work, re-served after a re-analysis or reinstall.
- **Tradeoff:** `ADR-0001` to `ADR-0022`, written between 2026-09-01 and 2026-09-20, now describe
  a system that is not running. They stay as written. The records most worth re-reading in a revival are `ADR-0007`
  (attestation, deliberately unbuilt), `ADR-0008`, `ADR-0019` (the key is path-dependent) and
  `ADR-0021`'s measurement.
- **Tradeoff:** The published export is one person's library under CC0, permanently. That is
  accepted here knowingly, and it is the condition madenvel would not ask an early adopter to
  accept.
- **Follow-up:** If the commons is ever revived, madenvel's three conditions come before any
  outreach: suppress an identity from the export below a minimum number of distinct clients,
  coarsen `created` for young identities, and let clients trickle contributions in shuffled order.
  So does the missing export test.
- **Follow-up:** `CLAUDE.md` and the memory notes that call the second contributor the bottleneck
  are rewritten when this is executed.
