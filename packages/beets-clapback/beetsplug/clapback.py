"""beets-clapback — look up, embed, and (if you say so) contribute.

`ADR-0011` point 5's first integration, and `absubmit` reborn with a better
payload. beets shipped `acousticbrainz` and `absubmit` for years; both were
deprecated when AcousticBrainz died, and the suggested replacement was to compute
locally and keep the results. This is the other half back.

    beet clapback                # every track: look up, else embed; contribute if enabled
    beet clapback -p             # say what would happen, touch nothing
    beet clapback artist:Autechre
    beet clapback-search "dreamy ambient with piano"
    beet clapback-similar title:"Gantz Graf"     # what sounds like this — including music you don't own

What it stores on each item, as flexible attributes you can query and see:

    clapback_hash      the corpus key — SHA256 of the AcoustID fingerprint
    clapback_status    found | contributed | local | unfingerprinted | unembedded
    clapback_named     the MusicBrainz recording id this install has claimed for it

The embeddings themselves live in a sidecar store under your beets config
directory, keyed by item id, so `clapback-search` works offline over what has
been embedded or fetched.

What leaves the machine, and only when `contribute: yes`: a 512-float vector, a
one-way hash, and — when the track has one — its MusicBrainz recording id
(`mb_trackid`, which despite the name is the recording). Never audio, never
paths, never other tags. The hash cannot be reversed into the fingerprint, and
the fingerprint is not the audio. The recording id is what lets the corpus tell
*somebody else* what their nearest neighbour is called; it also tells the corpus
operator which recording you hold, which is why it goes out under the same
switch and not silently (`ADR-0012` point 6).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from beets import config as beets_config
from beets import plugins, ui
from beets.exceptions import UserError
from clapback_client import (
    Corpus,
    CorpusError,
    FingerprintUnavailable,
    ensure_client_id,
    fingerprint_file,
    hash_fingerprint,
)

#: The flexible attributes this plugin writes. Named so they group in `beet ls`.
HASH_FIELD = "clapback_hash"
STATUS_FIELD = "clapback_status"
NAMED_FIELD = "clapback_named"


def _embedder():
    """`clapback-embed`, imported lazily so `beet` starts fast and a missing model
    is reported by the command that needs it rather than at plugin load."""
    import clapback_embed

    return clapback_embed


class _Store:
    """Vectors by item id, in two files under the beets config directory.

    The same shape as the reference client's store — a `.npy` of rows and a
    small JSON index — because it is the smallest thing that answers a search:
    a text query is one dot product against every row, and a `.npy` loads into
    exactly the array that needs. Not the beets database, which stores flexible
    attributes as strings and would make `beet ls` print 512 floats.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.vectors_path = home / "vectors.npy"
        self.index_path = home / "index.json"
        self.ids: list[int] = []
        self.pipeline_version: str | None = None
        self._vectors = None

    def load(self) -> _Store:
        import numpy as np

        if self.index_path.exists():
            data = json.loads(self.index_path.read_text())
            self.ids = [int(i) for i in data.get("ids", [])]
            self.pipeline_version = data.get("pipeline_version")
        if self.vectors_path.exists():
            self._vectors = np.load(self.vectors_path)
        else:
            self._vectors = np.zeros((0, 512), dtype=np.float32)
        if len(self.ids) != len(self._vectors):
            # Two halves that disagree attribute every result to the wrong track.
            self.ids, self._vectors = [], np.zeros((0, 512), dtype=np.float32)
        return self

    def save(self) -> None:
        import numpy as np

        self.home.mkdir(parents=True, exist_ok=True)
        np.save(self.vectors_path, self._vectors)
        self.index_path.write_text(
            json.dumps({"pipeline_version": self.pipeline_version, "ids": self.ids}, indent=1)
        )

    def __contains__(self, item_id: int) -> bool:
        return item_id in self.ids

    def put(self, item_id: int, vector: list[float]) -> None:
        import numpy as np

        v = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if item_id in self.ids:
            self._vectors[self.ids.index(item_id)] = v
            return
        self.ids.append(item_id)
        self._vectors = np.vstack([self._vectors, v]) if len(self._vectors) else v

    def get(self, item_id: int) -> list[float] | None:
        if item_id not in self.ids:
            return None
        return [float(x) for x in self._vectors[self.ids.index(item_id)]]

    def nearest(self, query, limit: int) -> list[tuple[int, float]]:
        import numpy as np

        if not len(self._vectors):
            return []
        sims = self._vectors @ np.asarray(query, dtype=np.float32)
        top = np.argsort(-sims)[:limit]
        return [(self.ids[int(i)], float(sims[i])) for i in top]


