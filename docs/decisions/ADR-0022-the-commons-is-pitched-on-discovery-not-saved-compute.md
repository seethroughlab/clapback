# ADR-0022: The Commons Is Pitched on Discovery, Not Saved Compute

Status: accepted

Date: 2026-09-20

Implementation:

- Accepted 2026-09-20, the day it was proposed.
- **Points 4 and 5 built 2026-09-20.** The tagline is **"Find what you don’t own, by how it
  sounds."** — it carries point 3's qualifier in its own words, and uses a typographic
  apostrophe because Jinja's autoescape turns a straight one into `&#39;` and the test that the
  tagline appears verbatim would fail. The landing `<h1>` is *"What sounds like this — including
  what you don't own."* (the beets plugin's line since it shipped); the hero snippet now shows
  `Corpus().similar()` before `contribute()` rather than `lookup()`; "Why plug your tool in"
  runs *Recommendations past the edge of one library* → *Somewhere to submit to again* → *Skip
  the recompute*, the last saying in its own text why it is last; the "corpus, dated" paragraph
  states the inversion. `test_it_makes_the_case_in_order` pins the new order. The README lead is
  rewritten around the streaming comparison, and the compute argument moved under "The reference
  pipeline", where the case for one implementation lives. The CLI's card on the page still says
  nothing about similarity, because point 7 is not built.
- **Deployed 2026-09-20 ~15:20 UTC**: box at `5cff52e`, migration `016` unchanged (no schema
  change), live `<title>` and card order verified; 25,930 rows, one contributing installation.
- Point 7 (`clapback-cli similar`) and point 6 (the maintainer message) are not built.

Extends [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 1 and
[ADR-0011](ADR-0011-the-commons-is-what-other-tools-plug-into.md) point 1. It decides no schema and
no endpoint. It decides what the project says it is for — on the site, in the README, and in the
next message to a maintainer — and it names the one gap in the code that the current pitch hid.

## Context

### What the project currently says about itself

Every surface that introduces the commons leads with the same argument. The README's first
paragraph (`README.md:5-8`): *"Computing one costs seconds of CPU and a 600 MB model; comparing
two is a dot product. So it is worth computing once and sharing."* The site's tagline
(`packages/server/app/templates.py:16`): **"Compute it once. Every tool gets it back."** The
landing page's "Why plug your tool in" (`packages/server/app/templates/index.html:65-87`) gives
three reasons in this order: *Skip the recompute*; *Similarity across libraries, not one user's*;
*Somewhere to submit to again*. The proposals `ADR-0011` point 5 sent to two maintainers were
framed as contribution — offered under their own pipeline identity — and its Implementation block
records them that way.

This is the AcousticBrainz pitch: a shared cache of something expensive. It has three problems.

1. **The thing is not expensive.** The README's own figure is seconds per track. A 10,000-track
   library is hours of background CPU, once. Nobody who runs beets on a FLAC collection is
   deterred by that, and `ADR-0011`'s code survey found that the two prospective contributors
   already compute CLAP for every track they hold.
2. **The saving requires overlap, and overlap is the cold start.** A lookup pays out only when the
   commons already holds the asker's track. `ADR-0011`'s Context says so under "What does not
   change: the cold start" (line 314): *"A commons is worth exactly its coverage of the library
   asking."* With one contributor, the first tool to plug in has a near-zero hit rate, and the
   pitch has told it to expect nothing.
3. **It makes contribution altruism.** A contributor has already paid the seconds. Giving the
   vector away saves *other people* seconds. `ADR-0001` point 8 measured that passive accumulation
   does not happen; this is one reason why.

### What the project was for from the first record

`ADR-0001`'s Context, on the day the corpus became the product: Familiar's `ADR-0102` *"proposed
keying it by MusicBrainz recording id so that music* nobody here owns *becomes rankable by how it
sounds."* That is not a cache. It is a recommendation from beyond the edge of a library, and every
expensive decision since has served it: `ADR-0002` made nearest-neighbour search a hosting
requirement, and its point 4 refused to call similarity delivered until results were identifiable;
`ADR-0012` and `ADR-0019` made them so; `ADR-0011` point 4 says the recording key is *"the
difference between `/v1/similar` being a curiosity that returns hashes and being the reason to
integrate."* The code knows too: the beets plugin's `clapback-similar` command is documented as
*"what sounds like this — including music you don't own"*
(`packages/beets-clapback/beetsplug/clapback.py:12`), and its docstring calls it *"the reason to
install this that is not altruism"* (line 219).

The discovery pitch is therefore not a change of direction. It is the lede, buried under the
cache pitch since the site was written.

### The problem it names is one this audience has and streaming solves

A streaming service recommends from a catalogue it holds and listening data it collects. A tool
that manages music a person owns — beets, Picard, KalinkaPlayer, Navidrome, Familiar — has
neither, so its recommendations end at the edge of one library. Its users chose that edge on
purpose and still want to know what else sounds like what they have.

Two things follow. A pooled *sonic* catalogue is the one recommendation surface a federation of
private libraries can build that a streaming service cannot take away, because it is built from
the audio and not from a licence. And content similarity — "sounds like" — is the right signal for
this audience specifically: collaborative filtering is strongest on mainstream taste and weakest
on the long tail, and people who own FLACs and run a tagger are the long tail. `ADR-0011` records
that MetaBrainz's successor work moved *"to similarity from listening data, not audio"* (line
274); that is the ground this project is not on, and should not claim.

