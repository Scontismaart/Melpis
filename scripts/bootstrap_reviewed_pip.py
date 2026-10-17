"""Explicit hash-verified pip bootstrap for a venv or disposable CI/Docker build.

This never runs implicitly from the application or dependency installer.
--isolated-build is an explicit caller assertion: never use it on a host's
global Python installation. Ordinary local use requires an activated venv.
"""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

PIP_VERSION = '26.2.1'
PIP_URL = 'https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl'
PIP_SHA256 = '71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e'


def bootstrap(*, isolated_build=False):
    if sys.prefix == sys.base_prefix and not isolated_build:
        raise ValueError('Activate a virtual environment; global host bootstrap is forbidden')
    with urllib.request.urlopen(PIP_URL, timeout=60) as response:
        payload = response.read(8 * 1024 * 1024 + 1)
    if len(payload) > 8 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != PIP_SHA256:
        raise ValueError('Official pip wheel SHA mismatch')
    with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
        metadata = wheel.read('pip-26.2.1.dist-info/METADATA')
        if b'Requires-Dist:' in metadata:
            raise ValueError('Bootstrap wheel must not introduce dependencies')
    with tempfile.TemporaryDirectory(prefix='melpis-pip-bootstrap-') as temporary:
        local_wheel = Path(temporary) / 'pip-26.2.1-py3-none-any.whl'
        local_wheel.write_bytes(payload)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade',
                        '--no-index', str(local_wheel)], check=True)
    # Importing metadata in this process could reuse the pre-upgrade import state.
    result = subprocess.run([sys.executable, '-c',
                             'import importlib.metadata; print(importlib.metadata.version("pip"))'],
                            check=True, capture_output=True, text=True)
    if result.stdout.strip() != PIP_VERSION:
        raise ValueError('Bootstrapped pip version mismatch')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-build', action='store_true',
                        help='Explicit disposable CI/container context; never a global host Python')
    args = parser.parse_args()
    bootstrap(isolated_build=args.isolated_build)
