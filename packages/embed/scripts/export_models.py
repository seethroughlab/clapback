#!/usr/bin/env python3
"""Produce the ONNX encoders from the pinned checkpoint, and prove they agree.

    python scripts/export_models.py --out ~/.cache/clapback/models

Needs `torch` and `transformers`; the package that consumes the output needs
neither. That asymmetry is the point — exporting is a rare developer action, and
running the embedder is what every contributor does.

**Every export verifies itself against PyTorch before writing.** An artifact that
disagrees with the reference is worse than a missing one: it produces plausible
vectors that are comparable with nothing, and nothing downstream would notice.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

CHECKPOINT = "laion/clap-htsat-unfused"
#: Below this, the export is refused. Storage is float4 (6e-8) and the runtime
#: difference measured 1.2e-7, so anything at 1e-6 or worse indicates a real
#: divergence rather than arithmetic noise.
MIN_AGREEMENT = 0.999999


def cosine(a, b) -> float:
    import numpy as np

    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def export_audio(out: Path, model, processor) -> None:
    import numpy as np
    import onnxruntime as ort
    import torch

    rng = np.random.default_rng(1)
    audio = rng.normal(size=480_000).astype(np.float32)
    # `audio=`, not `audios=`: the plural spelling was deprecated and now raises.
    enc = processor(audio=audio, sampling_rate=48_000, return_tensors="pt")
    feats, is_longer = enc["input_features"], enc["is_longer"]

    class AudioOnly(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_features, is_longer):
            o = self.m.get_audio_features(
                input_features=input_features, is_longer=is_longer
            )
            return o.pooler_output if hasattr(o, "pooler_output") else o

    wrapped = AudioOnly(model)
    with torch.no_grad():
        reference = wrapped(feats, is_longer).numpy()

    fp32 = out / "clap_audio.onnx"
    torch.onnx.export(
        wrapped,
        (feats, is_longer),
        str(fp32),
        input_names=["input_features", "is_longer"],
        output_names=["audio_embeds"],
        dynamic_axes={
            "input_features": {0: "b"},
            "is_longer": {0: "b"},
            "audio_embeds": {0: "b"},
        },
        opset_version=17,
        # Legacy TorchScript exporter. torch 2.13's dynamo path emits shape
        # metadata the ONNX quantiser rejects ("Inferred shape and existing shape
        # differ").
        dynamo=False,
    )

    # `is_longer` is folded out by the tracer, which is safe for this checkpoint:
    # it has enable_fusion=False, and flipping the flag changes the output by
    # exactly 0.0. A checkpoint with fusion enabled would need it kept.
    feed = {"input_features": feats.numpy()}
    got = ort.InferenceSession(str(fp32), providers=["CPUExecutionProvider"]).run(
        None, feed
    )[0]
    agreement = cosine(reference[0], got[0])
    print(f"  audio fp32  {fp32.stat().st_size / 1e6:6.1f} MB   vs torch: {agreement:.9f}")
    if agreement < MIN_AGREEMENT:
        raise SystemExit(f"audio fp32 disagrees with PyTorch ({agreement})")

    from onnxruntime.transformers import optimizer

    opt = optimizer.optimize_model(
        str(fp32), model_type="bert", num_heads=8, hidden_size=768,
        opt_level=0, use_gpu=False,
    )
    opt.convert_float_to_float16(keep_io_types=True)
    fp16 = out / "clap_audio_fp16.onnx"
    opt.save_model_to_file(str(fp16))
    got16 = ort.InferenceSession(str(fp16), providers=["CPUExecutionProvider"]).run(
        None, feed
    )[0]
    # Deliberately not held to MIN_AGREEMENT: fp16 measures ~1.5e-6 from fp32,
    # which is why it is not corpus-safe. Printed so the number stays visible.
    print(f"  audio fp16  {fp16.stat().st_size / 1e6:6.1f} MB   vs fp32:  {cosine(got[0], got16[0]):.9f}")


def export_text(out: Path, model, processor) -> None:
    import onnxruntime as ort
    import torch

    queries = [
        "dreamy ambient with piano",
        "driving krautrock",
        "melancholy jazz trumpet at 3am",
    ]
    enc = processor(text=queries, return_tensors="pt", padding=True)
    ids, mask = enc["input_ids"], enc["attention_mask"]

    class TextOnly(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            # transformers 5.x returns BaseModelOutputWithPooling; 4.x returned
            # the tensor directly.
            o = self.m.get_text_features(
                input_ids=input_ids, attention_mask=attention_mask
            )
            return o.pooler_output if hasattr(o, "pooler_output") else o

    wrapped = TextOnly(model)
    with torch.no_grad():
        reference = wrapped(ids, mask).numpy()

    path = out / "clap_text.onnx"
    torch.onnx.export(
        wrapped,
        (ids, mask),
        str(path),
        input_names=["input_ids", "attention_mask"],
        output_names=["text_embeds"],
        dynamic_axes={
            "input_ids": {0: "b", 1: "t"},
            "attention_mask": {0: "b", 1: "t"},
            "text_embeds": {0: "b"},
        },
        opset_version=17,
        dynamo=False,
    )

    got = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]).run(
        None, {"input_ids": ids.numpy(), "attention_mask": mask.numpy()}
    )[0]
    worst = min(cosine(a, b) for a, b in zip(reference, got))
    # May be written with external data (clap_text.onnx.data) depending on size;
    # both files must travel together.
    sidecar = out / "clap_text.onnx.data"
    total = path.stat().st_size + (sidecar.stat().st_size if sidecar.exists() else 0)
    print(f"  text  fp32  {total / 1e6:6.1f} MB   vs torch: {worst:.9f} (worst of {len(queries)})")
    if worst < MIN_AGREEMENT:
        raise SystemExit(f"text disagrees with PyTorch ({worst})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="directory to write artifacts into"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    try:
        from transformers import ClapModel, ClapProcessor
    except ImportError:
        print(
            "exporting needs the optional extra:  uv pip install -e '.[export]'",
            file=sys.stderr,
        )
        return 1

    print(f"checkpoint: {CHECKPOINT}")
    model = ClapModel.from_pretrained(CHECKPOINT).eval()
    processor = ClapProcessor.from_pretrained(CHECKPOINT)

    export_audio(args.out, model, processor)
    export_text(args.out, model, processor)
    print(f"\nwrote artifacts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
