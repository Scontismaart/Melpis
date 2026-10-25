import importlib.util
from pathlib import Path
import sys


def test_mixed_cli_passes_integer_tiers_to_executor_without_network(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_inf01_mixed.py"
    spec = importlib.util.spec_from_file_location("inf01_mixed_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = []

    async def run(args):
        observed.extend(args.sizes)
        return {"status": "complete", "verification_checks": {"synthetic": True}}

    monkeypatch.setattr(module, "_run", run)
    monkeypatch.setattr(sys, "argv", [str(path), "--sizes", "5,25,100", "--receipt", str(tmp_path / "result.json")])
    assert module.main() == 0
    assert observed == [5, 25, 100]
    assert all(type(size) is int for size in observed)
