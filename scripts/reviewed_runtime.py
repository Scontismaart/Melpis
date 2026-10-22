"""Install a hash-locked internal dependency metadata backport; audit real files.

CrewAI code/version/license stay identical to its official 1.15.4 wheel. This
internal derivative updates the MCP/OTel metadata contracts and omits the unused CLI
edge from the server runtime. It is not an upstream release or endorsement. No
fork, dependency override, --no-deps or new advisory exception.
"""
import argparse
import base64
import csv
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import site
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

SOURCE_URL = 'https://files.pythonhosted.org/packages/a2/69/efea134272b7ca3e9cf19d5561b95c106faca7d8555af5cfc4e497ed0225/crewai-1.15.4-py3-none-any.whl'
SOURCE_SHA256 = 'bb7210ccbac3c75c231966fb5dd8d77797945c5ddb220ca3b54b50bf0a31a711'
CORE_SOURCE_URL = 'https://files.pythonhosted.org/packages/7a/40/39ed45f604160f6ab4e1e4a99c9a30236e19f23c57ae8f8fa6e04ebb256d/crewai_core-1.15.4-py3-none-any.whl'
CORE_SOURCE_SHA256 = '3af1675c6f0a5fe394edafd227d2a43ded25997c246aacbe682ee745b889a07e'
CORE_DERIVED_SHA256 = '75112b4195665f57f19f3841f17d0dbd4d042d74230412e96dd27f224a9604a7'
# Set from the canonical ZIP_STORED build, reviewed independently of downloads.
DERIVED_SHA256 = '94030a7648346a9abd60213cf7964cc82f6afd1d9c6d098c13a9e1642989396c'
PROVENANCE_SHA256 = '3f1df5aa3885af30864ea39f4a9c22197b8a68d9583517519ebad8475da5bdfa'
WHEEL_NAME = 'crewai-1.15.4-py3-none-any.whl'
METADATA_NAME = 'crewai-1.15.4.dist-info/METADATA'
RECORD_NAME = 'crewai-1.15.4.dist-info/RECORD'
CORE_WHEEL_NAME = 'crewai_core-1.15.4-py3-none-any.whl'
CORE_METADATA_NAME = 'crewai_core-1.15.4.dist-info/METADATA'
CORE_RECORD_NAME = 'crewai_core-1.15.4.dist-info/RECORD'
OLD_CONSTRAINT = b'Requires-Dist: mcp~=1.28.1\n'
NEW_CONSTRAINT = b'Requires-Dist: mcp<1.31,>=1.30.0\n'
UNUSED_CLI_REQUIREMENT = b'Requires-Dist: crewai-cli==1.15.4\n'
TELEMETRY_PACKAGES = ('opentelemetry-api', 'opentelemetry-sdk', 'opentelemetry-exporter-otlp-proto-http')
TELEMETRY_CONSTRAINTS = tuple(
    (f'Requires-Dist: {name}~=1.42.0\n'.encode(),
     f'Requires-Dist: {name}<1.46,>=1.45.1\n'.encode())
    for name in TELEMETRY_PACKAGES
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(data):
    return (json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n').encode()


def repack_wheel(source, *, core=False):
    source_sha = CORE_SOURCE_SHA256 if core else SOURCE_SHA256
    metadata_name = CORE_METADATA_NAME if core else METADATA_NAME
    record_name = CORE_RECORD_NAME if core else RECORD_NAME
    if sha256(source) != source_sha:
        raise ValueError('CrewAI source SHA does not match reviewed official wheel')
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel members')
        originals = {name: archive.read(name) for name in names}
    files = dict(originals)
    metadata_bytes = files[metadata_name]
    if not core:
        if metadata_bytes.count(OLD_CONSTRAINT) != 1:
            raise ValueError('Expected exactly one reviewed MCP constraint')
        if metadata_bytes.count(UNUSED_CLI_REQUIREMENT) != 1:
            raise ValueError('Expected exactly one unused CrewAI CLI dependency')
        metadata_bytes = metadata_bytes.replace(OLD_CONSTRAINT, NEW_CONSTRAINT)
        metadata_bytes = metadata_bytes.replace(UNUSED_CLI_REQUIREMENT, b'')
    for old, new in TELEMETRY_CONSTRAINTS:
        if metadata_bytes.count(old) != 1:
            raise ValueError('Expected exactly one reviewed OpenTelemetry constraint')
        metadata_bytes = metadata_bytes.replace(old, new)
    files[metadata_name] = metadata_bytes
    rows = io.StringIO(newline='')
    writer = csv.writer(rows, lineterminator='\n')
    for name in sorted(files):
        if name == record_name:
            writer.writerow((name, '', ''))
        else:
            payload = files[name]
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
            writer.writerow((name, 'sha256=' + digest, len(payload)))
    files[record_name] = rows.getvalue().encode()
    if {name for name in files if originals[name] != files[name]} != {metadata_name, record_name}:
        raise ValueError('Backport changed an unreviewed wheel member')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, files[name])
    result = output.getvalue()
    verify_record(result, record_name=record_name)
    return result


def verify_record(wheel, *, record_name=RECORD_NAME):
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        seen = set()
        for name, digest, size in csv.reader(io.StringIO(archive.read(record_name).decode())):
            if name in seen:
                raise ValueError('Duplicate RECORD member')
            seen.add(name)
            if name == record_name:
                if digest or size:
                    raise ValueError('RECORD cannot hash itself')
                continue
            payload = archive.read(name)
            expected = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
            if digest != expected or size != str(len(payload)):
                raise ValueError('Invalid wheel RECORD hash or size')
        if seen != set(archive.namelist()):
            raise ValueError('Incomplete wheel RECORD')


def make_manifest(wheel, core_wheel):
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        hashes = {name: sha256(archive.read(name)) for name in archive.namelist() if name != RECORD_NAME}
    with zipfile.ZipFile(io.BytesIO(core_wheel)) as archive:
        core_hashes = {name: sha256(archive.read(name)) for name in archive.namelist() if name != CORE_RECORD_NAME}
    return {
        'schema': 2, 'distribution': 'crewai', 'version': '1.15.4', 'mcp_version': '1.30.0',
        'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
        'derived_sha256': sha256(wheel), 'internal_metadata_backport': True,
        'upstream_endorsed': False, 'changed_entries': [METADATA_NAME, RECORD_NAME],
        'old_constraint': OLD_CONSTRAINT.decode().strip(),
        'new_constraint': NEW_CONSTRAINT.decode().strip(),
        'omitted_runtime_requirement': UNUSED_CLI_REQUIREMENT.decode().strip(),
        'telemetry_constraints': {old.decode().strip(): new.decode().strip()
                                  for old, new in TELEMETRY_CONSTRAINTS},
        'telemetry_versions': {name: '1.45.1' for name in TELEMETRY_PACKAGES},
        'installed_files': hashes,
        'core': {'distribution': 'crewai-core', 'version': '1.15.4',
                 'source_url': CORE_SOURCE_URL, 'source_sha256': CORE_SOURCE_SHA256,
                 'derived_sha256': sha256(core_wheel),
                 'changed_entries': [CORE_METADATA_NAME, CORE_RECORD_NAME],
                 'installed_files': core_hashes},
    }


def render_requirements(original, wheel_path, core_wheel_path):
    lines = original.splitlines()
    if lines.count('crewai[litellm]==1.15.4') != 1:
        raise ValueError('Expected the reviewed CrewAI version and litellm extra')
    core_lines = [line for line in lines if re.match(r'^crewai-core\b', line, re.I)]
    if core_lines != ['crewai-core==1.15.4']:
        raise ValueError('Expected one exact reviewed CrewAI Core 1.15.4 pin')
    mcp_lines = [line for line in lines if re.match(r'^mcp\b', line, re.I)]
    if mcp_lines != ['mcp==1.30.0']:
        raise ValueError('Expected one exact reviewed MCP 1.30.0 pin')
    for name in TELEMETRY_PACKAGES:
        pins = [line for line in lines if re.match(r'^' + re.escape(name) + r'\b', line, re.I)]
        if pins != [name + '==1.45.1']:
            raise ValueError('Expected one exact reviewed OpenTelemetry 1.45.1 pin')
    return original.replace('crewai[litellm]==1.15.4',
                            'crewai[litellm] @ ' + wheel_path.resolve().as_uri()).replace(
                                'crewai-core==1.15.4', 'crewai-core @ ' + core_wheel_path.resolve().as_uri())


def prepare(requirements, output, source_wheel=None, core_source_wheel=None):
    # Network access is limited to the official hash-locked upstream artifact.
    if source_wheel:
        source = source_wheel.read_bytes()
    else:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            source = response.read(16 * 1024 * 1024 + 1)
        if len(source) > 16 * 1024 * 1024:
            raise ValueError('Unexpected upstream wheel size')
    wheel = repack_wheel(source)
    if sha256(wheel) != DERIVED_SHA256:
        raise ValueError('Derived wheel SHA mismatch')
    if core_source_wheel:
        core_source = core_source_wheel.read_bytes()
    else:
        with urllib.request.urlopen(CORE_SOURCE_URL, timeout=60) as response:
            core_source = response.read(16 * 1024 * 1024 + 1)
    if len(core_source) > 16 * 1024 * 1024:
        raise ValueError('Unexpected upstream core wheel size')
    core_wheel = repack_wheel(core_source, core=True)
    if sha256(core_wheel) != CORE_DERIVED_SHA256:
        raise ValueError('Derived core wheel SHA mismatch')
    manifest = canonical_json(make_manifest(wheel, core_wheel))
    if sha256(manifest) != PROVENANCE_SHA256:
        raise ValueError('Provenance manifest SHA mismatch')
    output.mkdir(parents=True, exist_ok=True)
    wheel_path = output / WHEEL_NAME
    core_wheel_path = output / CORE_WHEEL_NAME
    rendered = render_requirements(requirements.read_text(encoding='utf-8'), wheel_path, core_wheel_path)
    wheel_path.write_bytes(wheel)
    core_wheel_path.write_bytes(core_wheel)
    (output / 'manifest.json').write_bytes(manifest)
    (output / 'requirements.txt').write_text(rendered, encoding='utf-8', newline='\n')
    return output / 'requirements.txt'


def verify_installed_files(manifest, location):
    base = Path(location).resolve()
    for name, digest in manifest['installed_files'].items():
        relative = PurePosixPath(name)
        candidate = (base / relative).resolve()
        if relative.is_absolute() or not candidate.is_relative_to(base):
            raise ValueError('Unsafe installed file path')
        if not candidate.is_file() or sha256(candidate.read_bytes()) != digest:
            raise ValueError('Reviewed installed file differs: ' + name)


def verify_cli_tools_absent():
    for name in ('crewai-cli', 'uv'):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        raise ValueError('Unused CLI tooling must not ship in runtime: ' + name)


def verify_runtime(manifest_path):
    content = manifest_path.read_bytes()
    if sha256(content) != PROVENANCE_SHA256:
        raise ValueError('Provenance manifest SHA mismatch')
    manifest = json.loads(content)
    for name, version in (('crewai', '1.15.4'), ('crewai-core', '1.15.4'), ('mcp', '1.30.0'),
                          *((name, '1.45.1') for name in TELEMETRY_PACKAGES)):
        if metadata.version(name) != version:
            raise ValueError('Unreviewed installed dependency version: ' + name)
    verify_cli_tools_absent()
    distribution = metadata.distribution('crewai')
    verify_installed_files(manifest, distribution.locate_file(''))
    core_distribution = metadata.distribution('crewai-core')
    verify_installed_files(manifest['core'], core_distribution.locate_file(''))


def canonical_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def installed_inventory():
    paths = list(dict.fromkeys(site.getsitepackages() + [site.getusersitepackages()]))
    paths = [path for path in paths if Path(path).is_dir()]
    installed = {}
    for distribution in metadata.distributions(path=paths):
        name = canonical_name(distribution.metadata['Name'])
        if name in installed:
            raise ValueError('Duplicate installed distribution: ' + name)
        installed[name] = distribution.version
    return paths, installed


def verify_audit_inventory(report, installed):
    if not isinstance(report, dict) or not isinstance(report.get('dependencies'), list):
        raise ValueError('Invalid audit inventory')
    audited = {}
    for entry in report['dependencies']:
        if (not isinstance(entry, dict) or 'skip_reason' in entry
                or any(not isinstance(entry.get(key), str) or not entry[key].strip()
                       or not entry[key].isprintable() for key in ('name', 'version'))
                or not isinstance(entry.get('vulns'), list)):
            raise ValueError('Incomplete audit inventory')
        name = canonical_name(entry['name'])
        if name in audited:
            raise ValueError('Duplicate audited distribution')
        audited[name] = entry['version']
    if audited != installed:
        raise ValueError('Audit inventory does not match installed names and exact versions')


def verify_final_runtime(manifest_path, report_path):
    verify_runtime(manifest_path)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    verify_audit_inventory(report, installed_inventory()[1])
    policy = Path(__file__).with_name('check_dependency_audit.py')
    subprocess.run([sys.executable, str(policy), str(report_path)], check=True)


def audit_runtime(audit_python, report_path, manifest_path):
    verify_runtime(manifest_path)
    paths, installed = installed_inventory()
    before = dict(installed)
    # Never read a stale report or delete a caller-supplied existing file.
    with tempfile.TemporaryDirectory(prefix='melpis-runtime-audit-') as temporary:
        fresh = Path(temporary) / 'audit.json'
        command = [str(audit_python), '-m', 'pip_audit', '--vulnerability-service', 'osv',
                   '--strict', '--format', 'json', '--output', str(fresh)]
        for path in paths:
            command += ['--path', path]
        result = subprocess.run(command, check=False, timeout=900)
        if result.returncode not in (0, 1) or not fresh.is_file():
            raise ValueError('Auditor failed to produce a complete report')
        content = fresh.read_bytes()
        # Preserve the actual fresh report for CI review even when coverage fails.
        # It is never accepted until the inventory and existing policy both pass.
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(content)
        report = json.loads(content)
        verify_audit_inventory(report, before)
    if installed_inventory()[1] != before:
        raise ValueError('Runtime dependencies changed during audit')
    verify_runtime(manifest_path)
    policy = Path(__file__).with_name('check_dependency_audit.py')
    subprocess.run([sys.executable, str(policy), str(report_path)], check=True)
    inventory_path = report_path.with_suffix('.inventory.json')
    inventory_path.write_bytes(canonical_json({'installed': before, 'audited': before,
                                               'service': 'osv', 'exact_versions': True,
                                               'provenance_sha256': PROVENANCE_SHA256}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'install', 'verify', 'audit'))
    parser.add_argument('--requirements', type=Path, default=Path('requirements.txt'))
    parser.add_argument('--output', type=Path, default=Path('.runtime/reviewed-dependencies'))
    parser.add_argument('--source-wheel', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--audit-python', type=Path)
    parser.add_argument('--audit-report', type=Path)
    parser.add_argument('--report', type=Path, default=Path('pip-audit.json'))
    parser.add_argument('--user', action='store_true')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise ValueError('Reviewed runtime requires Python 3.12')
    manifest = args.manifest or args.output / 'manifest.json'
    if args.action in ('prepare', 'install'):
        generated = prepare(args.requirements, args.output, args.source_wheel)
        if args.action == 'install':
            command = [sys.executable, '-m', 'pip', 'install', '-r', str(generated)]
            if args.user:
                command.append('--user')
            subprocess.run(command, check=True)
            subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
            # A newly created --user site is absent from this process's startup
            # sys.path. Verify the actual post-install interpreter, not caches.
            subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            'verify', '--manifest', str(manifest)], check=True)
    elif args.action == 'verify':
        if args.audit_report:
            verify_final_runtime(manifest, args.audit_report)
        else:
            verify_runtime(manifest)
    else:
        if not args.audit_python:
            parser.error('--audit-python is required for the isolated audit tool')
        audit_runtime(args.audit_python, args.report, manifest)


if __name__ == '__main__':
    main()
