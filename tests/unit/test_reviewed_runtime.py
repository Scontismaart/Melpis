"""Supply-chain and installed-graph failures must stop the reviewed build."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
OTEL_OLD = (
    b'Requires-Dist: opentelemetry-api~=1.42.0\n'
    b'Requires-Dist: opentelemetry-sdk~=1.42.0\n'
    b'Requires-Dist: opentelemetry-exporter-otlp-proto-http~=1.42.0\n'
)
OTEL_NEW = (
    b'Requires-Dist: opentelemetry-api<1.46,>=1.45.1\n'
    b'Requires-Dist: opentelemetry-sdk<1.46,>=1.45.1\n'
    b'Requires-Dist: opentelemetry-exporter-otlp-proto-http<1.46,>=1.45.1\n'
)
OTEL_PINS = (
    'opentelemetry-api==1.45.1',
    'opentelemetry-sdk==1.45.1',
    'opentelemetry-exporter-otlp-proto-http==1.45.1',
)


def load_builder():
    path = ROOT / 'scripts/reviewed_runtime.py'
    assert path.exists(), 'Reviewed runtime builder is required'
    spec = importlib.util.spec_from_file_location('reviewed_runtime', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_wheel(*, requirement=(b'Requires-Dist: mcp~=1.28.1\n'
                                       b'Requires-Dist: crewai-cli==1.15.4\n'
                                       + OTEL_OLD)):
    files = {
        'crewai/__init__.py': b'__version__="1.15.4"\n',
        'crewai-1.15.4.dist-info/METADATA': b'Name: crewai\nVersion: 1.15.4\n' + requirement,
        'crewai-1.15.4.dist-info/LICENSE': b'Synthetic fixture license\n',
        'crewai-1.15.4.dist-info/WHEEL': b'Wheel-Version: 1.0\nTag: py3-none-any\n',
    }
    record = 'crewai-1.15.4.dist-info/RECORD'
    lines = io.StringIO(newline='')
    writer = csv.writer(lines, lineterminator='\n')
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        writer.writerow((name, 'sha256=' + digest, str(len(data))))
    writer.writerow((record, '', ''))
    files[record] = lines.getvalue().encode()
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return result.getvalue()


def synthetic_core_wheel(*, requirement=OTEL_OLD):
    files = {
        'crewai_core/__init__.py': b'__version__="1.15.4"\n',
        'crewai_core-1.15.4.dist-info/METADATA': b'Name: crewai-core\nVersion: 1.15.4\n' + requirement,
        'crewai_core-1.15.4.dist-info/LICENSE': b'Synthetic core license\n',
        'crewai_core-1.15.4.dist-info/WHEEL': b'Wheel-Version: 1.0\nTag: py3-none-any\n',
    }
    record = 'crewai_core-1.15.4.dist-info/RECORD'
    lines = io.StringIO(newline='')
    writer = csv.writer(lines, lineterminator='\n')
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        writer.writerow((name, 'sha256=' + digest, str(len(data))))
    writer.writerow((record, '', ''))
    files[record] = lines.getvalue().encode()
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return result.getvalue()


def test_rejects_untrusted_source_bytes():
    module = load_builder()
    with pytest.raises(ValueError, match='source SHA'):
        module.repack_wheel(b'untrusted')


def test_patch_changes_only_constraint_and_record_preserving_license(monkeypatch):
    module = load_builder()
    source = synthetic_wheel()
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source).hexdigest())
    derived = module.repack_wheel(source)
    assert derived == module.repack_wheel(source)
    with zipfile.ZipFile(io.BytesIO(source)) as a, zipfile.ZipFile(io.BytesIO(derived)) as b:
        changed = [name for name in a.namelist() if a.read(name) != b.read(name)]
        assert set(changed) == {'crewai-1.15.4.dist-info/METADATA', 'crewai-1.15.4.dist-info/RECORD'}
        metadata_bytes = b.read(changed[0])
        assert b'Requires-Dist: mcp<1.31,>=1.30.0\n' in metadata_bytes
        assert b'Requires-Dist: crewai-cli==1.15.4\n' not in metadata_bytes
        assert OTEL_NEW in metadata_bytes
        assert OTEL_OLD not in metadata_bytes
        assert b.read('crewai-1.15.4.dist-info/LICENSE') == b'Synthetic fixture license\n'
        for name, digest, size in csv.reader(io.StringIO(b.read('crewai-1.15.4.dist-info/RECORD').decode())):
            if not digest:
                assert name.endswith('/RECORD')
                continue
            payload = b.read(name)
            assert len(payload) == int(size)
            assert digest == 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')


def test_rejects_missing_or_duplicate_target_constraint(monkeypatch):
    module = load_builder()
    for requirement in (b'', b'Requires-Dist: mcp~=1.28.1\n' * 2):
        source = synthetic_wheel(requirement=requirement)
        monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source).hexdigest())
        with pytest.raises(ValueError, match='constraint'):
            module.repack_wheel(source)


@pytest.mark.parametrize('constraint', OTEL_OLD.splitlines(keepends=True))
@pytest.mark.parametrize('duplicate', [False, True])
def test_rejects_missing_or_duplicate_otel_metadata_constraint(monkeypatch, constraint, duplicate):
    module = load_builder()
    requirement = b'Requires-Dist: mcp~=1.28.1\nRequires-Dist: crewai-cli==1.15.4\n'
    if duplicate:
        requirement += OTEL_OLD + constraint
    else:
        requirement += OTEL_OLD.replace(constraint, b'')
    source = synthetic_wheel(requirement=requirement)
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source).hexdigest())
    with pytest.raises(ValueError, match='telemetry|OpenTelemetry|constraint'):
        module.repack_wheel(source)


def test_repack_core_wheel_changes_only_otel_metadata_and_record(monkeypatch):
    module = load_builder()
    source = synthetic_core_wheel()
    monkeypatch.setattr(module, 'CORE_SOURCE_SHA256', hashlib.sha256(source).hexdigest())
    derived = module.repack_wheel(source, core=True)
    assert derived == module.repack_wheel(source, core=True)
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(io.BytesIO(derived)) as patched:
        changed = [name for name in original.namelist() if original.read(name) != patched.read(name)]
        assert set(changed) == {module.CORE_METADATA_NAME, module.CORE_RECORD_NAME}
        metadata_bytes = patched.read(module.CORE_METADATA_NAME)
        assert OTEL_NEW in metadata_bytes
        assert OTEL_OLD not in metadata_bytes
        assert patched.read('crewai_core-1.15.4.dist-info/LICENSE') == b'Synthetic core license\n'
        module.verify_record(derived, record_name=module.CORE_RECORD_NAME)


def test_rejects_untrusted_core_wheel_bytes():
    module = load_builder()
    with pytest.raises(ValueError, match='source SHA'):
        module.repack_wheel(b'untrusted core bytes', core=True)


@pytest.mark.parametrize('constraint', OTEL_OLD.splitlines(keepends=True))
@pytest.mark.parametrize('duplicate', [False, True])
def test_rejects_missing_or_duplicate_core_otel_constraint(monkeypatch, constraint, duplicate):
    module = load_builder()
    requirement = OTEL_OLD + constraint if duplicate else OTEL_OLD.replace(constraint, b'')
    source = synthetic_core_wheel(requirement=requirement)
    monkeypatch.setattr(module, 'CORE_SOURCE_SHA256', hashlib.sha256(source).hexdigest())
    with pytest.raises(ValueError, match='OpenTelemetry constraint'):
        module.repack_wheel(source, core=True)


def test_rejects_missing_or_duplicate_unused_cli_edge(monkeypatch):
    module = load_builder()
    for requirement in (
        b'Requires-Dist: mcp~=1.28.1\n',
        b'Requires-Dist: mcp~=1.28.1\n' + b'Requires-Dist: crewai-cli==1.15.4\n' * 2,
    ):
        source = synthetic_wheel(requirement=requirement)
        monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source).hexdigest())
        with pytest.raises(ValueError, match='unused CrewAI CLI dependency'):
            module.repack_wheel(source)


@pytest.mark.parametrize('installed_package', ['crewai-cli', 'uv'])
def test_runtime_rejects_unused_cli_tooling_when_installed(monkeypatch, installed_package):
    module = load_builder()

    def installed_version(name):
        if name == installed_package:
            return '1.0.0'
        raise module.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(module.metadata, 'version', installed_version)
    with pytest.raises(ValueError, match=f'must not ship in runtime: {installed_package}'):
        module.verify_cli_tools_absent()


def test_runtime_accepts_absent_cli_tooling(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(
        module.metadata,
        'version',
        lambda name: (_ for _ in ()).throw(module.metadata.PackageNotFoundError(name)),
    )

    module.verify_cli_tools_absent()


def test_generated_requirements_keep_extra_and_all_other_requirements():
    module = load_builder()
    original = (
        'fastapi==0.139.2\ncrewai[litellm]==1.15.4\ncrewai-core==1.15.4\nmcp==1.30.0\n'
        + '\n'.join(OTEL_PINS) + '\n'
    )
    generated = module.render_requirements(
        original, Path('/isolated/crewai.whl'), Path('/isolated/crewai_core.whl'),
    )
    assert 'crewai[litellm] @ ' in generated
    assert 'crewai-core @ ' in generated
    assert 'fastapi==0.139.2\n' in generated
    assert 'mcp==1.30.0\n' in generated
    for pin in OTEL_PINS:
        assert generated.count(pin) == 1
    assert len(generated.splitlines()) == 7


@pytest.mark.parametrize('original', [
    'crewai==1.15.4\ncrewai-core==1.15.4\nmcp==1.30.0\n' + '\n'.join(OTEL_PINS) + '\n',
    'crewai[litellm]==1.15.4\ncrewai-core==1.15.4\nmcp>=1.30.0\n' + '\n'.join(OTEL_PINS) + '\n',
    'crewai[litellm]==1.15.4\ncrewai-core==1.15.4\nmcp==1.30.0\nmcp==1.28.1\n' + '\n'.join(OTEL_PINS) + '\n',
])
def test_requirements_fail_closed_on_unreviewed_contract(original):
    module = load_builder()
    with pytest.raises(ValueError):
        module.render_requirements(original, Path('/isolated/crewai.whl'), Path('/isolated/crewai_core.whl'))


@pytest.mark.parametrize(('pin', 'mutation'), [
    (pin, mutation)
    for pin in OTEL_PINS
    for mutation in ('missing', 'wrong', 'duplicate')
])
def test_requirements_reject_missing_wrong_or_duplicate_otel_pins(pin, mutation):
    module = load_builder()
    original = 'crewai[litellm]==1.15.4\ncrewai-core==1.15.4\nmcp==1.30.0\n' + '\n'.join(OTEL_PINS) + '\n'
    if mutation == 'missing':
        original = original.replace(pin + '\n', '')
    elif mutation == 'wrong':
        original = original.replace(pin, pin.rsplit('==', 1)[0] + '==1.45.0')
    else:
        original += pin + '\n'
    with pytest.raises(ValueError):
        module.render_requirements(original, Path('/isolated/crewai.whl'), Path('/isolated/crewai_core.whl'))


@pytest.mark.parametrize('mutation', ['missing', 'wrong', 'duplicate'])
def test_requirements_reject_missing_wrong_or_duplicate_core_pin(mutation):
    module = load_builder()
    original = 'crewai[litellm]==1.15.4\ncrewai-core==1.15.4\nmcp==1.30.0\n' + '\n'.join(OTEL_PINS) + '\n'
    if mutation == 'missing':
        original = original.replace('crewai-core==1.15.4\n', '')
    elif mutation == 'wrong':
        original = original.replace('crewai-core==1.15.4', 'crewai-core==1.15.3')
    else:
        original += 'crewai-core==1.15.4\n'
    with pytest.raises(ValueError):
        module.render_requirements(original, Path('/isolated/crewai.whl'), Path('/isolated/crewai_core.whl'))


@pytest.mark.parametrize('entries', [
    [{'name': 'torch', 'version': '2.14.0+cpu', 'skip_reason': 'missing'}],
    [{'name': 'torch', 'version': '2.14.0', 'vulns': []}],
    [],
    [{'name': 'torch', 'version': '2.14.0+cpu', 'vulns': []}, {'name': 'extra', 'version': '1', 'vulns': []}],
    [{'name': 'torch', 'version': '2.14.0+cpu', 'vulns': []}, {'name': 'TORCH', 'version': '2.14.0+cpu', 'vulns': []}],
])
def test_inventory_rejects_skips_omissions_versions_extras_and_duplicates(entries):
    module = load_builder()
    with pytest.raises(ValueError):
        module.verify_audit_inventory({'dependencies': entries}, {'torch': '2.14.0+cpu'})


def test_inventory_accepts_exact_cpu_version_without_normalization():
    module = load_builder()
    module.verify_audit_inventory({'dependencies': [{'name': 'torch', 'version': '2.14.0+cpu', 'vulns': []}]}, {'torch': '2.14.0+cpu'})


def test_installed_inventory_ignores_disabled_user_site(tmp_path, monkeypatch):
    module = load_builder()
    runtime_site = tmp_path / 'runtime-site'
    user_site = tmp_path / 'user-site'
    runtime_site.mkdir()
    user_site.mkdir()
    monkeypatch.setattr(module.site, 'getsitepackages', lambda: [str(runtime_site)])
    monkeypatch.setattr(module.site, 'getusersitepackages', lambda: str(user_site))
    monkeypatch.setattr(module.site, 'ENABLE_USER_SITE', False)
    monkeypatch.setattr(
        module.metadata,
        'distributions',
        lambda *, path: [SimpleNamespace(metadata={'Name': 'runtime-package'}, version='1.0')]
        if path == [str(runtime_site)] else pytest.fail(f'Unexpected distribution paths: {path}'),
    )

    paths, installed = module.installed_inventory()

    assert paths == [str(runtime_site)]
    assert installed == {'runtime-package': '1.0'}


def test_installed_inventory_includes_enabled_user_site_and_rejects_duplicates(tmp_path, monkeypatch):
    module = load_builder()
    runtime_site = tmp_path / 'runtime-site'
    user_site = tmp_path / 'user-site'
    runtime_site.mkdir()
    user_site.mkdir()
    monkeypatch.setattr(module.site, 'getsitepackages', lambda: [str(runtime_site)])
    monkeypatch.setattr(module.site, 'getusersitepackages', lambda: str(user_site))
    monkeypatch.setattr(module.site, 'ENABLE_USER_SITE', True)
    monkeypatch.setattr(
        module.metadata,
        'distributions',
        lambda *, path: [SimpleNamespace(metadata={'Name': 'runtime-package'}, version='1.0')]
        if path == [str(runtime_site), str(user_site)] else pytest.fail(f'Unexpected distribution paths: {path}'),
    )

    paths, installed = module.installed_inventory()
    assert paths == [str(runtime_site), str(user_site)]
    assert installed == {'runtime-package': '1.0'}

    duplicate = SimpleNamespace(metadata={'Name': 'runtime-package'}, version='1.0')
    monkeypatch.setattr(module.metadata, 'distributions', lambda *, path: [duplicate, duplicate])
    with pytest.raises(ValueError, match='Duplicate installed distribution'):
        module.installed_inventory()


def test_runtime_verifier_rejects_modified_installed_code(tmp_path):
    module = load_builder()
    code = tmp_path / 'crewai/__init__.py'
    code.parent.mkdir()
    code.write_bytes(b'modified code')
    manifest = {'installed_files': {'crewai/__init__.py': hashlib.sha256(b'original code').hexdigest()}}
    with pytest.raises(ValueError, match='installed file'):
        module.verify_installed_files(manifest, tmp_path)


@pytest.mark.parametrize('package', [
    'opentelemetry-api',
    'opentelemetry-sdk',
    'opentelemetry-exporter-otlp-proto-http',
])
def test_runtime_verifier_requires_exact_otel_versions(tmp_path, monkeypatch, package):
    module = load_builder()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_bytes(b'{}')
    monkeypatch.setattr(module, 'PROVENANCE_SHA256', hashlib.sha256(b'{}').hexdigest())
    versions = {
        'crewai': '1.15.4',
        'crewai-core': '1.15.4',
        'mcp': '1.30.0',
        **{name: '1.45.1' for name in module.TELEMETRY_PACKAGES},
    }
    versions[package] = '1.45.0'
    monkeypatch.setattr(module.metadata, 'version', lambda name: versions[name])
    with pytest.raises(ValueError, match='Unreviewed installed dependency version'):
        module.verify_runtime(manifest_path)


def test_runtime_verifier_accepts_exact_reviewed_otel_versions(tmp_path, monkeypatch):
    module = load_builder()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_bytes(b'{"core": {}}')
    monkeypatch.setattr(module, 'PROVENANCE_SHA256', hashlib.sha256(b'{"core": {}}').hexdigest())
    versions = {
        'crewai': '1.15.4',
        'crewai-core': '1.15.4',
        'mcp': '1.30.0',
        **{name: '1.45.1' for name in module.TELEMETRY_PACKAGES},
    }
    monkeypatch.setattr(module.metadata, 'version', lambda name: versions[name])
    monkeypatch.setattr(module.metadata, 'distribution', lambda _name: SimpleNamespace(locate_file=lambda _path: tmp_path))
    monkeypatch.setattr(module, 'verify_cli_tools_absent', lambda: None)
    monkeypatch.setattr(module, 'verify_installed_files', lambda *_args: None)

    module.verify_runtime(manifest_path)


def test_reviewed_otel_and_crewai_telemetry_run_offline_without_sensitive_header_logs():
    script = r'''
import logging
import os
import socket
from importlib.metadata import version
from io import StringIO
from types import SimpleNamespace

expected = {
    "opentelemetry-api": "1.45.1",
    "opentelemetry-sdk": "1.45.1",
    "opentelemetry-exporter-otlp-proto-http": "1.45.1",
    "crewai": "1.15.4",
    "crewai-core": "1.15.4",
}
for package, wanted in expected.items():
    assert version(package) == wanted, (package, version(package))

os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["CREWAI_DISABLE_TRACKING"] = "true"

attempts = []
def blocked(*args, **kwargs):
    attempts.append((args, kwargs))
    raise AssertionError("telemetry attempted network access")
socket.create_connection = blocked
socket.getaddrinfo = blocked
socket.socket.connect = blocked

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
import crewai_core.telemetry as core_module
import crewai.telemetry.telemetry as framework_module

# Exercise the reviewed OTLP exporter constructor without exporting anything.
probe = core_module.SafeOTLPSpanExporter(endpoint="http://127.0.0.1:9/v1/traces", timeout=30)
probe.shutdown()

exporters = []
def memory_exporter(**kwargs):
    exporter = InMemorySpanExporter()
    exporters.append(exporter)
    return exporter
core_module.SafeOTLPSpanExporter = memory_exporter
framework_module.SafeOTLPSpanExporter = memory_exporter
core_module.Telemetry._instance = None
framework_module.Telemetry._instance = None
core_module.Telemetry._register_shutdown_handlers = lambda self: None
framework_module.Telemetry._register_shutdown_handlers = lambda self: None
os.environ.pop("OTEL_SDK_DISABLED")
os.environ.pop("CREWAI_DISABLE_TELEMETRY")
os.environ.pop("CREWAI_DISABLE_TRACKING")

log_stream = StringIO()
handler = logging.StreamHandler(log_stream)
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.DEBUG)
core = core_module.Telemetry()
framework = framework_module.Telemetry()
assert core.ready and framework.ready
core_module.trace = SimpleNamespace(get_tracer=core.provider.get_tracer)
framework_module.trace = SimpleNamespace(get_tracer=framework.provider.get_tracer)
try:
    core.feature_usage_span("offline-smoke")
    framework.flow_execution_span("offline-flow", ["node-a"])
    framework.feature_usage_span("offline-feature")
    assert core.provider.force_flush(timeout_millis=5000)
    assert framework.provider.force_flush(timeout_millis=5000)

    finished = [span for exporter in exporters for span in exporter.get_finished_spans()]
    assert {span.name for span in finished} >= {"Feature Usage", "Flow Execution"}
    assert all(span.end_time is not None for span in finished)
    assert all(span.status.status_code.name == "OK" for span in finished)
    assert any(span.attributes.get("feature") == "offline-smoke" for span in finished)
    assert any(span.attributes.get("flow_name") == "offline-flow" for span in finished)

    marker = "SENSITIVE_HEADER_MARKER"
    carrier = {
        "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "tracestate": "vendor=" + marker * 8000,
    }
    context = TraceContextTextMapPropagator().extract(carrier)
    state = get_current_span(context).get_span_context().trace_state
    assert len(state) == 0
    assert marker not in log_stream.getvalue()
    assert attempts == []
finally:
    core.provider.shutdown()
    framework.provider.shutdown()
    root_logger.removeHandler(handler)
'''
    result = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SENSITIVE_HEADER_MARKER' not in result.stdout + result.stderr


def test_release_paths_use_same_reviewed_installer_and_strict_audit():
    workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'python scripts/reviewed_runtime.py install' in workflow
    assert 'python scripts/reviewed_runtime.py install' in dockerfile
    assert 'python scripts/reviewed_runtime.py audit' in workflow
    assert 'pip-audit" -r requirements.txt' not in workflow
    assert 'runtime-provenance.json' in dockerfile
    assert 'mcp==1.30.0' in (ROOT / 'requirements.txt').read_text()


def test_migration_workflow_cannot_bypass_reviewed_runtime():
    workflow = (ROOT / '.github/workflows/migrations.yml').read_text(encoding='utf-8')
    assert 'python scripts/reviewed_runtime.py install' in workflow
    assert 'pip install -r requirements.txt' not in workflow


def test_each_docker_build_audits_its_actual_runtime_before_copy():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'python scripts/reviewed_runtime.py audit' in dockerfile
    assert '/tmp/pip-audit/bin/python' in dockerfile
    assert 'COPY scripts/check_dependency_audit.py' in dockerfile


def test_cache_and_generated_artifacts_follow_reviewed_inputs():
    for filename in ('.github/workflows/ci.yml', '.github/workflows/migrations.yml'):
        workflow = (ROOT / filename).read_text(encoding='utf-8')
        assert "hashFiles('requirements.txt', 'scripts/reviewed_runtime.py', 'scripts/check_dependency_audit.py', 'scripts/bootstrap_reviewed_pip.py')" in workflow
    assert '.runtime/reviewed-dependencies/' in (ROOT / '.gitignore').read_text()


def test_failed_auditor_never_reads_or_deletes_an_existing_report(tmp_path, monkeypatch):
    module = load_builder()
    report = tmp_path / 'existing.json'
    report.write_text('preserve previous report')
    monkeypatch.setattr(module, 'verify_runtime', lambda path: None)
    monkeypatch.setattr(module, 'installed_inventory', lambda: (['synthetic-path'], {'package': '1'}))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=2))
    with pytest.raises(ValueError):
        module.audit_runtime(Path('isolated-auditor'), report, Path('manifest'))
    assert report.read_text() == 'preserve previous report'


def test_final_image_inventory_must_match_the_builder_audit():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert '--audit-report /app/runtime-dependency-audit.json' in dockerfile


@pytest.mark.parametrize('entries', [
    [{'name': 'runtime-package', 'version': '1', 'vulns': []}, {'name': 'builder-extra', 'version': '1', 'vulns': []}],
    [],
])
def test_final_verification_rejects_builder_extras_or_missing_runtime_packages(tmp_path, monkeypatch, entries):
    module = load_builder()
    report = tmp_path / 'builder-audit.json'
    report.write_text(json.dumps({'dependencies': entries}))
    monkeypatch.setattr(module, 'verify_runtime', lambda path: None)
    monkeypatch.setattr(module, 'installed_inventory', lambda: (['synthetic'], {'runtime-package': '1'}))
    with pytest.raises(ValueError):
        module.verify_final_runtime(Path('manifest'), report)


def test_ci_preserves_only_public_audit_and_provenance_artifacts():
    workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    section = workflow.split('- name: Preserve runtime audit evidence', 1)
    assert len(section) == 2
    evidence = section[1].split('- name:', 1)[0]
    assert 'if: always()' in evidence
    assert 'actions/upload-artifact@v4' in evidence
    assert 'if-no-files-found: ignore' in evidence
    assert 'include-hidden-files: true' in evidence
    assert 'pip-audit.json' in evidence and 'pip-audit.inventory.json' in evidence
    assert '.runtime/reviewed-dependencies/manifest.json' in evidence
    assert 'requirements.txt' not in evidence and '*.whl' not in evidence


def test_incomplete_fresh_report_is_preserved_as_failed_evidence(tmp_path, monkeypatch):
    module = load_builder()
    report = tmp_path / 'audit.json'
    monkeypatch.setattr(module, 'verify_runtime', lambda path: None)
    monkeypatch.setattr(module, 'installed_inventory', lambda: (['synthetic'], {'package': '1'}))

    def incomplete_auditor(command, **kwargs):
        Path(command[command.index('--output') + 1]).write_text('{"dependencies": []}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, 'run', incomplete_auditor)
    with pytest.raises(ValueError):
        module.audit_runtime(Path('tool'), report, Path('manifest'))
    assert json.loads(report.read_text()) == {'dependencies': []}


def test_install_verifies_new_user_site_in_a_fresh_interpreter(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(module.sys, 'version_info', (3, 12, 0))
    monkeypatch.setattr(module.sys, 'argv', ['reviewed_runtime.py', 'install', '--user'])
    monkeypatch.setattr(module, 'prepare', lambda *args: Path('generated-requirements.txt'))
    monkeypatch.setattr(module, 'verify_runtime', lambda *args: pytest.fail('Parent cannot see newly created user site'))
    commands = []
    monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: commands.append(command))
    module.main()
    assert len(commands) == 3
    assert commands[0][:4] == [module.sys.executable, '-m', 'pip', 'install']
    assert commands[1] == [module.sys.executable, '-m', 'pip', 'check']
    assert commands[2][0] == module.sys.executable
    assert commands[2][2:4] == ['verify', '--manifest']
