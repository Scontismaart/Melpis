"""Verify pre-provisioned immutable model assets. Never download at runtime."""

import hashlib
import json
import os
from pathlib import Path

from src.core.documenti.embedding_contract import ARTIFACTS, REVISION, VOCABULARY_SHA256


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def verified_assets(engine):
    default = (
        Path.home()
        / ".cache/huggingface/hub"
        / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots"
        / REVISION
    )
    root = Path(os.getenv("MELPIS_EMBEDDING_MODEL_DIR", str(default)))
    weight = root / ("onnx/model.onnx" if engine == "onnx" else "model.safetensors")
    try:
        if (
            digest(weight) != ARTIFACTS[engine]
            or digest(root / "tokenizer.json") != VOCABULARY_SHA256
        ):
            raise ValueError()
        config = json.loads(
            (root / "sentence_bert_config.json").read_text(encoding="utf-8")
        )
        pooling = json.loads(
            (root / "1_Pooling/config.json").read_text(encoding="utf-8")
        )
        if (
            config.get("max_seq_length") != 128
            or pooling.get("word_embedding_dimension") != 384
            or pooling.get("pooling_mode_mean_tokens") is not True
            or any(
                value is True
                for key, value in pooling.items()
                if key.startswith("pooling_mode_") and key != "pooling_mode_mean_tokens"
            )
        ):
            raise ValueError()
    except Exception:
        raise RuntimeError("embedding_assets_unverified") from None
    return root, weight


class OfflineOnnxEncoder:
    """Same pinned BERT tokenizer, prefixes, masked mean pooling and L2 normalization."""

    def __init__(self):
        root, weight = verified_assets("onnx")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(weight), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.inputs = {item.name for item in self.session.get_inputs()}

    def encode(self, texts, tipo):
        import numpy as np

        prefix = "query: " if tipo == "query" else "passage: "
        tokens = self.tokenizer.encode_batch([prefix + text for text in texts])
        values = {
            "input_ids": np.asarray([t.ids for t in tokens], dtype=np.int64),
            "attention_mask": np.asarray(
                [t.attention_mask for t in tokens], dtype=np.int64
            ),
            "token_type_ids": np.asarray([t.type_ids for t in tokens], dtype=np.int64),
        }
        hidden = self.session.run(None, {name: values[name] for name in self.inputs})[0]
        mask = values["attention_mask"][..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        return (
            pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        ).tolist()