### The cold start inverts

`ADR-0011`'s premise — worth exactly its coverage of the library asking — is right for lookup and
wrong for discovery, and this is the reason to reorder the pitch rather than merely reword it.

Lookup value needs **overlap**: the commons must hold the asker's tracks. Discovery value needs
**non-overlap**: the commons must hold tracks the asker does not have. For a second contributor,
the same 25,930 rows (live count on the landing page, 2026-09-20; 89.6% naming a MusicBrainz
recording; one contributing installation) are a near-zero lookup hit rate and, at the same
moment, a catalogue of 25,930 recordings they can be shown. The first tool to plug in gets nothing
from the cache and something real from discovery, on the same day, from the same rows.

It also changes why anyone contributes. What a user contributes is what other users can be shown;
what they can be shown is what others contributed. The payoff is something the contributor wants
rather than a saving for a stranger. The cold start for *lookup* is untouched; this record does not
claim to solve it, only to stop leading with the value it withholds.

### What the pitch cannot promise

- **"Sounds like" is not "will like."** A CLAP neighbour is a recording the model heard as
  similar. That is a weaker recommender than collaborative filtering where collaborative
  filtering has data, and the commons has none by design (`ADR-0004`, `ADR-0013` point 4). The
  pitch says *by how it sounds* and never *good recommendations*.
- **The catalogue is one person's taste until it is not.** Every one of the 25,930 rows was
  contributed by one installation. A recommendation from the commons today is a recommendation
  from that collection. The second contributor changes this for both parties at once, which is
  the point of the previous section, but it is true today.
- **The last mile is the client's.** `/v1/similar` returns a recording id; the server never
  resolves one to a name (`ADR-0012`; the explorer resolves in the visitor's browser), and nothing
  here says where a recording can be heard or bought. The beets and Picard plug-ins print a
  MusicBrainz link (`beetsplug/clapback.py:253`, `picard-clapback/clapback/_core.py:187`). Where
  the user goes from there is the tool's job.

### Two clients that never ask

The reference client has no command that asks the commons what sounds like a track: there is no
call to `Corpus.similar()` anywhere in `packages/cli/src` (2026-09-20). The commons' first client
is the same — Familiar's backend contains no reference to `/v1/similar` (sibling checkout,
2026-09-20); its `/{track_id}/similar` route ranks within its own library. The client whose
`ADR-0102` gave this project its purpose has never consumed it. Under the cache pitch that was
unremarkable; under this one it is the gap.

## Decision

1. **The commons is pitched on discovery.** Its stated purpose, everywhere it is introduced, is
   that a tool can tell its user what sounds like a track they own — including recordings they do
   not — from a catalogue no one had to assemble first. This is `ADR-0001`'s `ADR-0102` sentence
   promoted from Context to pitch. It supersedes no decision; it reorders what the decisions were
   for.

2. **Saved compute is a supporting fact, stated after, never the lead.** Lookup-before-compute
   stays in the contract (`ADR-0011` point 2) and on the page, because a hit is still 512 floats
   for free. It is no longer the reason to plug in.

3. **The pitch promises what the corpus can deliver, in these words.** *By how it sounds*, not
   *what you will like*. *A recording you can open*, meaning a MusicBrainz id, and a hash when
   nobody has named it. Where to hear it is the client's. The commons does not become a store, a
   player or a link resolver, which `ADR-0009` point 9 already says of the tool and `ADR-0012`
   of the server.

4. **The site carries the pitch.** The tagline in `SITE` (`packages/server/app/templates.py`) is
   replaced — the sentence is copy, chosen when the page ships and recorded in this record's
   Implementation block; the direction is this record's. The landing page's "Why plug your tool
   in" leads with discovery, worded as such: what a user can be shown that they do not own. *Skip
   the recompute* moves to last. *Somewhere to submit to again* stays; it is true and it is the
   history, but it names a loss rather than a gain. The counted-live tiles stay as they are: the
   number the pitch turns on — recordings held that the asker does *not* own — cannot be counted
   for an asker the server has not met, so the page shows the count it can stand behind.

5. **The README lead mirrors the site.** The first paragraph says what a tool's user gets;
   *computing once* moves down with the reference-pipeline material, where the argument for one
   implementation actually lives.

