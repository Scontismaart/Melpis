"""Install a hash-locked internal dependency metadata backport; audit real files.

CrewAI code/version/license stay identical to its official 1.15.4 wheel. This
internal derivative is not an upstream release or an upstream compatibility
endorsement. No fork, dependency override, --no-deps or new advisory exception.
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
# Set from the canonical ZIP_STORED build, reviewed independently of downloads.
DERIVED_SHA256 = '74baca11ee420f0cde6c76c421092a78174c963b4fa646830df33be52d053455'
PROVENANCE_SHA256 = '11537e7bd6d0f3dd12cf26beb6e39db7503ff1f44e61a261c60cc8e0b4645def'
WHEEL_NAME = 'crewai-1.15.4-py3-none-any.whl'
METADATA_NAME = 'crewai-1.15.4.dist-info/METADATA'
RECORD_NAME = 'crewai-1.15.4.dist-info/RECORD'
OLD_CONSTRAINT = b'Requires-Dist: mcp~=1.28.1\n'
NEW_CONSTRAINT = b'Requires-Dist: mcp<1.31,>=1.30.0\n'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(data):
    return (json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n').encode()


def repack_wheel(source):
    if sha256(source) != SOURCE_SHA256:
        raise ValueError('CrewAI source SHA does not match reviewed official wheel')
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel members')
        originals = {name: archive.read(name) for name in names}
    files = dict(originals)
    if files[METADATA_NAME].count(OLD_CONSTRAINT) != 1:
        raise ValueError('Expected exactly one reviewed MCP constraint')
    files[METADATA_NAME] = files[METADATA_NAME].replace(OLD_CONSTRAINT, NEW_CONSTRAINT)
    rows = io.StringIO(newline='')
    writer = csv.writer(rows, lineterminator='\n')
    for name in sorted(files):
        if name == RECORD_NAME:
            writer.writerow((name, '', ''))
        else:
            payload = files[name]
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
            writer.writerow((name, 'sha256=' + digest, len(payload)))
    files[RECORD_NAME] = rows.getvalue().encode()
    if {name for name in files if originals[name] != files[name]} != {METADATA_NAME, RECORD_NAME}:
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
    verify_record(result)
    return result


def verify_record(wheel):
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        seen = set()
        for name, digest, size in csv.reader(io.StringIO(archive.read(RECORD_NAME).decode())):
            if name in seen:
                raise ValueError('Duplicate RECORD member')
            seen.add(name)
            if name == RECORD_NAME:
                if digest or size:
                    raise ValueError('RECORD cannot hash itself')
                continue
            payload = archive.read(name)
            expected = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
            if digest != expected or size != str(len(payload)):
                raise ValueError('Invalid wheel RECORD hash or size')
        if seen != set(archive.namelist()):
            raise ValueError('Incomplete wheel RECORD')


def make_manifest(wheel):
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        hashes = {name: sha256(archive.read(name)) for name in archive.namelist() if name != RECORD_NAME}
    return {
        'schema': 1, 'distribution': 'crewai', 'version': '1.15.4', 'mcp_version': '1.30.0',
        'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
        'derived_sha256': sha256(wheel), 'internal_metadata_backport': True,
        'upstream_endorsed': False, 'changed_entries': [METADATA_NAME, RECORD_NAME],
        'old_constraint': OLD_CONSTRAINT.decode().strip(),
        'new_constraint': NEW_CONSTRAINT.decode().strip(), 'installed_files': hashes,
    }


def render_requirements(original, wheel_path):
    lines = original.splitlines()
    if lines.count('crewai[litellm]==1.15.4') != 1:
        raise ValueError('Expected the reviewed CrewAI version and litellm extra')
    mcp_lines = [line for line in lines if re.match(r'^mcp\b', line, re.I)]
    if mcp_lines != ['mcp==1.30.0']:
        raise ValueError('Expected one exact reviewed MCP 1.30.0 pin')
    return original.replace('crewai[litellm]==1.15.4',
                            'crewai[litellm] @ ' + wheel_path.resolve().as_uri())


def prepare(requirements, output, source_wheel=None):
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
    manifest = canonical_json(make_manifest(wheel))
    if sha256(manifest) != PROVENANCE_SHA256:
        raise ValueError('Provenance manifest SHA mismatch')
    output.mkdir(parents=True, exist_ok=True)
    wheel_path = output / WHEEL_NAME
    rendered = render_requirements(requirements.read_text(encoding='utf-8'), wheel_path)
    wheel_path.write_bytes(wheel)
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


def verify_runtime(manifest_path):
    content = manifest_path.read_bytes()
    if sha256(content) != PROVENANCE_SHA256:
        raise ValueError('Provenance manifest SHA mismatch')
    manifest = json.loads(content)
    for name, version in (('crewai', '1.15.4'), ('mcp', '1.30.0')):
        if metadata.version(name) != version:
            raise ValueError('Unreviewed installed dependency version: ' + name)
    distribution = metadata.distribution('crewai')
    verify_installed_files(manifest, distribution.locate_file(''))


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
            verify_runtime(manifest)
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
