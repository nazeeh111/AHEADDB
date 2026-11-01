"""Audit local release bytes. Public CI/clone/download verification is separate."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {'.git', '.runtime', '.venv', '.aws', '.ssh', '.env', '.netrc', '.pgpass',
                 'TASK_STATE.md', '__pycache__', 'evidence', '.pytest_cache', '.mypy_cache',
                 '.ruff_cache', '.hypothesis', '.cache', '.data'}
PRIVATE_SUFFIXES = {'.pyc', '.dump', '.dmg', '.key', '.pem', '.pkl', '.joblib'}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe(name: str) -> None:
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or PRIVATE_PARTS.intersection(path.parts)
            or path.suffix in PRIVATE_SUFFIXES or any(p.startswith('confirmation') for p in path.parts)):
        raise ValueError('Private, raw experiment or unsafe member: ' + name)


def check(dist: Path) -> dict:
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    wheel = dist / f'aheaddb-{version}-py3-none-any.whl'
    source = dist / f'aheaddb-{version}.tar.gz'
    modules = [p for p in (ROOT / 'src/aheaddb').rglob('*') if p.is_file()
               and (p.suffix == '.py' or p.name == 'py.typed' or 'static' in p.parts)
               and '__pycache__' not in p.parts]
    expected = {str(p.relative_to(ROOT / 'src')): p.read_bytes() for p in modules}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel members')
        for name in names:
            safe(name)
        for name, data in expected.items():
            if archive.read(name) != data:
                raise ValueError('Wheel differs from tested source: ' + name)
        info = f'aheaddb-{version}.dist-info/'
        metadata = BytesParser().parsebytes(archive.read(info + 'METADATA'))
        if metadata['Name'] != 'aheaddb' or metadata['Version'] != version:
            raise ValueError('Wheel metadata version mismatch')
        license_names = [n for n in names if n.startswith(info + 'licenses/')]
        if set(license_names) != {info + 'licenses/LICENSE'}:
            raise ValueError('Wheel license members differ from current public source')
        if archive.read(license_names[0]) != (ROOT / 'LICENSE').read_bytes():
            raise ValueError('Wheel license bytes mismatch')
    required = ['README.md', 'LICENSE', 'pyproject.toml', 'uv.lock', 'CITATION.cff',
                'contracts/v1/openapi.json', 'docs/live-design.md', 'docs/live-quickstart.md',
                'docs/live-verification.md', 'docs/live-results.md', 'docs/live-architecture.mmd',
                'docs/releases/v0.3.0.md', 'docs/launch-v030-draft.md', 'SECURITY.md',
                '.github/workflows/ci.yml', 'assets/live/demo.mp4',
                'assets/live/signaldesk.png', 'assets/live/signaldesk-mobile.png',
                'examples/live_developer.py', 'examples/live_desk/app.py', 'examples/live_desk/index.html',
                'scripts/benchmark_live.py', 'scripts/live_metrics.py', 'scripts/summarize_live.py',
                'scripts/check_release.py', 'tests/live_reference.py']
    prefix = f'aheaddb-{version}/'
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate source archive members')
        for member in members:
            safe(member.name)
            if not member.name.startswith(prefix) and member.name != prefix.rstrip('/'):
                raise ValueError('Unexpected source archive root')
            if not (member.isfile() or member.isdir()):
                raise ValueError('Source archive contains a link or special file')
        def read(name):
            stream = archive.extractfile(prefix + name)
            if stream is None:
                raise ValueError('Missing regular archive file: ' + name)
            return stream.read()
        for name in [*required, *('src/' + n for n in expected)]:
            if read(name) != (ROOT / name).read_bytes():
                raise ValueError('Source archive differs from release checkout: ' + name)
        verification = 'artifacts/live/verification/'
        comparison = json.loads(read(verification + 'comparison.json'))
        if (not comparison['complete'] or comparison['verified_cases'] != 90
                or comparison['missing_cases'] or comparison['invalid_cases']):
            raise ValueError('Release comparison is incomplete')
        protocol_bytes = read(verification + 'protocol.json')
        protocol = json.loads(protocol_bytes)
        freeze = json.loads(read(verification + 'experiment-freeze.json'))
        cases = json.loads(gzip.decompress(read(verification + 'cases.json.gz')))
        names = {f'{case}-{policy}-{repeat}.json' for case in protocol['cases']
                 for policy in protocol['policies'] for repeat in range(protocol['repeats'])}
        if set(cases) != names or set(comparison['results_sha256']) != names:
            raise ValueError('Release raw case set is incomplete')
        if not (comparison['protocol_sha256'] == freeze['protocol_sha256'] == sha(protocol_bytes)):
            raise ValueError('Frozen protocol hash mismatch')
        for name, text in cases.items():
            if sha(text.encode()) != comparison['results_sha256'][name]:
                raise ValueError('Bundled raw case hash mismatch: ' + name)
            if any(marker in text for marker in ('/Users/', '/private/tmp/', 'postgresql://', 'postgres://')):
                raise ValueError('Personal path or connection string in bundled case: ' + name)
        if sha(read('scripts/summarize_live.py')) != freeze['report_reader_sha256']:
            raise ValueError('Frozen report reader hash mismatch')
        for name, digest in protocol['code'].items():
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT) or path.suffix != '.py':
                raise ValueError('Invalid frozen source path')
            encoded = json.dumps(read(name).decode(), sort_keys=True, default=str).encode()
            if sha(encoded) != digest:
                raise ValueError('Packaged code differs from measured frozen code: ' + name)
        for member in members:
            name = member.name.removeprefix(prefix)
            if name.startswith(verification) and member.isfile() and member.name.endswith(('.json', '.md')):
                if any(marker in read(name) for marker in
                       (b'/Users/', b'/private/tmp/', b'postgresql://', b'postgres://')):
                    raise ValueError('Personal path or connection string in curated verification: ' + name)
    return {'local_package_check': 'passed', 'version': version, 'runtime_files_match': len(expected),
            'complete_bundled_cases': len(cases), 'public_ci_clone_download_checks': 'separate, not proved here',
            'artifacts': {p.name: {'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size}
                          for p in (wheel, source)}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.dist), indent=2))


if __name__ == '__main__':
    main()
