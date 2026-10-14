#!/usr/bin/env python3
"""Verify a complete draft Release and its source commit without changing GitHub."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit

EXPECTED_ASSET_COUNT = 21
SHA256 = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{40}')


class VerificationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def parse_pages(text):
    """gh 2.46 --paginate emits consecutive JSON bodies, without --slurp."""
    decoder, position, pages = json.JSONDecoder(), 0, []
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            break
        try:
            value, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as error:
            raise VerificationError('GitHub API returned invalid JSON') from error
        pages.append(value)
    require(pages, 'GitHub API returned no JSON pages')
    return pages


class GitHub:
    """All requests use GET on github.com; authentication stays inside gh."""
    def __init__(self, executable='gh'):
        self.executable = executable

    def command(self, endpoint, accept='application/vnd.github+json'):
        return [self.executable, 'api', '--hostname', 'github.com', '--method', 'GET',
                '-H', 'X-GitHub-Api-Version: 2022-11-28', '-H', 'Accept: ' + accept, endpoint]

    def pages(self, endpoint):
        process = subprocess.run(self.command(endpoint) + ['--paginate'], text=True,
                                 capture_output=True, timeout=180)
        require(process.returncode == 0, f'GitHub API GET failed (exit {process.returncode}): {endpoint}')
        return parse_pages(process.stdout)

    def object(self, endpoint):
        process = subprocess.run(self.command(endpoint), text=True, capture_output=True, timeout=180)
        require(process.returncode == 0, f'GitHub API GET failed (exit {process.returncode}): {endpoint}')
        pages = parse_pages(process.stdout)
        require(len(pages) == 1 and isinstance(pages[0], dict), 'Expected one GitHub API object')
        return pages[0]

    def asset_bytes(self, endpoint):
        # Only used when GitHub omits an asset digest. Do not buffer large sources
        # in memory or echo authentication/gh stderr into public evidence.
        with tempfile.TemporaryDirectory(prefix='release-asset-check-') as temporary:
            path = Path(temporary) / 'asset'
            with path.open('wb') as stream:
                process = subprocess.run(self.command(endpoint, 'application/octet-stream'),
                                         stdout=stream, stderr=subprocess.PIPE, timeout=7200)
            require(process.returncode == 0, f'GitHub asset download failed (exit {process.returncode})')
            return sha256_file(path), path.stat().st_size


def validate_parameters(repo, tag, source_commit, workflow):
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) is not None,
            '--repo must be OWNER/REPOSITORY')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', tag) is not None and
            '..' not in tag and not tag.endswith('/') and '//' not in tag,
            '--tag must be a concrete Release tag')
    require(COMMIT.fullmatch(source_commit) is not None, '--source-commit must be a lowercase 40-character Git SHA')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+\.ya?ml|[0-9]+', workflow) is not None,
            '--workflow must be a workflow filename or numeric workflow ID')


def validate_expected(expected):
    require(isinstance(expected, dict) and len(expected) == EXPECTED_ASSET_COUNT,
            f'Expected asset manifest must contain exactly {EXPECTED_ASSET_COUNT} names')
    require('RELEASE_SHA256SUMS' in expected, 'Expected assets must include RELEASE_SHA256SUMS')
    for name, entry in expected.items():
        require(isinstance(name, str) and name not in ('.', '..') and
                not any(character in name for character in '/\\\r\n\0') and name,
                'Asset names must be plain filenames')
        require(isinstance(entry, dict) and set(entry) == {'size', 'sha256'},
                f'Expected asset fields must be exactly size and sha256: {name}')
        require(isinstance(entry['size'], int) and not isinstance(entry['size'], bool) and entry['size'] > 0,
                f'Invalid expected asset size: {name}')
        require(isinstance(entry['sha256'], str) and SHA256.fullmatch(entry['sha256']) is not None,
                f'Invalid expected asset SHA256: {name}')


def collect_assets(api, repo, release_id):
    pages = api.pages(f'repos/{repo}/releases/{release_id}/assets?per_page=100')
    require(all(isinstance(page, list) for page in pages), 'Invalid asset pagination response')
    result = {}
    for asset in [item for page in pages for item in page]:
        require(isinstance(asset, dict) and isinstance(asset.get('name'), str), 'Invalid asset record')
        name = asset['name']
        require(name not in result, f'Duplicate remote asset name: {name}')
        require(isinstance(asset.get('id'), int) and not isinstance(asset['id'], bool) and asset['id'] > 0,
                f'Invalid remote asset ID: {name}')
        result[name] = asset
    require(len({item['id'] for item in result.values()}) == len(result), 'Duplicate remote asset ID')
    return result


def release_by_tag(api, repo, tag):
    # The tag endpoint is intended for published releases and can return 404
    # for an untagged draft. Authenticated release listing includes drafts.
    pages = api.pages(f'repos/{repo}/releases?per_page=100')
    require(all(isinstance(page, list) for page in pages), 'Invalid Release pagination response')
    matches = [release for page in pages for release in page
               if isinstance(release, dict) and release.get('tag_name') == tag]
    require(len(matches) == 1, 'Expected exactly one accessible Release for the requested tag')
    return matches[0]


def validate_release(release, tag, source_commit):
    require(isinstance(release.get('id'), int) and not isinstance(release['id'], bool) and release['id'] > 0,
            'Invalid Release ID')
    require(release.get('draft') is True, 'Release must remain a draft')
    require(release.get('tag_name') == tag, 'Release tag differs')
    require(release.get('target_commitish') == source_commit, 'Release target is not the exact source commit')


def release_url(release, repo):
    value = release.get('html_url')
    require(isinstance(value, str), 'Release HTML URL is missing')
    parsed = urlsplit(value)
    prefix = f'/{repo}/releases/tag/'
    require(parsed.scheme == 'https' and parsed.netloc == 'github.com' and
            parsed.path.startswith(prefix) and len(parsed.path) > len(prefix) and
            not parsed.query and not parsed.fragment and
            not any(part in ('.', '..') for part in unquote(parsed.path).split('/')),
            'Release HTML URL is outside the expected GitHub repository')
    return value


def validate_main(api, repo, source_commit):
    reference = api.object(f'repos/{repo}/git/ref/heads/main')
    require(reference.get('object', {}).get('type') == 'commit' and
            reference['object'].get('sha') == source_commit, 'Remote main differs from the source commit')


def successful_workflow(api, repo, workflow, source_commit):
    endpoint = f'repos/{repo}/actions/workflows/{quote(workflow, safe="")}/runs?head_sha={source_commit}&per_page=100'
    pages = api.pages(endpoint)
    require(all(isinstance(page, dict) and isinstance(page.get('workflow_runs'), list) for page in pages),
            'Invalid workflow pagination response')
    runs = [run for page in pages for run in page['workflow_runs']
            if isinstance(run, dict) and run.get('head_sha') == source_commit and
            run.get('head_branch') == 'main' and run.get('event') in ('push', 'workflow_dispatch')]
    require(runs, 'No workflow run for the specified source commit on main')
    require(all(isinstance(run.get(key), int) and not isinstance(run[key], bool) and run[key] > 0
                for run in runs for key in ('id', 'run_number', 'run_attempt')), 'Invalid workflow run identity')
    latest = max(runs, key=lambda run: (run['run_number'], run['run_attempt'], run['id']))
    require(latest.get('status') == 'completed' and latest.get('conclusion') == 'success',
            'Latest matching workflow run has not completed successfully')
    return {'workflow': workflow, 'run_id': latest['id'], 'run_number': latest['run_number'],
            'run_attempt': latest['run_attempt'], 'head_sha': source_commit,
            'event': latest['event'], 'status': 'completed', 'conclusion': 'success',
            'url': f'https://github.com/{repo}/actions/runs/{latest["id"]}',
            'scope': 'Repository host checks; CI is not evidence of a full kernel build or device test'}


def snapshot(assets):
    return {name: {key: asset.get(key) for key in ('id', 'name', 'size', 'state', 'digest')}
            for name, asset in assets.items()}


def verify(api, repo, tag, expected, source_commit, workflow):
    validate_parameters(repo, tag, source_commit, workflow)
    validate_expected(expected)
    validate_main(api, repo, source_commit)
    release = release_by_tag(api, repo, tag)
    validate_release(release, tag, source_commit)
    assets = collect_assets(api, repo, release['id'])
    require(assets.keys() == expected.keys(),
            'Asset name mismatch: missing=' + str(sorted(expected.keys() - assets.keys())) +
            ' extra=' + str(sorted(assets.keys() - expected.keys())))
    checked = {}
    for name, wanted in sorted(expected.items()):
        actual = assets[name]
        require(actual.get('state') == 'uploaded', f'Asset is not uploaded: {name}')
        require(type(actual.get('size')) is int and actual['size'] == wanted['size'], f'Asset size differs: {name}')
        digest = actual.get('digest')
        if digest is None or digest == '':
            print(f'GitHub digest absent; downloading one asset to verify: {name}', flush=True)
            downloaded_sha, downloaded_size = api.asset_bytes(f'repos/{repo}/releases/assets/{actual["id"]}')
            require(downloaded_size == wanted['size'] and downloaded_sha == wanted['sha256'],
                    f'Downloaded asset checksum or size differs: {name}')
            method = 'DOWNLOADED_SHA256'
        else:
            require(digest == 'sha256:' + wanted['sha256'], f'GitHub asset digest differs: {name}')
            method = 'GITHUB_SHA256_DIGEST'
        checked[name] = {'asset_id': actual['id'], 'size': wanted['size'], 'sha256': wanted['sha256'],
                         'state': 'uploaded', 'verification': method}
    workflow_result = successful_workflow(api, repo, workflow, source_commit)
    # Downloads may be slow. Re-read identities/assets before attesting the result.
    validate_main(api, repo, source_commit)
    after_release = api.object(f'repos/{repo}/releases/{release["id"]}')
    validate_release(after_release, tag, source_commit)
    require(after_release['id'] == release['id'], 'Release identity changed during verification')
    require(snapshot(collect_assets(api, repo, release['id'])) == snapshot(assets),
            'Release assets changed during verification')
    return {'schema_version': 1, 'status': 'PASS_COMPLETE_DRAFT_RELEASE_SYNC',
            'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'repository': f'https://github.com/{repo}', 'source_commit': source_commit,
            'remote_main': source_commit, 'release_id': release['id'], 'tag': tag,
            'release_url': release_url(after_release, repo),
            'release_draft': True, 'release_target_commitish': source_commit,
            'asset_count': len(checked), 'assets': checked, 'missing_assets': [],
            'ci': workflow_result, 'kernel_full_build_verified_by_this_tool': False,
            'runtime': 'NOT_TESTED_BY_THIS_HOST_VERIFIER', 'phone_modified': False,
            'runtime_scope': 'These two fields describe this read-only host verifier only; '
                             'candidate device results are recorded separately in runtime_manifest.json.',
            'github_mutations_performed': False}


def atomic_report(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.name + '.', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--expected-assets-json', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.output.resolve() != args.expected_assets_json.resolve(), 'Output cannot overwrite expected assets')
    require(not args.output.is_symlink(), 'Output receipt cannot be a symlink')
    validate_parameters(args.repo, args.tag, args.source_commit, args.workflow)
    require(args.expected_assets_json.is_file(), 'Expected asset JSON is missing')
    try:
        expected_bytes = args.expected_assets_json.read_bytes()
        expected = json.loads(expected_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError('Expected asset JSON is invalid') from error
    validate_expected(expected)
    # Do not retain an earlier success after a failed remote recheck.
    args.output.unlink(missing_ok=True)
    report = verify(GitHub(), args.repo, args.tag, expected, args.source_commit, args.workflow)
    require(args.expected_assets_json.read_bytes() == expected_bytes, 'Expected asset manifest changed during verification')
    report['expected_assets_manifest_sha256'] = hashlib.sha256(expected_bytes).hexdigest()
    atomic_report(args.output, report)
    print(f'PASS: {report["asset_count"]} assets; draft preserved; source commit and host CI verified.')


if __name__ == '__main__':
    try:
        main()
    except (VerificationError, OSError, subprocess.SubprocessError) as error:
        # OSError/gh stderr can contain private paths. Keep the public CLI error concise.
        message = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print('error: ' + message, file=sys.stderr)
        sys.exit(1)
