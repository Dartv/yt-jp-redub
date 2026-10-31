"""IndexTTS-2.5 worker. Runs inside IndexTTS's own environment (third_party/index-tts/.venv),
so it must not import yt_jp_redub.

Protocol: one JSON request per line on stdin, one JSON reply per line on the original stdout.
IndexTTS's own prints are redirected to stderr.

Request:  {"id": 1, "text": "...", "ref": "ref.wav", "emo_ref": "ref.wav" | null, "out": "out.wav",
           "lang": "JA", "duration_factor": 1.0, "emo_alpha": 1.0, "max_mel_tokens": 200 | null,
           "num_beams": 1 | null}
Reply:    {"id": 1, "ok": true, "sec": 0.83} or {"id": 1, "ok": false, "error": "..."}
First line on startup: {"ready": true, "device": "cuda:0"}
"""

import argparse
import json
import os
import sys
import time
import traceback

proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr


def reply(obj):
    proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
    proto.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="checkpoints")
    ap.add_argument("--bf16", action="store_true")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    import torch
    from indextts.infer_v2_5 import IndexTTS2
    from transformers import Wav2Vec2BertModel

    # w2v-bert only gives the reference voice's features, from its 17th layer: the 7 layers after it
    # are dead weight, and in fp16 the features match fp32's to 0.4%. 1.4 GB less VRAM in all
    half = torch.cuda.is_available() and (args.device or "cuda").startswith("cuda")
    load = Wav2Vec2BertModel.from_pretrained
    Wav2Vec2BertModel.from_pretrained = lambda *a, **k: load(
        *a, num_hidden_layers=17, torch_dtype=torch.float16 if half else None, **k
    )
    tts = IndexTTS2(
        cfg_path=os.path.join(args.model_dir, "config.yaml"),
        model_dir=args.model_dir,
        use_bf16=args.bf16,
        device=args.device,
    )
    if half:
        get_emb = tts.get_emb
        tts.get_emb = lambda features, mask: get_emb(features.half(), mask)
    reply({"ready": True, "device": str(tts.device)})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req, t0 = {}, time.time()
        try:
            req = json.loads(raw)
            if req.get("cmd") == "quit":
                break
            # generation settings: a cap on the speech tokens keeps very short texts from running on into
            # babble; one beam is ~20% faster than IndexTTS's default three at the same quality
            gen = {k: int(req[k]) for k in ("max_mel_tokens", "num_beams") if req.get(k)}
            tts.infer(
                spk_audio_prompt=req["ref"],
                text=req["text"],
                output_path=req["out"],
                lang=req.get("lang", "JA"),
                emo_audio_prompt=req.get("emo_ref"),
                emo_alpha=float(req.get("emo_alpha", 1.0)),
                duration_factor=float(req.get("duration_factor", 1.0)),
                verbose=False,
                **gen,
            )
            if not os.path.exists(req["out"]):
                raise RuntimeError("no audio produced")
            reply({"id": req.get("id"), "ok": True, "sec": round(time.time() - t0, 3)})
        except Exception as e:  # keep serving after a bad line
            traceback.print_exc()
            reply({"id": req.get("id"), "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
