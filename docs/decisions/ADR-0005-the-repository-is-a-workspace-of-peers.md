# ADR-0005: The Repository Is a Workspace of Peers

Status: proposed

Date: 2026-09-03

Extends [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 3, which said the
repository holds three members — the embedder, the tool and the server — and that the embedder "is
published for others to depend on". It says how, and it says where the members live, because today
one of them is the repository and the other is a subdirectory of it.

Implementation:
- Landed 2026-09-03, in one commit. `app/`, `tests/`, `migrations/`, `alembic.ini`, the
  `Dockerfile`, the compose files, `fly.toml`, `.env.example` and `CHANGELOG.md` moved to
  `packages/server/`; the root became a virtual `uv` workspace that builds nothing; the distribution
  is `clapback-server`. `server-ci.yml` and `embed-release.yml` are added, `__all__` widened per
  point 11, and the compose files needed no edits because `build: .` and `.:/app` are relative to the
  file that was moved.
- **Point 7 was wrong and is amended above.** A `uv` workspace has exactly one lock, at the root,
  covering every member — "the server commits a lock file; the libraries do not" is not expressible.
  The reasoning was also mistaken: a lock never ships inside a wheel, so committing one cannot pin
  what `clapback-embed`'s dependents resolve. The real defect it was reaching for is that
  **nothing was locked at all** and the `Dockerfile` re-resolved every dependency on every
  production build. One root `uv.lock` is committed and that is fixed.
- **The server had no ruff configuration**, so point 6's gate found 49 pre-existing findings on its
  first run. 48 were mechanical and are fixed; three rules are named and ignored in
  `packages/server/pyproject.toml` rather than silently absent. `DTZ003` is the one worth returning
  to: six `datetime.utcnow()` calls, deprecated in 3.12, feeding naive `DateTime` columns — the fix
  changes what is stored and is a migration rather than a lint pass.
- Nothing is published yet. `embed-release.yml` fires on an `embed-v*` tag and no tag exists, so
  Familiar's `@main` git reference stays correct until one does.
- Verified after the move: 40 embedder tests pass (6 artifact-marked skips), 11 server tests pass,
  `ruff check` is clean in both members, `alembic heads` resolves, and `app.main:app` imports with
  its 10 routes.

## Context

`ADR-0001` point 2 decided this is one repository rather than two, on MetaBrainz's resources
argument. That decision is not reopened here. What it did not say is how the members sit inside the
one repository, and the answer it inherited is that the server *is* the repository.

### What the repository actually is, measured 2026-09-03

| | |
|---|---|
| Root package name in `pyproject.toml` | **`familiar-cache`** |
| Root `pyproject.toml` dependencies | the server's — `fastapi`, `asyncpg`, `pgvector`, `alembic` |
| Root `tests/` | one file, 161 lines |
| Root `Dockerfile`, `docker-compose*.yml`, `alembic.ini`, `fly.toml` | the server's |
| `app/` | 1,511 lines of Python, 7 migrations |
| `packages/embed/` | 1,255 lines of Python, its own `pyproject.toml`, lock file and virtualenv |
| Members with CI | **one** — `.github/workflows/embed-ci.yml` |
| Members published to an index | **none** |

The two members are within twenty percent of each other in size. Nothing about their relative weight
explains why one sits at the root and the other in a subdirectory; that arrangement is a fossil of
the repository having been `familiar-cache` before `ADR-0001` renamed the project.

### The dependency that proves the point

Familiar depends on the embedder, per its `ADR-0105`, like this
(`backend/pyproject.toml:81` in that repository):

```
"clapback-embed @ git+https://github.com/seethroughlab/clapback.git@main#subdirectory=packages/embed"
```

with a comment two lines further down explaining that it "is installed from git rather than PyPI —
it is published from the clapback repository and has no release on an index yet", and a
`[tool.hatch.metadata] allow-direct-references = true` stanza that exists only to permit it.

That is the friction stated exactly. Every future client — the thing `ADR-0001` point 1 says the
corpus exists for — copies that line, that comment and that stanza, or gives up.

### The versioning hazard nobody has hit yet

The reference above pins `@main`. The package's entire purpose is that its version determines
whether two vectors are comparable: `PIPELINE_VERSION` composes the checkpoint, the front-end
version, the artifact version, the pooling version and the precision, and
`packages/embed/src/clapback_embed/__init__.py` says plainly that two vectors are comparable only if
it matches.

So the identity is versioned rigorously in code and delivered by a moving branch. A commit to `main`
that bumps `POOLING_VERSION` reaches every client on their next rebuild, silently, with no release,
no tag and no changelog entry. The `PIPELINE_VERSION` string would correctly report that the vectors
had changed — after they had already been computed and contributed. The discipline the package is
built around stops at its own front door.

### Only one member is defended

`embed-ci.yml` lints the embedder, runs its tests, and checks its front-end against `transformers`
on every push. It is, as its own comment says, "the first CI gate in this repository".

The server has none. It has one test file covering the submission-agreement measurement, no lint
job, and no gate of any kind — `fly-deploy.yml`, which used to push it to production unreviewed, was
removed under `ADR-0002` and nothing replaced it. `ADR-0003` point 7 is about to make this a public
service that accepts writes.

### Nothing here is locked, and the deployed image re-resolves on every build

`.gitignore:15` ignores `uv.lock` everywhere, and no lock file is tracked — `git ls-files` matching
`lock` returns nothing. `embed-ci.yml` even documents working around `setup-uv`'s cache because the
glob it keys on matches no file.

For a library that is defensible. For the server it is not, and the consequence is concrete rather
than theoretical: `Dockerfile` copies `pyproject.toml` alone and runs `uv sync`, so **every
production image resolves every dependency afresh from version ranges**, and two builds of the same
commit can differ. That is the reproducibility the corpus's own arguments lean on everywhere else.

### What Familiar actually depends on, audited 2026-09-03

There is one client, `ADR-0001` point 1 says it is not the owner, and it is nonetheless the thing
that must not break. Its dependency is two surfaces, not one.

**The package.** Familiar imports more than the documented API:

| import | where | in `__all__`? |
|---|---|---|
| `embed_file`, `embed_text`, `embed_audio` | `analysis.py:165`, `:201`, `smoke_test_clap.py:73` | yes |
| `PIPELINE_VERSION` | `smoke_test_clap.py:42` | yes |
| `clapback_embed.artifacts.audio_session` | `analysis.py:73` | **no** |
| `clapback_embed.artifacts.providers` | `analysis.py:63` | **no** |
| `clapback_embed.artifacts.model_dir` | `smoke_test_clap.py:39` | **no** |
| `clapback_embed.mel.SAMPLE_RATE` | `smoke_test_clap.py:74` | re-exported at top level, imported from the submodule anyway |

Three of those are not in `__all__`, so the package's real contract is wider than its declared one.
Worse for anyone planning to reorganise: `backend/tests/test_embedder_delegation.py:42-50` stubs
`clapback_embed` and `clapback_embed.artifacts` **by module path**, so the internal module layout is
load-bearing for a test suite in another repository.

**The server.** Familiar's `community_cache.py` calls six endpoints and `/health`: embeddings
(`:214`, `:289`), features (`:333`, `:392`) and analysis-detail (`:434`, `:501`). Two things follow
that this repository's own documents get wrong:

- **The legacy endpoints are in active use by the largest contributor.** `ADR-0001` point 7 said
  nothing in `features` migrates and the README calls them "legacy, not direction". Both are about
  the *corpus*, and neither licenses removing the endpoints. Deleting them breaks Familiar.
- **Familiar sends no `client_id`.** `community_cache.py` contains the string zero times. Under
  `ADR-0004` point 3 that makes every submission from the corpus's largest contributor permanently
  unconfirmable — which is correct behaviour, and not what anyone would predict from
  `contributor_count` today.

**The default host is dead in both repositories.** `community_cache.py:38` and
`app_settings.py:81` both default to `https://familiar-cache.fly.dev`, which `ADR-0002` established
is NXDOMAIN. The running instance works only because it is overridden in app settings.

### The access path nobody has written down

Familiar reaches the corpus over HTTP and always has, so the question has never come up. It is not
recorded as a rule anywhere — not in an ADR, not in the README, not in a comment on the schema.

What exists instead is a habit that happens to be right. `docker-compose.omv.yml:4` gives Postgres
no host port, noted in passing as a property of that deployment rather than as a constraint;
`docker-compose.yml:10` publishes the database on the host with nothing saying it is for development
only. A rule that
holds because no one has tried the alternative is not a rule, and `ADR-0003` is about to put this
database on a public host, next to `ADR-0001` point 3's tool — a client that will run on
contributors' machines and will want, at some point, to go faster.

### The version that has to be bumped in two repositories at once

`PIPELINE_VERSION` composes checkpoint, front-end, artifact, pooling and precision into a string.
Familiar sends `analysis_version=EMBEDDING_VERSION`, a hand-maintained integer (`config.py:122`,
currently 7) whose comment correctly states that it "is the identity of the embedding *pipeline*,
not of the checkpoint" and that "vectors from two pipelines are not comparable".

Both are right, and they are the same fact expressed twice, in two repositories, kept in step by a
human remembering. If `POOLING_VERSION` moves here and `EMBEDDING_VERSION` does not move there,
incomparable vectors are contributed under a key that asserts they are comparable. Nothing detects
this — not the server, which cannot know, and not the package, which cannot see the client. It is
the same hazard as the `@main` reference above, arriving through the corpus instead of the import.

## Decision

1. **The repository is a workspace of peer members, and none of them is the root.** The server moves
   to `packages/server/` alongside `packages/embed/`, taking its `app/`, `tests/`, `migrations/`,
   `alembic.ini`, `Dockerfile` and compose files with it. The root `pyproject.toml` becomes a `uv`
   workspace declaration that builds nothing and ships nothing.

2. **`familiar-cache` as a package name ends with the move.** The distribution becomes
   `clapback-server`. `ADR-0001` renamed the project on 2026-09-01, seven months after its first
   commit, and this is the last place the old name is load-bearing rather than historical.

3. **`clapback-embed` is published to PyPI, and that is the supported way to depend on it.** The git
   reference is a workaround for the absence of a release, and it stops being anybody's install
   instructions. Familiar's dependency becomes an ordinary version specifier, and the
   `allow-direct-references` stanza it needs goes away.

4. **The package version and the pipeline identity are different things, and neither may be inferred
   from the other.** `PIPELINE_VERSION` says whether two vectors are comparable; the package version
   says what code you installed. The binding rule is one-directional and must be stated because it is
   not obvious:

   - Any change that moves `PIPELINE_VERSION` is **at minimum a minor version bump**, and its
     release notes name the old and new identity strings. A patch release may never move it.
   - A version bump does **not** imply the identity moved. Most will not.

   A client that needs comparability checks `PIPELINE_VERSION`, never the package version. The
   version bump exists so that a human reading a changelog sees it coming.

   **A release that moves the identity also names the client-side constants that must move with
   it** — today `EMBEDDING_VERSION` in Familiar. That is a note in the release, not a mechanism, and
   the mechanism is a follow-up below.

5. **A release is a tag, and the tag is what publishes.** A workflow triggered on `embed-v*` builds
   and publishes to PyPI via trusted publishing, so there is no long-lived token in the repository.
   Publishing on every push to `main` is not the alternative here — point 4 requires that a release
   is a deliberate act with notes attached to it.

6. **The server gets the same gate the embedder has**, in the same commit as the move: lint and its
   test suite on every pull request touching `packages/server/`. `ADR-0003` point 7 makes this a
   public write endpoint; shipping that with no CI at all is not defensible now that the mechanism
   for having some exists.

7. **The workspace commits one lock file, at the root.** `.gitignore`'s blanket `uv.lock` rule goes.

   The distinction this point originally drew — an application pins what it deploys, a library must
   not pin what its dependents resolve — does not survive contact with either uv or the packaging
   model. A `uv` workspace has exactly one lock by design, covering every member; and a lock never
   ships inside a wheel, so what `clapback-embed`'s dependents resolve is set by its `dependencies`
   ranges no matter what is committed here. The rule was protecting against something that cannot
   happen.

   What the lock does pin is development, CI and the deployed image — and the server has never had
   that. `uv.lock` was gitignored while the `Dockerfile` copied only `pyproject.toml` and ran
   `uv sync`, so every production build re-resolved every dependency from ranges. That is the actual
   defect the original point was reaching for, and committing the lock fixes it.

8. **The third member is named but not created here.** `packages/cli/` is where the tool of
   `ADR-0001` point 3 will go, and this record deliberately decides nothing about what it does.
   That is `ADR-0001` deferred item 5 and needs its own record; creating an empty directory for it
   now would be a claim that the decision is made.

9. **This record moves files and changes no behaviour.** No endpoint, no schema, no vector, and no
   dependency of the server changes. It is separable from everything `ADR-0003` and `ADR-0004`
   require, and it does not block them.

10. **Nothing here changes an import path, a module path or an endpoint.** The audited surface above
    is the compatibility floor: the top-level functions, `clapback_embed.artifacts` and
    `clapback_embed.mel` as importable module paths, and all six `/v1` endpoints including the
    legacy ones. The members move; what a client imports and calls does not. A restructure that
    requires a coordinated change in another repository is not the cheap mechanical move point 1
    claims to be.

11. **The package's declared API widens to what is actually depended on.** `providers`,
    `audio_session` and `model_dir` are added to `__all__` and documented, because three imports
    that work but are not public is a contract nobody can safely refactor against — including the
    author. This is the honest direction: Familiar's use of them is reasonable, so the package
    should say so rather than leave them accidentally load-bearing.

12. **The database belongs to the server, and nothing else touches it.** Every other member of this
    workspace, the tool of point 8 included, and every external client reach the corpus **only
    through the HTTP API**. No tool ships a connection string, and no client is given one.

    This is a boundary, not a preference, because every guarantee the corpus makes lives in the
    application rather than in the schema. `ADR-0004` point 3's confirmability, point 6's
    revocation, point 9's per-client quotas and row ceiling, `ADR-0001` point 9's agreement
    recording, and whatever `ADR-0002` point 1's similarity endpoint eventually enforces are all
    code on the write path. A `psql` connection is a second write path with none of them, producing
    rows indistinguishable from contributed ones and carrying no evidence of who computed them. The
    corpus's integrity is a property of the path, so there is one path.

    It is also what keeps `ADR-0003` point 5's upgrade steps cheap: resizing, reindexing or moving
    Postgres is invisible to every client precisely because no client holds a connection to it.

    The development compose file publishes 5432 for local work and says so; that is a convenience
    on a throwaway database, not an access path, and the deployed configuration exposes no port at
    all.

## Alternatives Considered

- **Split the embedder into its own repository.** The strongest alternative, and the one worth
  re-reading in a year. The library's audience is everybody doing CLAP embeddings, which is far
  larger than the audience for this particular commons; it would get its own issue tracker, its own
  release cadence, a README that is not about a corpus, and none of the "what is this server doing
  here" confusion that a monorepo imposes on a drive-by contributor. Rejected on two grounds.
  `ADR-0001` point 2 already weighed the repository count against one maintainer's attention and
  came down on one. And the coupling that matters is the pipeline identity: a change to windowing
  moves `PIPELINE_VERSION`, the server's version handling and possibly a migration, which is one
  commit here and a coordination protocol across two repositories, forever. Point 1's restructure is
  what keeps this alternative cheap — a `git filter-repo` of one self-contained directory — if the
  library ever earns its own contributors.

- **Publish from `packages/embed/` and leave the layout alone.** Genuinely cheap: the package
  already has its own `pyproject.toml`, lock file, virtualenv and CI, so a publish workflow is the
  only missing piece and points 3 through 5 could land this afternoon. Rejected because it leaves
  the asymmetry that caused the problem. The repository would still be named for the server, still
  put the server's tests at the root, and still read to a newcomer as a service that happens to
  contain a library — while the library is the half strangers install. It also leaves point 7
  inexpressible, since there is nowhere to put a rule that distinguishes the two.

- **Pin Familiar's git dependency to a tag instead of `@main`.** This is the honest minimum fix for
  the versioning hazard, costs one line, and needs no restructure and no PyPI account. Rejected
  because it solves the hazard for exactly one client. `ADR-0001` point 1 says Familiar is the first
  client and not the owner; a fix that works only for the client who already knows the trick makes
  the second client no more likely.

- **Vendor the embedder into the server and drop the package.** Removes the boundary and every
  question in this record. Rejected outright: it contradicts `ADR-0001` point 3, and it destroys the
  reason the package exists. One implementation shared by everyone is what lets the corpus tell
  disagreement about audio from disagreement about code, and a copy inside the server is not shared
  with anyone.

- **Do nothing until there is a second client.** Defensible on the evidence — there is one client
  today and it works. Rejected on the same causation `ADR-0002` rejected deferring the endpoint for:
  a second client cannot appear through an install line that requires a git URL, a subdirectory
  fragment and a hatchling escape hatch. The friction is upstream of the demand it is waiting for.

## Consequences

- **Positive** — `pip install clapback-embed` becomes true, which is the whole of `ADR-0001` point
  3's "published for others to depend on" and the precondition for any client that is not Familiar.
- **Positive** — the silent-pipeline-change hazard closes. After point 4 and point 5, a change to
  the identity is a tagged release with notes rather than a rebuild.
- **Positive** — the server acquires a CI gate before it acquires a public write endpoint, rather
  than after.
- **Positive** — the API becomes the stated boundary rather than an accident of how the one
  existing client happens to work, before `ADR-0003` puts the database on a public host and
  `ADR-0001` point 3's tool starts running on machines this project does not control.
- **Positive** — the layout stops contradicting `ADR-0001` point 3. Three members, three
  directories, and the tool's absence becomes visible rather than implicit.
- **Tradeoff** — a large, mechanical, conflict-prone diff that touches nearly every path in the
  repository and rewrites every import path, Docker context and compose volume that names `app/`.
  It is worth doing in one commit and worth doing before there is more to move.
- **Tradeoff** — point 12 costs the tool the fastest bulk path it could have had. A client
  backfilling a large library over HTTP is slower than one issuing a `COPY`, and if that ever
  genuinely bites, the answer is a batch endpoint that keeps the guarantees rather than an
  exception that drops them.
- **Tradeoff** — publishing is a public commitment. A name on PyPI, a version history that cannot be
  rewritten, and the implicit promise that a package on an index is maintained. `ADR-0001` names
  attention as the scarce resource, and this spends some.
- **Tradeoff** — releases become a step that can be forgotten. Today Familiar gets embedder changes
  by rebuilding; afterwards it gets them when someone cuts a tag. That is the point, and it is still
  a new way for work to sit unshipped.
- **Follow-up** — Familiar's dependency and its `ADR-0105` are updated once the first release
  exists. Until then `@main` stays, because a broken client is worse than an ugly install line.
- **Follow-up** — **the two-repository version lockstep needs a mechanism, and this record
  deliberately does not invent one.** Options run from Familiar deriving `analysis_version` from
  `PIPELINE_VERSION` instead of maintaining its own integer, to the server rejecting a submission
  whose declared version it has not been told about. It is a corpus-integrity decision, it affects
  the stored key, and it deserves its own ADR rather than a paragraph in a restructuring one.
- **Follow-up** — the dead default host in `community_cache.py:38` and `app_settings.py:81` is
  Familiar's to fix, and becomes urgent at `ADR-0003`'s launch rather than now.
- **Follow-up** — Familiar sending a `client_id` is a one-line change with an outsized effect: it
  moves the largest contributor from permanently unconfirmable to countable under `ADR-0004`
  point 3. Not this record's work, but nothing blocks it.
- **Follow-up** — `CHANGELOG.md` is the server's, is still titled "Familiar Cache", and still links
  to the retired repository path. Point 1 gives it a home; what it says is a separate edit.
- **Follow-up** — the embedder needs a changelog of its own, since point 4 makes release notes the
  mechanism by which a pipeline change is announced.
- **Follow-up** — `fly.toml` moves with the server or is deleted. `ADR-0002` established the Fly app
  no longer exists; `ADR-0003` chose AWS. Keeping it "for reference" survived both.
