# ADR-0004: Contributors Are Identified, But Not Accounts

Status: accepted

Date: 2026-09-03

Extends [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md), whose deferred item 3
this answers, and is a prerequisite of
[ADR-0003](ADR-0003-the-commons-runs-on-one-small-server.md) point 7, which makes unauthenticated
writes a launch blocker.

Implementation:
- Accepted 2026-09-03. Point 1 is partially built; nothing else is.
- **A `client_id` is accepted and stored, but nothing counts by it.** The field is optional on
  `POST /v1/embeddings` (`app/api/routes.py:66`) and recorded against each measured submission
  (`app/db/models.py:96`, migration `007_submission_agreement`), which is point 3's shape: accepted,
  and not confirmable without one. **Point 4 is not done** — `contributor_count` is still
  incremented once per POST in `contribute_embedding`, so it counts submissions rather than distinct
  clients and must not be read as independence.
- Points 6 and 7 are entirely owed. There is no revocation path and **no `DELETE` route anywhere in
  the API**, which point 7 makes non-optional before the endpoint is public.
- Point 9's ceiling on corpus rows is not implemented. It is the one bound that needs nothing from
  identity, which makes it the cheapest of these to land first.

## Context

`ADR-0001` deferred "identity, revocation and deletion" and described the state as *"IP-only
identity, no delete path anywhere, and flagging that is enforced nowhere."* Two of those three are
accurate. Audited 2026-09-03:

- **IP-only identity: true.** Nothing distinguishes contributors beyond the address a request came
  from. `client_id` exists in the open Phase 0 pull request (`#1`) and is not merged.
- **No delete path: true.** There is no `DELETE` route anywhere, admin included.
- **Flagging enforced nowhere: half wrong.** There are two separate mechanisms and they behave
  differently. `IPStats.flagged` is advisory — it colours the admin dashboard and blocks nothing.
  `BannedIP`, however, is enforced: `IPBanMiddleware` checks it on every request and returns 403.
  So a working ban path exists; what does not is any way to attribute a bad *submission* to anything
  finer than an address.

### What identity is actually for here

The instinct is that identity prevents abuse. It does not, and designing as though it does would
produce a signup wall that solves nothing.

`ADR-0001` point 6 already decided where the corpus's protection lives: **a vector is confirmed by
independent parties computing the same thing**, and a lone contributor's submissions never reach
that bar however many they send. Reference-clip attestation catches a wrong checkpoint or a broken
install. Rate limits bound how fast anyone can write — though not, as the next section shows, how
much. Between them, the failure modes identity is usually reached for are already addressed.

What is *not* addressed without it:

- **Counting independence.** Consensus is meaningless if two submissions from one machine can look
  like two contributors agreeing. Today `contributor_count` increments when the same address
  resubmits, which is why the 86 rows above 1 are not 86 independent agreements.
- **Attributing a batch.** If a contributor's pipeline is subtly wrong, the corpus needs to identify
  and retract *their* submissions without touching anyone else's.
- **Revoking without collateral.** Banning an IP bans everyone behind it — a household, an office,
  a university.

That is a narrower job than "authentication", and it is worth naming precisely, because the narrow
version can be free and frictionless while the broad version cannot.

### Rate limits bound the rate, not the total

The reasoning above leans on rate limiting to handle volume. It does not, and the gap only became
visible once the per-row cost was measured for `ADR-0003`.

Contribution is limited to 30 per minute **per address**, with no cap on corpus size and no
per-contributor quota. At the measured 5.4 KB per vector — 2,830 bytes of table and TOAST plus
2,724 of HNSW index:

    30/min x 60 x 24  =  43,200 rows/day, per address
                      =  233 MB/day, per address

Against the 60 GB of the instance `ADR-0003` point 10 selects, one address fills the disk in about
seven months, ten in about three weeks, and a hundred in two days. **A full disk stops Postgres**,
which takes the service down rather than degrading it.

This is not data poisoning — consensus already denies an attacker anything durable — and it is not
solved by identity, since identifiers rotate. It is a resource limit, and it has to exist separately.

### The thing self-issued identity cannot do

A client identifier that the client generates is trivially regenerated. Anyone determined to poison
the corpus rotates it and continues. **This design does not stop them, and it should not be
described as though it does.**

It is defensible anyway because consensus is what actually protects the corpus: unconfirmed vectors
are served as unconfirmed, and a rotating attacker never accumulates the independent agreement that
would confirm anything. Identity makes honest contribution attributable and independence countable.
It is not a security boundary, and calling it one would be the sort of claim that gets believed and
then relied upon.

## Decision

1. **Every contribution carries a client identifier.** An opaque token, at least 32 characters,
   supplied by the contributor.

2. **It is self-issued and requires no registration.** A client generates one on first run — a
   random UUID is sufficient — and reuses it. There is no signup, no email, no key exchange, and no
   moment at which a would-be contributor is asked for anything. `ADR-0001` point 5 requires the
   tool to be worth running before the corpus is worth querying; a registration wall in front of
   contribution would invert that.

3. **A contribution without one is accepted but can never be confirmed.** Not rejected: existing
   clients predate this and must keep working, and a submission that cannot join a quorum is still
   evidence. It simply does not count toward `ADR-0001` point 6's independent agreement, because an
   unattributed submission cannot be shown to be independent of any other. This makes the identifier
   worth sending without ever making it mandatory.