class ClapbackPlugin(plugins.BeetsPlugin):
    def __init__(self) -> None:
        super().__init__()
        self.config.add(
            {
                "url": "https://clapback.seethroughlab.com",
                # `ADR-0011` point 6: opt-in, and a second setting from lookup.
                # A plugin in somebody else's software has less standing to
                # assume consent, not more.
                "contribute": False,
                # Run on import. Off by default because embedding is minutes per
                # album on a laptop and an import should not silently become that.
                "auto": False,
                "pace": 0.15,
            }
        )
        if self.config["auto"].get(bool):
            self.register_listener("album_imported", self._on_album_imported)
            self.register_listener("item_imported", self._on_item_imported)

    # --- where things live --------------------------------------------------

    def _home(self) -> Path:
        return Path(beets_config.config_dir()) / "clapback"

    def _client_id(self) -> str:
        """Minted on first contribution and never before — `ADR-0009` point 4
        covers the fact that an install exists. Stored as a file the user can
        find and delete; deleting it makes them a new contributor."""
        return ensure_client_id(self._home() / "client_id")

    # --- the commands -------------------------------------------------------

    def commands(self) -> list[ui.Subcommand]:
        run = ui.Subcommand(
            "clapback", help="look up or embed tracks; contribute if enabled"
        )
        run.parser.add_option(
            "-p", "--pretend", action="store_true", help="say what would happen, touch nothing"
        )
        run.parser.add_option(
            "-f", "--force", action="store_true", help="re-process tracks already done"
        )
        run.func = self._cmd_run

        search = ui.Subcommand(
            "clapback-search", help="find tracks matching a description"
        )
        search.parser.add_option("-n", "--limit", type="int", default=10)
        search.func = self._cmd_search

        similar = ui.Subcommand(
            "clapback-similar", help="what sounds like this track, across every library the commons holds"
        )
        similar.parser.add_option("-n", "--limit", type="int", default=10)
        similar.func = self._cmd_similar
        return [run, search, similar]

    def _cmd_run(self, lib, opts, args) -> None:
        items = list(lib.items(args))
        if not items:
            raise UserError("no tracks match")
        self._process(lib, items, pretend=opts.pretend, force=opts.force)

    def _cmd_search(self, lib, opts, args) -> None:
        description = " ".join(args).strip()
        if not description:
            raise UserError("give a description: beet clapback-search \"dreamy ambient\"")
        embed = _embedder()
        store = _Store(self._home()).load()
        if not store.ids:
            raise UserError("nothing embedded yet — run: beet clapback")
        query = embed.embed_text(description)
        for item_id, score in store.nearest(query, opts.limit):
            item = lib.get_item(item_id)
            if item is not None:
                ui.print_(f"{score:.4f}  {item}")

    def _cmd_similar(self, lib, opts, args) -> None:
        """The reason to install this that is not altruism.

        Takes the first matching track's vector and asks the commons what sounds
        like it. A neighbour you own is shown as your track; one you do not is
        shown as a MusicBrainz recording you can open — `ADR-0012` — or, when
        nobody has named it yet, as the bare hash it still is.
        """
        items = list(lib.items(args))
        if not items:
            raise UserError("no tracks match")
        seed = items[0]
        store = _Store(self._home()).load()
        vector = store.get(seed.id)
        if vector is None:
            raise UserError(f"not embedded yet — run: beet clapback {' '.join(args)}")
        corpus = Corpus(self.config["url"].as_str())
        try:
            neighbours = corpus.similar(
                vector, limit=opts.limit + 1, pipeline_version=store.pipeline_version
            )
        except CorpusError as exc:
            raise UserError(f"corpus unreachable: {exc}") from exc

        by_hash = {i.get(HASH_FIELD): i for i in lib.items(f"{HASH_FIELD}::.") if i.get(HASH_FIELD)}
        ui.print_(f"sounds like: {seed}")
        shown = 0
        for n in neighbours:
            if n["fingerprint_hash"] == seed.get(HASH_FIELD):
                continue
            mine = by_hash.get(n["fingerprint_hash"])
            if mine is not None:
                what = f"{mine}  (in your library)"
            elif n.get("recording_mbid"):
                what = (
                    f"https://musicbrainz.org/recording/{n['recording_mbid']}"
                    f"  ({n.get('recording_claims', 0)} claim{'s' if n.get('recording_claims', 0) != 1 else ''})"
                )
            else:
                what = f"{n['fingerprint_hash'][:16]}…  (not yet named by anyone)"
            ui.print_(f"{n['similarity']:.4f}  {what}")
            shown += 1
            if shown >= opts.limit:
                break

    # --- import hooks -------------------------------------------------------

    def _on_album_imported(self, lib, album) -> None:
        self._process(lib, list(album.items()), pretend=False, force=False)

    def _on_item_imported(self, lib, item) -> None:
        self._process(lib, [item], pretend=False, force=False)

    # --- the work -----------------------------------------------------------

    def _process(self, lib, items, *, pretend: bool, force: bool) -> None:
        """Look up, else embed; contribute if enabled. Over these items."""
        import time

        contribute = self.config["contribute"].get(bool)
        pace = self.config["pace"].as_number()
        corpus = Corpus(self.config["url"].as_str())

        embed = _embedder()
        pipeline_version = embed.PIPELINE_VERSION
        store = _Store(self._home()).load()
        if store.pipeline_version and store.pipeline_version != pipeline_version:
            # A store from a different pipeline cannot be searched alongside
            # this one, and its vectors cannot be contributed under this
            # identity (`ADR-0006` point 5: recomputed, not relabelled).
            raise UserError(
                f"the local store was built by {store.pipeline_version}\n"
                f"and the installed embedder is  {pipeline_version}.\n"
                "Delete the store to re-embed under the new pipeline:\n"
                f"  {self._home()}"
            )
        store.pipeline_version = pipeline_version

        client_id = None
        found = contributed = local = unfingerprinted = unembedded = skipped = 0
        try:
            for n, item in enumerate(items, start=1):
                done = item.get(STATUS_FIELD) in ("found", "contributed") and item.id in store
                # A done track still has work if contribution is on and it carries
                # a recording id the corpus has not been told about yet.
                unnamed = (
                    contribute
                    and item.get("mb_trackid")
                    and item.get(NAMED_FIELD) != item.get("mb_trackid")
                )
                if not force and done and not unnamed:
                    skipped += 1
                    continue

                # 1. The key. beets' `chroma` plugin stores the fingerprint as
                #    chromaprint returned it — measured 2026-09-13 on 99 tracks,
                #    99 byte-identical — but it goes through `hash_fingerprint`
                #    regardless, because `ADR-0010`'s defect came from a column.
                fp = item.get("acoustid_fingerprint")
                if not fp:
                    try:
                        fp = fingerprint_file(os.fsdecode(item.path))
                    except FingerprintUnavailable as exc:
                        self._log.info("{0}: no fingerprint ({1})", item, exc)
                        unfingerprinted += 1
                        if not pretend:
                            item[STATUS_FIELD] = "unfingerprinted"
                            item.store()
                        continue
                key = hash_fingerprint(fp)

                if pretend:
                    ui.print_(f"would process: {item}")
                    continue

                # 2. Look up before anything else. A hit means the commons has
                #    done the work; it also means we must not POST, because a
                #    repeat submission is recorded as agreement (`ADR-0008`).
                try:
                    row = corpus.lookup(key, pipeline_version)
                except CorpusError as exc:
                    self._log.warning("corpus unreachable: {0}", exc)
                    row = None
                    corpus_ok = False
                else:
                    corpus_ok = True

                if row is not None:
                    store.put(item.id, row["embedding"])
                    item[HASH_FIELD] = key
                    # A track this install contributed earlier keeps saying so;
                    # "found" is for rows somebody else put there.
                    if item.get(STATUS_FIELD) != "contributed":
                        item[STATUS_FIELD] = "found"
                    # The corpus had the vector; it may not have the name. A claim
                    # attaches the id to somebody else's row without touching
                    # their vector — `ADR-0012` point 4 — and only under the
                    # same switch that would have sent ours.
                    mbid = item.get("mb_trackid")
                    if contribute and mbid and item.get(NAMED_FIELD) != mbid:
                        if client_id is None:
                            client_id = self._client_id()
                        try:
                            corpus.claim(fingerprint_hash=key, recording_mbid=mbid, client_id=client_id)
                        except CorpusError as exc:
                            self._log.info("{0}: not named ({1})", item, exc)
                        else:
                            item[NAMED_FIELD] = mbid
                    item.store()
                    found += 1
                    continue

                # 3. Embed locally — unless a previous run already did, in which
                #    case the vector is in the store and the model does not run.
                #    This is the path a user takes when they turn `contribute`
                #    on after indexing: everything they already embedded goes
                #    out without being recomputed.
                vector = store.get(item.id)
                if vector is None:
                    try:
                        vector = [float(x) for x in embed.embed_file(os.fsdecode(item.path))]
                    except embed.ArtifactsMissing:
                        raise UserError(
                            "the ONNX encoders are missing — they are 614 MB and not bundled. "
                            "Export them once with clapback-embed's scripts/export_models.py, "
                            "or set CLAPBACK_MODEL_DIR to where they already are."
                        ) from None
                    except Exception as exc:  # noqa: BLE001 - one bad file must not end the run
                        self._log.info("{0}: could not embed ({1})", item, exc)
                        unembedded += 1
                        item[STATUS_FIELD] = "unembedded"
                        item.store()
                        continue
                    store.put(item.id, vector)
                item[HASH_FIELD] = key

                # 4. Contribute, if and only if asked.
                if contribute and corpus_ok:
                    if client_id is None:
                        client_id = self._client_id()
                    mbid = item.get("mb_trackid") or None
                    try:
                        corpus.contribute(
                            fingerprint_hash=key,
                            embedding=vector,
                            pipeline_version=pipeline_version,
                            client_id=client_id,
                            recording_mbid=mbid,
                        )
                    except CorpusError as exc:
                        self._log.warning("{0}: not contributed ({1})", item, exc)
                        item[STATUS_FIELD] = "local"
                        local += 1
                    else:
                        item[STATUS_FIELD] = "contributed"
                        if mbid:
                            item[NAMED_FIELD] = mbid
                        contributed += 1
                        time.sleep(pace)
                else:
                    item[STATUS_FIELD] = "local"
                    local += 1
                item.store()

                if n % 25 == 0:
                    store.save()
                    self._log.info("{0}/{1}", n, len(items))
        finally:
            if not pretend:
                store.save()

        if pretend:
            return
        summary = (
            f"found in corpus {found} · contributed {contributed} · local only {local} · "
            f"no fingerprint {unfingerprinted} · unembeddable {unembedded} · already done {skipped}"
        )
        if not contribute and local:
            summary += "\n(contribution is off; set clapback.contribute: yes to send local vectors)"
        ui.print_(summary)
