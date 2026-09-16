"""Is the AcoustID fingerprint *string* the same whichever decoder produced the samples?

The corpus key is the SHA256 of that string (`ADR-0010`), so one flipped bit is a
different row. This compares, per file:

  fpcalc     the official static `fpcalc` (ffmpeg decode, swresample to 11025 Hz,
             then chromaprint) — what Familiar on Linux and Picard's bundled fpcalc do
  coreaudio  pyacoustid's library path on macOS: Core Audio decode at native rate,
             libchromaprint resamples internally — what beets on a Mac does
  sndfile    libsndfile decode to int16 at native rate, libchromaprint — a tool's
             own decode, best case

Measured 2026-09-16 on 56 FLACs (24 × 44.1k/16-bit, 32 × 24-bit): fpcalc agrees
with coreaudio on 24, with sndfile on 24; coreaudio with sndfile on 47. On 16-bit
files alone fpcalc agrees with coreaudio on 10 of 24. Differences are 1–17 bits of
~30,000. Results beside this file as `measure_fingerprint.results-2026-09-16.csv`;
the record is `ADR-0010`'s Implementation block and `ADR-0017` (rejected on it).

Offline tool — needs numpy, soundfile, pyacoustid with libchromaprint on the
library path (`DYLD_LIBRARY_PATH=/opt/homebrew/lib` on a Homebrew Mac), and a
`fpcalc` binary passed as the second argument. Not part of any package.

    python scripts/measure_fingerprint.py ~/Music/Familiar /path/to/fpcalc
"""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import acoustid
import chromaprint
import numpy as np
import soundfile as sf

MAXLEN = 120  # AcoustID's default: the first two minutes


def fpcalc(binary: str, p: Path) -> str:
    out = subprocess.run([binary, "-json", "-length", str(MAXLEN), str(p)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["fingerprint"]


def coreaudio(p: Path) -> str:
    _, fp = acoustid.fingerprint_file(str(p), maxlength=MAXLEN)
    return fp.decode() if isinstance(fp, bytes) else fp


def sndfile(p: Path) -> str:
    x16, sr = sf.read(str(p), dtype="int16", always_2d=True)
    f = chromaprint.Fingerprinter()
    f.start(sr, x16.shape[1])
    f.feed(np.ascontiguousarray(x16)[: sr * MAXLEN].tobytes())
    return f.finish().decode()


def bits(a: str, b: str):
    da = chromaprint.decode_fingerprint(a.encode())[0]
    db = chromaprint.decode_fingerprint(b.encode())[0]
    if len(da) != len(db):
        return f"len {len(da)}/{len(db)}"
    return sum((x ^ y).bit_count() for x, y in zip(da, db))


def main() -> None:
    root, binary = Path(sys.argv[1]).expanduser(), sys.argv[2]
    tally: Counter = Counter()
    rows = []
    for f in sorted(root.rglob("*.flac")):
        info = sf.info(str(f))
        kind = f"{info.samplerate}/{info.subtype}"
        a, b, d = fpcalc(binary, f), coreaudio(f), sndfile(f)
        tally[(kind, "fpcalc==coreaudio", a == b)] += 1
        tally[(kind, "fpcalc==sndfile", a == d)] += 1
        tally[(kind, "coreaudio==sndfile", b == d)] += 1
        rows.append({
            "file": f.name, "kind": kind,
            "fpcalc_eq_coreaudio": a == b, "fpcalc_eq_sndfile": a == d, "coreaudio_eq_sndfile": b == d,
            "bits_fpcalc_coreaudio": bits(a, b), "bits_fpcalc_sndfile": bits(a, d),
        })
        print(f"{f.name[:44]:44} {kind:13} fpcalc==coreaudio {a == b!s:5} bits {bits(a, b)}", flush=True)
    for k in sorted(tally):
        print(k, tally[k])
    n = len(rows)
    print(f"\n{n} files: fpcalc==coreaudio {sum(r['fpcalc_eq_coreaudio'] for r in rows)}, "
          f"fpcalc==sndfile {sum(r['fpcalc_eq_sndfile'] for r in rows)}, "
          f"coreaudio==sndfile {sum(r['coreaudio_eq_sndfile'] for r in rows)}")
    Path("fpmeasure.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
