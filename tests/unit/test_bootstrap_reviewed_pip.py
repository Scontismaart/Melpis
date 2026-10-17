"""The bootstrap may upgrade pip only after verifying the official wheel."""
import hashlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_bootstrap():
    path = ROOT / 'scripts/bootstrap_reviewed_pip.py'
    assert path.exists(), 'Explicit isolated pip bootstrap is required'
    spec = importlib.util.spec_from_file_location('bootstrap_reviewed_pip', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_global_bootstrap_is_rejected_before_download_or_pip(monkeypatch):
    module = load_bootstrap()
    monkeypatch.setattr(module.sys, 'prefix', module.sys.base_prefix)
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: pytest.fail('unexpected download'))
    with pytest.raises(ValueError, match='virtual environment'):
        module.bootstrap()


def test_bad_wheel_hash_never_calls_pip(monkeypatch):
    module = load_bootstrap()
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'untrusted wheel'))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('pip called before hash validation'))
    with pytest.raises(ValueError, match='SHA'):
        module.bootstrap(isolated_build=True)


def wheel_fixture():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as wheel:
        wheel.writestr('pip-26.2.1.dist-info/METADATA', b'Name: pip\nVersion: 26.2.1\n')
    return output.getvalue()


def test_verified_wheel_is_installed_offline_and_checked_in_fresh_python(monkeypatch):
    module = load_bootstrap()
    payload = wheel_fixture()
    monkeypatch.setattr(module, 'PIP_SHA256', hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(payload))
    calls = []

    def subprocess_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            assert Path(command[-1]).read_bytes() == payload
            assert '--upgrade' in command and '--no-index' in command
            assert '--no-deps' not in command
        return SimpleNamespace(stdout='26.2.1\n')

    monkeypatch.setattr(module.subprocess, 'run', subprocess_run)
    module.bootstrap(isolated_build=True)
    assert len(calls) == 2
    assert calls[0][:4] == [module.sys.executable, '-m', 'pip', 'install']
    assert calls[1][0] == module.sys.executable and calls[1][1] == '-c'


def test_post_install_version_mismatch_fails(monkeypatch):
    module = load_bootstrap()
    payload = wheel_fixture()
    monkeypatch.setattr(module, 'PIP_SHA256', hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(payload))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='25.0.1\n'))
    with pytest.raises(ValueError, match='version'):
        module.bootstrap(isolated_build=True)


def test_bootstrap_is_explicit_in_ci_migrations_and_both_docker_stages():
    for filename in ('.github/workflows/ci.yml', '.github/workflows/migrations.yml'):
        workflow = (ROOT / filename).read_text(encoding='utf-8')
        assert 'python scripts/bootstrap_reviewed_pip.py --isolated-build' in workflow
        assert workflow.index('bootstrap_reviewed_pip.py --isolated-build') < workflow.index('reviewed_runtime.py install')
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    builder, runtime = dockerfile.split('# -- Stage 2:', 1)
    for stage in (builder, runtime):
        assert 'python scripts/bootstrap_reviewed_pip.py --isolated-build' in stage
    assert 'python scripts/bootstrap_reviewed_pip.py --isolated-build' not in (ROOT / 'scripts/reviewed_runtime.py').read_text()
