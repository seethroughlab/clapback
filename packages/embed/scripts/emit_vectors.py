"""Embed every track and emit vectors at full precision, with input checksums.

Half of the cross-machine conformance check. Run it on two machines with the
*same* artifact bytes and the *same* audio bytes, then diff the outputs with
`compare_vectors.py`. The checksums are in the output so a bad transfer cannot
masquerade as a disagreement — which is the whole thing this is trying to
measure.

    CLAPBACK_MODEL_DIR=... AUDIO_DIR=... python scripts/emit_vectors.py > out.json
"""
import glob
import hashlib
import json
import os
import platform

import librosa
import numpy
import onnxruntime

from clapback_embed import PIPELINE_VERSION, embed_file

out = {"arch": platform.machine(), "platform": platform.platform(),
       "pipeline": PIPELINE_VERSION, "tracks": {}}
out["versions"] = {"onnxruntime": onnxruntime.__version__,
                   "numpy": numpy.__version__, "librosa": librosa.__version__,
                   "python": platform.python_version()}
for path in sorted(glob.glob(os.environ["AUDIO_DIR"] + "/*.mp3")):
    name = os.path.basename(path)
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()[:16]
    v = embed_file(path)
    out["tracks"][name] = {"sha256": digest, "vector": v}
print(json.dumps(out))
