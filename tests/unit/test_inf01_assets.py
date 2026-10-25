"""Offline asset verification tests using synthetic files and pinned digests."""

import json

import pytest

from src.core.documenti import embedding_assets, embedding_contract


def _synthetic_assets(root):
    files = {
        "model.safetensors": b"synthetic torch weights",
        "onnx/model.onnx": b"synthetic onnx weights",
        "tokenizer.json": b"synthetic tokenizer",
        "sentence_bert_config.json": json.dumps({"max_seq_length": 128}).encode(),
        "1_Pooling/config.json": json.dumps(
            {
                "word_embedding_dimension": 384,
                "pooling_mode_mean_tokens": True,
                "pooling_mode_cls_token": False,
                "pooling_mode_max_tokens": False,
                "pooling_mode_mean_sqrt_len_tokens": False,
            }
        ).encode(),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return files


def _patch_synthetic_digests(monkeypatch, root, files, *, mismatch=None):
    expected = {
        root / "model.safetensors": embedding_contract.ARTIFACTS["torch"],
        root / "onnx/model.onnx": embedding_contract.ARTIFACTS["onnx"],
        root / "tokenizer.json": embedding_contract.VOCABULARY_SHA256,
    }
    if mismatch is not None:
        expected[root / mismatch] = "0" * 64

    seen = []

    def fake_digest(path):
        assert path.is_file(), "asset verification must use pre-provisioned local files"
        seen.append(path)
        return expected[path]

    monkeypatch.setattr(embedding_assets, "digest", fake_digest)
    return seen


@pytest.mark.parametrize("engine,weight", [("torch", "model.safetensors"), ("onnx", "onnx/model.onnx")])
def test_verified_assets_accept_synthetic_local_files_without_download(
    tmp_path, monkeypatch, engine, weight
):
    files = _synthetic_assets(tmp_path)
    monkeypatch.setenv("MELPIS_EMBEDDING_MODEL_DIR", str(tmp_path))
    seen = _patch_synthetic_digests(monkeypatch, tmp_path, files)

    root, selected = embedding_assets.verified_assets(engine)

    assert root == tmp_path
    assert selected == tmp_path / weight
    assert set(seen) == {tmp_path / weight, tmp_path / "tokenizer.json"}


@pytest.mark.parametrize(
    "engine,mismatch",
    [("torch", "model.safetensors"), ("onnx", "tokenizer.json")],
)
def test_verified_assets_fail_closed_on_digest_mismatch(
    tmp_path, monkeypatch, engine, mismatch
):
    files = _synthetic_assets(tmp_path)
    monkeypatch.setenv("MELPIS_EMBEDDING_MODEL_DIR", str(tmp_path))
    _patch_synthetic_digests(monkeypatch, tmp_path, files, mismatch=mismatch)

    with pytest.raises(RuntimeError, match="^embedding_assets_unverified$"):
        embedding_assets.verified_assets(engine)


@pytest.mark.parametrize(
    "relative,value",
    [
        ("sentence_bert_config.json", {"max_seq_length": 256}),
        (
            "1_Pooling/config.json",
            {"word_embedding_dimension": 384, "pooling_mode_mean_tokens": False},
        ),
    ],
)
def test_verified_assets_fail_closed_on_incompatible_embedding_config(
    tmp_path, monkeypatch, relative, value
):
    files = _synthetic_assets(tmp_path)
    (tmp_path / relative).write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("MELPIS_EMBEDDING_MODEL_DIR", str(tmp_path))
    _patch_synthetic_digests(monkeypatch, tmp_path, files)

    with pytest.raises(RuntimeError, match="^embedding_assets_unverified$"):
        embedding_assets.verified_assets("torch")


def test_verified_assets_fail_closed_when_artifact_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MELPIS_EMBEDDING_MODEL_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="^embedding_assets_unverified$"):
        embedding_assets.verified_assets("onnx")
