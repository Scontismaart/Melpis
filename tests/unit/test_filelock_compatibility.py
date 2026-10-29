"""Cross-process compatibility checks for filelock and its consumers."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def lock_context(kind, path, timeout):
    if kind == "filelock":
        from filelock import FileLock
        return FileLock(path, timeout=timeout)
    if kind == "huggingface":
        from huggingface_hub.utils._fixes import WeakFileLock
        return WeakFileLock(path, timeout=timeout)
    if kind == "torch":
        from torch.utils._filelock import FileLock
        return FileLock(path, timeout=timeout)
    raise ValueError(kind)


def child(kind, path, timeout):
    from filelock import Timeout
    # Imports complete before signaling readiness, so slow torch startup cannot
    # cause a false success or fail the lock timeout before contention begins.
    context = lock_context(kind, path, timeout)
    print("READY", flush=True)
    try:
        with context:
            result = "acquired"
    except Timeout:
        result = "timeout"
    print(json.dumps({"result": result}), flush=True)


def run_child(kind, path, timeout):
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child", kind, str(path), str(timeout)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # communicate bounds startup and contention, including slow torch import.
        stdout, stderr = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        pytest.fail("Lock subprocess did not finish within 90 seconds")
    assert process.returncode == 0, f"Lock subprocess failed: {stderr}"
    lines = stdout.splitlines()
    assert lines[0] == "READY", f"Missing readiness signal: {stdout}"
    return json.loads(lines[-1])["result"]


@pytest.mark.parametrize("kind", ["filelock", "huggingface", "torch"])
def test_file_lock_excludes_another_process_and_releases(kind, tmp_path):
    path = tmp_path / "private.lock"
    with lock_context(kind, path, 5):
        assert run_child(kind, path, 0.3) == "timeout"
    assert run_child(kind, path, 5) == "acquired"


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--child":
    child(sys.argv[2], sys.argv[3], float(sys.argv[4]))
