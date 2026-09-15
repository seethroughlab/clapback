# Clapback plugin for MusicBrainz Picard
#
# Copyright (C) 2026 Jeff Crouse
# MIT licence — see LICENSE beside this file.

PLUGIN_NAME = "Clapback"
PLUGIN_AUTHOR = "Jeff Crouse"
PLUGIN_DESCRIPTION = """
Look up, name, and — if you say so — contribute <b>CLAP audio embeddings</b> for
your files in the <a href="https://clapback.seethroughlab.com">clapback</a> commons,
and ask what sounds like a track across every library it holds.
<br/><br/>
Picard already fingerprints every file it scans and knows its MusicBrainz recording
id, which is exactly the pair the commons needs. Without <code>clapback-embed</code>
installed the plugin runs in <b>lookup-only</b> mode: it can tell you whether the
commons holds a recording, name it, and ask what it sounds like using the commons's
own vector — and says so rather than computing anything.
<br/><br/>
Nothing leaves the machine until <i>Contribute</i> is turned on in the options. Then,
per file: a one-way fingerprint hash, a 512-float vector when one is computed here,
and the MusicBrainz recording id. Never audio, never paths, never other tags.
Everything sent is dedicated to the public domain under CC0 1.0, like every other row in the
corpus, and may be republished in its public exports.
"""
PLUGIN_VERSION = "0.1.2"
PLUGIN_API_VERSIONS = ["2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "MIT"
PLUGIN_LICENSE_URL = "https://opensource.org/license/mit"
PLUGIN_USER_GUIDE_URL = "https://github.com/seethroughlab/clapback/tree/main/packages/picard-clapback"

from functools import partial

from picard import log
from picard.config import BoolOption, TextOption, get_config
from picard.file import File, register_file_post_save_processor
from picard.ui.itemviews import BaseAction, register_file_action, register_track_action
from picard.ui.options import OptionsPage, register_options_page
from picard.util import thread
from PyQt5 import QtWidgets

from ._core import Outcome, find_embedder, neighbour_html, neighbours, process
from .clapback_client import DEFAULT_BASE_URL, Corpus, mint_client_id

# Hidden variables, so they show in the metadata panel and are usable in scripts
# without ever being written to a tag.
STATUS_VAR = "~clapback_status"
NAMED_VAR = "~clapback_named"

OPT_URL = "clapback_url"
OPT_CONTRIBUTE = "clapback_contribute"
OPT_ON_SAVE = "clapback_on_save"
OPT_CLIENT_ID = "clapback_client_id"


# --- settings -----------------------------------------------------------------


def _corpus() -> Corpus:
    return Corpus(get_config().setting[OPT_URL] or DEFAULT_BASE_URL)


def _client_id() -> str:
    """This install's id, minted on the first contribution and never before."""
    config = get_config()
    value = config.setting[OPT_CLIENT_ID]
    if not value:
        value = mint_client_id()
        config.setting[OPT_CLIENT_ID] = value
    return value


# --- files, fingerprints, and the two-stage run ---------------------------------


def _files_of(objs):
    """Every File in a selection of files, tracks, albums or clusters."""
    seen = set()
    for obj in objs:
        candidates = [obj] if isinstance(obj, File) else list(getattr(obj, "iterfiles", lambda: [])())
        for f in candidates:
            if id(f) not in seen:
                seen.add(id(f))
                yield f


def _fingerprint_of(file) -> str | None:
    """Picard keeps the fingerprint on the File after a scan; the tag is the
    fallback for files whose fingerprint was saved by an earlier session."""
    return getattr(file, "acoustid_fingerprint", None) or file.metadata.get("acoustid_fingerprint") or None


def _job(file) -> dict:
    """What the worker needs, captured on the main thread. Files are Qt objects
    and are not touched from the worker."""
    return {
        "file": file,
        "path": file.filename,
        "fingerprint": _fingerprint_of(file),
        "recording_mbid": file.metadata.get("musicbrainz_recordingid") or None,
        "already_named": file.metadata.get(NAMED_VAR) or None,
    }


class _Fingerprinted:
    """Fingerprint what needs it through Picard's own fpcalc — asynchronous, on
    the main thread — then call `then(files)`. One object per invocation."""

    def __init__(self, tagger, files, then):
        self.tagger = tagger
        self.files = list(files)
        self.then = then
        self.pending = 0

    def start(self):
        need = [f for f in self.files if not _fingerprint_of(f)]
        client = getattr(self.tagger, "_acoustid", None)
        use = getattr(self.tagger, "use_acoustid", True)
        if callable(use):
            use = use()
        if need and client is not None and use:
            self.pending = len(need)
            _status(self.tagger, f"Clapback: fingerprinting {len(need)} file(s)…")
            for f in need:
                client.fingerprint(f, self._one_done)
        else:
            self.then(self.files)

    def _one_done(self, result=None):
        self.pending -= 1
        if self.pending <= 0:
            self.then(self.files)


def _status(tagger, text: str):
    window = getattr(tagger, "window", None)
    if window is not None:
        window.set_statusbar_message(text)
    log.info("%s", text)


def _run_lookup(tagger, files, *, contribute: bool):
    """The contract over these files: fingerprint, then worker, then write back."""

    def then(ready):
        jobs = [_job(f) for f in ready]
        # Minted here, on the main thread, before any worker touches settings —
        # and only if something might actually be sent.
        cid = _client_id() if contribute else ""
        _status(tagger, f"Clapback: {'contributing' if contribute else 'looking up'} {len(jobs)} file(s)…")
        thread.run_task(partial(_process_jobs, jobs, contribute, cid), partial(_apply, tagger))

    _Fingerprinted(tagger, files, then).start()


def _apply(tagger, result=None, error=None):
    if error is not None:
        log.error("clapback: %s", error)
        _status(tagger, f"Clapback: failed ({error})")
        return
    counts: dict[str, int] = {}
    for job, outcome in result:
        file = job["file"]
        file.metadata[STATUS_VAR] = outcome.line
        if outcome.named:
            file.metadata[NAMED_VAR] = outcome.named
        file.update()
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    _status(tagger, "Clapback: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def _process_jobs(jobs: list[dict], contribute: bool, client_id: str) -> list[tuple[dict, Outcome]]:
    """Worker thread: the contract, per file, in order. No Qt here."""
    corpus = _corpus()
    embedder = find_embedder() if contribute else None
    out = []
    for job in jobs:
        outcome = process(
            corpus,
            fingerprint=job["fingerprint"],
            path=job["path"],
            recording_mbid=job["recording_mbid"],
            contribute=contribute,
            client_id=lambda: client_id,
            embedder=embedder,
            already_named=job["already_named"],
        )
        out.append((job, outcome))
    return out


# --- actions --------------------------------------------------------------------


class ClapbackLookup(BaseAction):
    NAME = "Clapback: look up in the commons…"

    def callback(self, objs):
        contribute = bool(get_config().setting[OPT_CONTRIBUTE])
        _run_lookup(self.tagger, _files_of(objs), contribute=contribute)


class ClapbackSimilar(BaseAction):
    NAME = "Clapback: what sounds like this…"

    def callback(self, objs):
        files = list(_files_of(objs))
        if not files:
            return
        file = files[0]

        def then(ready):
            _status(self.tagger, "Clapback: asking what sounds like this…")
            thread.run_task(partial(_neighbours_job, _job(file)), partial(self._show, file))

        _Fingerprinted(self.tagger, [file], then).start()

    def _show(self, file, result=None, error=None):
        title = file.metadata.get("title") or file.base_filename
        if error is not None:
            QtWidgets.QMessageBox.information(
                self.tagger.window, "Clapback", f"Could not ask about “{title}”:\n\n{error}"
            )
            return
        lines = "<br/>".join(neighbour_html(n) for n in result) or "The commons holds nothing near it yet."
        dialog = QtWidgets.QDialog(self.tagger.window)
        dialog.setWindowTitle(f"Sounds like: {title}")
        layout = QtWidgets.QVBoxLayout(dialog)
        browser = QtWidgets.QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setHtml(
            "<p>Nearest recordings in the commons, across every library it holds.<br/>"
            "A named neighbour is a MusicBrainz recording; a hash is one nobody has named yet.</p>"
            f"<p style='font-family: monospace'>{lines}</p>"
        )
        layout.addWidget(browser)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(640, 360)
        dialog.exec_()


def _neighbours_job(job: dict):
    corpus = _corpus()
    return neighbours(corpus, fingerprint=job["fingerprint"], path=job["path"], embedder=find_embedder())


# --- on save --------------------------------------------------------------------


def _after_save(file):
    """`ADR-0011` point 5: "computes the embedding on save or on demand". On save
    is opt-in on top of contribute, because saving is the frequent action and
    the model is minutes per album on a laptop."""
    config = get_config()
    if not config.setting[OPT_ON_SAVE]:
        return
    _run_lookup(file.tagger, [file], contribute=bool(config.setting[OPT_CONTRIBUTE]))


# --- options page ---------------------------------------------------------------


class ClapbackOptionsPage(OptionsPage):
    NAME = "clapback"
    TITLE = "Clapback"
    PARENT = "plugins"

    options = [
        TextOption("setting", OPT_URL, DEFAULT_BASE_URL),
        BoolOption("setting", OPT_CONTRIBUTE, False),
        BoolOption("setting", OPT_ON_SAVE, False),
        TextOption("setting", OPT_CLIENT_ID, ""),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.url = QtWidgets.QLineEdit(self)
        form.addRow("Commons URL", self.url)
        layout.addLayout(form)

        self.contribute = QtWidgets.QCheckBox("Contribute — send what leaves the machine, below", self)
        layout.addWidget(self.contribute)
        self.on_save = QtWidgets.QCheckBox("Also run after every save (needs Contribute for anything to be sent)", self)
        layout.addWidget(self.on_save)

        embedder = find_embedder()
        mode = (
            f"clapback-embed is installed — pipeline <code>{embedder.PIPELINE_VERSION}</code>. "
            "Files the commons does not hold are embedded here and contributed."
            if embedder
            else "clapback-embed is <b>not</b> installed — <b>lookup-only mode</b>. The plugin can "
            "tell you whether the commons holds a recording, name it, and ask what it sounds "
            "like; it computes nothing. <code>pip install clapback-embed</code> into Picard's "
            "Python, plus the ONNX encoders (614 MB), to contribute vectors."
        )
        note = QtWidgets.QLabel(
            "<p><b>What leaves the machine, per file, only when Contribute is on:</b> a one-way "
            "SHA256 of the AcoustID fingerprint; a 512-float vector when one is computed here; "
            "and the MusicBrainz recording id, which tells the commons which recording you hold. "
            "Never audio, never paths, never other tags. Everything sent is dedicated to the "
            "public domain under CC0 1.0, like every other row in the corpus, and may be "
            "republished in its public exports.</p>"
            f"<p>{mode}</p>"
            "<p>A random client id is minted on your first contribution and stored in Picard's "
            "settings. It lets the commons tell two contributions apart from one retrying; "
            "delete it and you are a new contributor.</p>",
            self,
        )
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def load(self):
        config = get_config()
        self.url.setText(config.setting[OPT_URL] or DEFAULT_BASE_URL)
        self.contribute.setChecked(bool(config.setting[OPT_CONTRIBUTE]))
        self.on_save.setChecked(bool(config.setting[OPT_ON_SAVE]))

    def save(self):
        config = get_config()
        config.setting[OPT_URL] = self.url.text().strip().rstrip("/") or DEFAULT_BASE_URL
        config.setting[OPT_CONTRIBUTE] = self.contribute.isChecked()
        config.setting[OPT_ON_SAVE] = self.on_save.isChecked()


register_track_action(ClapbackLookup())
register_file_action(ClapbackLookup())
register_track_action(ClapbackSimilar())
register_file_action(ClapbackSimilar())
register_file_post_save_processor(_after_save)
register_options_page(ClapbackOptionsPage)
