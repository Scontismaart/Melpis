import pytest
import asyncio
from types import SimpleNamespace
from pathlib import Path
import yaml

from src.core.documenti.embedding_contract import service_token
from src.core.documenti.embedding_instance import instance_lock
from src.core.documenti.embedding_service import worker_ready


def test_secret_file_is_bounded_and_ambiguous_configuration_is_rejected(
    monkeypatch, tmp_path
):
    path = tmp_path / "synthetic-secret"
    path.write_text("x" * 40 + "\n")
    monkeypatch.delenv("MELPIS_EMBEDDING_TOKEN", raising=False)
    monkeypatch.setenv("MELPIS_EMBEDDING_TOKEN_FILE", str(path))
    assert service_token() == "x" * 40
    monkeypatch.setenv("MELPIS_EMBEDDING_TOKEN", "y" * 40)
    with pytest.raises(ValueError, match="embedding_configuration_invalid"):
        service_token()
    monkeypatch.delenv("MELPIS_EMBEDDING_TOKEN")
    for value in ["x" * 257, "x" * 31, "x" * 40 + "\nextra", "x" * 256 + "\n\r\n"]:
        path.write_bytes(value.encode("ascii"))
        with pytest.raises(ValueError, match="embedding_configuration_invalid"):
            service_token()


def test_instance_fence_prevents_second_owner_and_releases_on_exit(tmp_path):
    path = tmp_path / "instance"
    with instance_lock(path):
        with pytest.raises(RuntimeError, match="embedding_instance_already_running"):
            with instance_lock(path):
                pytest.fail("second model owner admitted")
    with instance_lock(path):
        pass


@pytest.mark.asyncio
async def test_health_recovers_after_caller_timeout_while_startup_finishes():
    startup = asyncio.create_task(asyncio.sleep(0))
    worker = SimpleNamespace(process=SimpleNamespace(returncode=None), starting=startup)
    assert not worker_ready(worker)
    await startup
    # A completed handshake establishes readiness without a new request.
    assert worker_ready(worker)
    worker.process.returncode = 1
    assert not worker_ready(worker)


@pytest.mark.asyncio
async def test_completed_cold_start_after_timeout_never_spawns_second_model(
    monkeypatch,
):
    from src.core.documenti.isolated import LocalWorker

    worker = LocalWorker("embed_onnx")
    release = asyncio.Event()
    starts = []

    async def start():
        starts.append(1)
        worker.process = SimpleNamespace(returncode=None)
        await release.wait()

    monkeypatch.setattr(worker, "_start", start)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(worker.ensure_started(), 0.01)
    release.set()
    await worker.starting
    await asyncio.gather(*(worker.ensure_started() for _ in range(8)))
    assert starts == [1]
    assert worker.starting is None


def test_candidate_deployment_uses_one_private_encoder_and_no_caller_fallback():
    root = Path(__file__).resolve().parents[2]
    candidate = yaml.safe_load((root / "compose.inf01.candidate.yml").read_text(encoding="utf-8"))
    services = candidate["services"]
    assert candidate["networks"]["embedding_private"]["internal"] is True
    encoder = services["embedding"]
    assert encoder["networks"] == ["embedding_private"]
    assert "ports" not in encoder
    assert encoder["command"][encoder["command"].index("--workers") + 1] == "1"
    assert encoder["command"][encoder["command"].index("--limit-concurrency") + 1] == "32"
    assert encoder["mem_limit"] == "1536m"
    assert encoder["read_only"] is True
    for name in ["api", "worker-inbound", "worker-retry", "supervisor"]:
        assert services[name]["environment"]["MELPIS_EMBEDDING_BACKEND"] == "shared"
        assert services[name]["environment"]["MELPIS_EMBEDDING_ENGINE"] == "onnx"
        assert services[name]["secrets"] == ["embedding_token"]
        assert "embedding_private" in services[name]["networks"]
