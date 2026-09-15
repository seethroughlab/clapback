# beets — list `beets-clapback` under "Other Plugins"

**Where:** a pull request to `beetbox/beets`, editing `docs/plugins/index.rst`. The list is
alphabetical; `beets-clapback` goes between `beets-check` and the cmus entry. CONTRIBUTING asks for a
changelog entry for code changes; a listing is docs-only and the recent listing PRs do not add one.

**Checked 2026-09-15:** the section starts at line 412 (`Other Plugins`), `beets-check_` is at 469
and its link target at 620; the entry format is `name_` + an indented one-line description + a
`.. _name: url` target near the end of the file.

## The diff

```rst
 beets-check_
     Automatically checksums your files to detect corruption.

+beets-clapback_
+    Fetches and contributes CLAP audio embeddings through the clapback commons
+    — ``absubmit`` reborn — and finds what sounds like a track across libraries.
+
 `A cmus plugin`_
     Integrates with the cmus_ console music player.
```

```rst
 .. _beets-check: https://github.com/geigerzaehler/beets-check

+.. _beets-clapback: https://github.com/seethroughlab/clapback/tree/main/packages/beets-clapback
+
 .. _beets-copyartifacts: https://github.com/adammillerio/beets-copyartifacts
```

## PR title

    docs: list beets-clapback under Other Plugins

## PR body

> Adds `beets-clapback` to the community plugin list.
>
> It is the fetch-and-submit pair that `acousticbrainz` and `absubmit` were, for a different
> payload: CLAP audio embeddings — 512-float vectors describing what a recording sounds like —
> in a public commons keyed on the SHA256 of the AcoustID fingerprint the `chroma` plugin already
> stores. `beet clapback` looks a track up, embeds it locally only if the commons lacks it, and
> contributes only under `contribute: yes` (off by default). `beet clapback-similar` asks what
> sounds like a track across every library that has contributed, and answers with MusicBrainz
> recording ids where anyone has named a row.
>
> Why embeddings rather than the descriptors AcousticBrainz pooled (#4627): an embedding from a
> pinned pipeline is reconcilable between contributors — two people contributing the same
> recording can be told whether they agree — which the bpm/key/mood estimates never were.
>
> On PyPI as `beets-clapback` (0.2.0, MIT). Plugin: <https://github.com/seethroughlab/clapback/tree/main/packages/beets-clapback>.
> The commons and its API: <https://clapback.seethroughlab.com>.
