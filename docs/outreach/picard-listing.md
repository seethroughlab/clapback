# Picard — add the plugin to `metabrainz/picard-plugins`

**Where:** a pull request to `metabrainz/picard-plugins`, branch `2.0`, adding
`plugins/clapback/` — the source directory, not the zip. Their README: "If you're a plugin author
and would like to include your plugin here, simply open a pull request", and new plugins "should be
under the GNU General Public License version 2 or a license compatible with it". MIT is compatible.

**Checked 2026-09-15:** the repository's `2.0` branch holds one directory per plugin under
`plugins/`, each with `__init__.py`; multi-file plugins (`lrclib_lyrics`, `acousticbrainz`) are
directories. Picard 3's plugin system is a separate registry and is not this PR — see
`ADR-0011`'s Implementation block for why the port waits.

## What goes in

`plugins/clapback/` = the contents of `packages/picard-clapback/clapback/` exactly as released in
`picard-v0.1.1`: `__init__.py`, `_core.py`, `clapback_client/` (four files), `LICENSE`. Nothing is
rewritten for the listing; the release zip and the listed directory are the same bytes.

## PR title

    Add clapback: look up, name and contribute CLAP embeddings; ask what sounds like a track

## PR body

> Adds the **Clapback** plugin (MIT; Picard 2.6–2.13).
>
> Picard fingerprints every file it scans and knows its MusicBrainz recording id — exactly the
> pair the [clapback](https://clapback.seethroughlab.com) commons keys on: a public corpus of CLAP
> audio embeddings (512-float vectors describing what a recording sounds like), keyed on the
> SHA256 of the AcoustID fingerprint, with recording ids attached by the contributors who hold the
> audio. It is the fetch-and-submit pair the AcousticBrainz plugins were, for a payload that can be
> reconciled between contributors.
>
> Two context-menu actions on tracks and files:
> - **Clapback: look up in the commons…** — per file, whether the commons holds the recording
>   (`~clapback_status` in the metadata panel). With *Contribute* on, a held row is **named**
>   with the recording id Picard already has; an absent one is embedded and contributed only if
>   `clapback-embed` is installed.
> - **Clapback: what sounds like this…** — the nearest recordings across every library that has
>   plugged in, with MusicBrainz links for the named and hashes for the rest.
>
> **Lookup-only by default.** A bundled Picard cannot `pip install`, so the plugin carries a
> verbatim copy of the stdlib-only `clapback-client` and computes nothing without an embedder —
> it says so in its options page. Files without a fingerprint go through Picard's own fpcalc.
>
> Nothing leaves the machine until *Contribute* is turned on: then a one-way fingerprint hash, a
> vector if one was computed here, and the MusicBrainz recording id. Never audio, paths or other
> tags. The options page says this where the switch is.
>
> Tested against Picard 2.13.3 headless (registrations, options round-trip, installing the zip
> through `PluginManager`); source and tests:
> <https://github.com/seethroughlab/clapback/tree/main/packages/picard-clapback>.