4. **Independence is counted by client, never by address.** `contributor_count` becomes a count of
   distinct client identifiers. The existing figure is not that and should not be read as it: the
   same machine resubmitting after a version bump increments it today.

5. **A client identifier is not a person, and must not become one.** No email, no name, no account
   recovery, nothing that could link it to an individual. It identifies an *installation*, which is
   what independence-counting needs, and `ADR-0001`'s privacy position depends on the corpus staying
   free of personal data.

6. **Revocation operates on client identifiers, and cascades.** Revoking one marks its submissions
   unconfirmed and excludes them from future quorum. IP banning stays for what it is good at —
   volume abuse and denial of service — and stops being the instrument for bad *data*, which it
   punishes a whole household for.

7. **A delete path exists before the endpoint is public.** Non-optional. A public corpus needs
   takedown for legal requests and retraction for poisoned recordings, and there is no `DELETE`
   route anywhere today. Deletion is by fingerprint hash and by client identifier, admin-only.

8. **Reads stay unauthenticated.** The corpus is public. Requiring identity to *query* would make it
   a members' club, and nothing about a lookup needs attribution.

9. **Writes are bounded by total, not only by rate.** Three limits, in increasing order of what
   they require:

   - **A ceiling on corpus rows**, checked on write and rejecting past it with a clear error. Raised
     deliberately as the corpus grows, so growth is a decision rather than a surprise. This works
     today and needs nothing from identity.
   - **Disk alerting before Postgres dies**, not after. A full disk is an outage; 80% of one is a
     Tuesday afternoon.
   - **Per-client-identifier quotas** once point 1 is in place. This is the second reason to send an
     identifier — the first being confirmability under point 3 — and it turns identity into
     something with a benefit attached rather than an altruistic act.

   None of these stop a determined attacker rotating addresses and identifiers. They stop the
   service falling over while somebody notices.

10. **Identity is not described as abuse prevention, in code or documentation.** Self-issued
   identifiers rotate. The protection is consensus, attestation, rate limiting and
   point 9's total bounds; this is bookkeeping that makes consensus countable. Anywhere the distinction blurs, it will eventually be
   relied on.

## Alternatives Considered

- **Server-issued tokens** (`POST /v1/clients` returns one). Marginally more control: the server
  chooses what to hand out and can refuse. Rejected because it is the same strength for more work —
  a token obtained without registration is exactly as rotatable as one generated locally — and it
  adds a round trip and a failure mode to every first contribution. Worth revisiting only alongside
  something that makes issuance *cost* the requester.

- **Accounts, with an email.** Real accountability, and the only option that makes revocation stick.
  Rejected on `ADR-0001` point 5 and point 8: the project has effectively one contributor, and a
  signup wall at that moment guarantees it keeps having one. It also puts personal data in a corpus
  whose privacy argument rests on containing none.

- **Keep IP as the identifier and do nothing.** Free, already built, and the ban path works.
  Rejected because it cannot distinguish two machines behind one address from one machine
  submitting twice — which is precisely the question consensus asks — and because revoking by
  address punishes everyone behind it.

- **Require the identifier, rejecting submissions without one.** Cleaner data, and no
  unattributable rows. Rejected because it breaks every client in the field on the day it ships, for
  a corpus that has nine contributing addresses. Point 3's shape — accepted but never confirmable —
  gets the same outcome by incentive rather than by force.

- **Proof of work on contribution.** Makes rotation expensive, which is the one thing self-issued
  identity cannot do. Rejected as disproportionate: it taxes every honest contributor to inconvenience
  an attacker who has not appeared, against a corpus where consensus already denies them anything
  durable. Reconsider if poisoning is ever observed rather than imagined.

## Consequences

- **Positive** — `ADR-0001` point 6's consensus becomes countable. Independent agreement can be
  distinguished from one machine repeating itself, which it currently cannot.
- **Positive** — revocation stops requiring collateral damage. A bad batch is retractable without
  banning an address that may serve a building.
- **Positive** — the launch blocker in `ADR-0003` point 7 is answered without a registration wall,
  so contribution stays as frictionless as `ADR-0001` point 5 needs.
- **Tradeoff** — **the existing 21,924 embeddings have no client identifier and never will.** Under
  point 3 none of them can be confirmed. `ADR-0001` point 10 already decided they enter unconfirmed,
  so this changes nothing about their status — but it does mean the corpus starts with its entire
  contents unconfirmable until independent clients resubmit the same recordings.
- **Tradeoff** — `contributor_count` changes meaning under point 4. Any figure quoted from it
  before this — including in `ADR-0001` and this repository's README — counts resubmissions.
- **Tradeoff** — a determined bad actor rotates identifiers freely. Point 10 makes that explicit
  rather than hoping nobody notices, and accepts it because consensus is the actual defence.
- **Tradeoff** — point 9's row ceiling means the corpus can refuse an honest contribution because it
  is full. That is the correct failure — a rejected write is a message and a full disk is an outage —
  but the ceiling has to be raised deliberately, and forgetting to raise it looks exactly like an
  attack from the contributor's side.
- **Follow-up** — what confirmation requires. This makes independence countable and does not decide
  *how many* independent clients confirm a vector, or what threshold of agreement counts. That needs
  the Phase 0 measurement, which `ADR-0001` records cannot accumulate passively with one real
  contributor.
- **Follow-up** — whether `IPStats.flagged`, which blocks nothing, should be removed or wired to
  something. Two mechanisms that look alike and behave differently is how an operator comes to
  believe they have banned somebody.