6. **The maintainer pitch is discovery from day one.** The next message to a maintainer —
   KalinkaPlayer#128 is open, and beets is owed a re-approach on its terms — says: your users get
   *sounds like this* beyond their library from the first day, and the more of their library they
   contribute, the more they can be shown. Contribution is stated as the price of discovery and
   its source, not as a favour. Written by Jeff, with the project's AI policy read first, per
   `ADR-0011`'s Implementation block.

7. **The reference client gets the surface the pitch names.** `clapback-cli` gains a command that
   takes a track and asks the commons what sounds like it, printing owned neighbours as the user's
   files and the rest as MusicBrainz recordings — the beets plugin's `clapback-similar`, in the
   CLI's conventions. This is the ~40 lines that make `ADR-0001` point 8 and this record consistent:
   the tool is useful with the corpus empty *and* is the reference for what a tool does when it is
   not. Whether Familiar consumes `/v1/similar` is Familiar's decision; this record notes only that
   it does not.

8. **Nothing else changes.** No endpoint, no key, no schema, no threshold. Every integration stays
   opt-in and off by default (`ADR-0011` point 6). The lookup cold start stands as recorded.
   `ADR-0001` point 8 stands as a statement about the tool. `ADR-0021`'s bridges gain a reason:
   under this pitch, a second pipeline identity that cannot see the first is a catalogue its users
   cannot be shown.

9. **Execution order:** this record → point 4 and point 5 in one change, with the tagline settled
   there → point 7 → point 6, in the new framing, when Jeff next writes to a maintainer.

## Alternatives Considered

- **Keep the cache lead.** The current site, README and proposals. Rejected for the three reasons
  in the Context: the compute is cheap, the saving needs the overlap that a new contributor does
  not have, and it makes contribution altruism. It is also not what the project was for, by its
  own first record.

- **Lead with the AcousticBrainz succession — "somewhere to submit to again."** True, specific,
  and it names the exact pair of operations beets and Picard lost. Rejected because it tells a
  maintainer what they had rather than what their users get, it is a loss rather than a gain, and
  it was already tried: it is the third reason on the landing page, and beets#7032 closed on
  other grounds without it helping. It stays as history, not as the lead.

- **Build the last mile into the commons.** Resolve recording ids to names server-side, link to
  Bandcamp or a streaming service, tell the user where to hear the neighbour. Rejected: `ADR-0012`
  keeps the server from resolving an id, and the explorer already does it in the visitor's
  browser for that reason; a link resolver is a second product with a maintenance surface and a
  set of partners; and `ADR-0009` point 9 says the tool is not a player or a downloader, which
  applies with more force to the server. The last mile belongs to the tool that has the user.

- **Claim recommendation from listening data.** ListenBrainz's ground. Rejected because the
  commons holds no listening data and is designed not to — no accounts (`ADR-0004`), addresses
  and `client_id` stripped from the export (`ADR-0013`) — and because MetaBrainz already does
  this well. The commons' claim is the one MetaBrainz stopped making: similarity from the audio.

- **Reword the README and say nothing in a record.** Cheapest. Rejected because a pitch is a
  direction, the next message to a maintainer will be built on it, and the argument — that the
  cold start inverts for discovery — should sit where it can be contradicted by a measurement
  once a second library exists to measure against.

## Consequences

- **Positive:** the pitch pays out for the second user on the day they arrive, from the rows
  already held, instead of promising a saving that needs overlap they do not have.
- **Positive:** contribution acquires a self-interested reason — what you add is what you can be
  shown by others — which is the incentive the cache pitch lacked and `ADR-0001` point 8 found
  missing.
- **Positive:** the site, the README and the code agree. The beets plugin has said "the reason to
  install this that is not altruism" since it shipped; the page will say it too.
- **Tradeoff:** *sounds like* can be over-read as *will like*, and the pitch must keep the
  qualifier wherever it appears. A user disappointed by a CLAP neighbour is a user who was told
  something the corpus cannot deliver.
- **Tradeoff:** until a second contributor lands, the catalogue is one taste, and the landing page
  cannot show the number the pitch turns on. The page shows recordings held and says whose.
- **Tradeoff:** two records now pull on the reference client — locally useful with the corpus
  empty, and the demonstration of discovery when it is not. Point 7 is the reconciliation, and it
  is small.
- **Follow-up:** the site and README change (points 4 and 5), the tagline recorded here when
  chosen.
- **Follow-up:** `clapback-cli`'s similarity command (point 7), released as `clapback-cli` 0.3.0.
- **Follow-up:** the next message to madenvel, and the re-approach to beets, in this framing
  (point 6).
- **Follow-up:** once a second library has contributed, measure the inversion: for that library's
  seeds, how many of the top-10 neighbours are recordings it does not hold, and how many of those
  are named. Dated, in this record's Implementation block. That figure is the pitch, and today it
  cannot be computed.
