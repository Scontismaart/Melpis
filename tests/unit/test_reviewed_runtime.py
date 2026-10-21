"""Supply-chain and installed-graph failures must stop the reviewed build."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_builder():
    path = ROOT / 'scripts/reviewed_runtime.py'
    assert path.exists(), 'Reviewed runtime builder is required'
    spec = importlib.util.spec_from_file_location('reviewed_runtime', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_wheel(*, requirement=(b'Requires-Dist: mcp~=1.28.1\n'
                                       b'Requires-Dist: crewai-cli==1.15.4\n')):
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


def test_generated_requirements_keep_extra_and_all_other_requirements():
    module = load_builder()
    original = 'fastapi==0.139.2\ncrewai[litellm]==1.15.4\nmcp==1.30.0\n'
    generated = module.render_requirements(original, Path('/isolated/crewai.whl'))
    assert 'crewai[litellm] @ ' in generated
    assert 'fastapi==0.139.2\n' in generated
    assert 'mcp==1.30.0\n' in generated
    assert len(generated.splitlines()) == 3


@pytest.mark.parametrize('original', [
    'crewai==1.15.4\nmcp==1.30.0\n',
    'crewai[litellm]==1.15.4\nmcp>=1.30.0\n',
    'crewai[litellm]==1.15.4\nmcp==1.30.0\nmcp==1.28.1\n',
])
def test_requirements_fail_closed_on_unreviewed_contract(original):
    module = load_builder()
    with pytest.raises(ValueError):
        module.render_requirements(original, Path('/isolated/crewai.whl'))


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


def test_runtime_verifier_rejects_modified_installed_code(tmp_path):
    module = load_builder()
    code = tmp_path / 'crewai/__init__.py'
    code.parent.mkdir()
    code.write_bytes(b'modified code')
    manifest = {'installed_files': {'crewai/__init__.py': hashlib.sha256(b'original code').hexdigest()}}
    with pytest.raises(ValueError, match='installed file'):
        module.verify_installed_files(manifest, tmp_path)


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
