# Outreach

`ADR-0011` point 5's last step: the messages that ask somebody else to say yes. Drafted
2026-09-15, in the order the record fixed, after reading each project's code rather than its
README. Each file is one message, ready to paste, with a short note on what was checked.

| # | to | shape | file |
|---|---|---|---|
| 1 | beets | docs PR listing `beets-clapback` | [`beets-listing.md`](beets-listing.md) |
| 2 | Picard | PR adding the plugin to `picard-plugins` | [`picard-listing.md`](picard-listing.md) |
| 3 | dj-track-similarity | an issue proposing opt-in contribution, with a PR offered | [`dj-track-similarity.md`](dj-track-similarity.md) |
| 4 | KalinkaPlayer | an issue proposing lookup-before-embed and contribution | [`kalinkaplayer.md`](kalinkaplayer.md) |

**What the code survey changed (2026-09-15).** `ADR-0011` point 5 said the dj-track-similarity ask
was to "route CLAP through `clapback-embed`". Both dj-track-similarity and KalinkaPlayer compute
CLAP from `lukewys/laion_clap`'s `music_audioset_epoch_15_esc_90.14.pt` — the music-specialised
checkpoint — over 10-second windows, not from `laion/clap-htsat-unfused` whole-track means. A pull
request that swapped their checkpoint would ask their users to recompute every vector so that ours
could compare with them. The corpus key admits any pipeline identity at 512 dimensions
(`ADR-0011` point 3), so the honest ask is **contribute under your own identity**: their users skip
what other installs of the same tool computed and get similarity among themselves; comparability
with the existing rows is a separate conversation, recorded below as an open question.

**The open question this raises for clapback.** Two independent tools converged on the same music
checkpoint. If both plug in under one shared identity, the commons holds two pipelines with two
populations, and the more-used one might not be ours. That is not a problem the outreach should
pre-empt — it is `ADR-0002`'s "which pipelines does the commons document as reference" question,
and it needs its own record once there is a second contributor to have it with.
