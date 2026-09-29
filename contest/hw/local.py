# SPDX-License-Identifier: GPL-2.0

"""Database-free local frontend for the NIPA hardware worker."""

import argparse
import datetime
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import urllib.parse
import uuid

from contest.backend.result_metrics import evaluate_regressions, \
    extract_result_metrics
from contest.hw.lib.result_parser import parse_results


HW_DIR = Path(__file__).resolve().parent
ROOT_DIR = HW_DIR.parent.parent
UI_DIR = ROOT_DIR / 'ui'


SSH_OPTIONS = [
    '-o', 'ConnectTimeout=10',
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'BatchMode=yes',
]


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class _ThreadingTCPServerV6(_ThreadingTCPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def _run(command, **kwargs):
    print('+', shlex.join(str(part) for part in command))
    kwargs.setdefault('check', True)
    return subprocess.run(command, **kwargs)


def _ssh(host, command, **kwargs):
    return _run(['ssh', *SSH_OPTIONS, host, command], **kwargs)


def _rsync(source, destination):
    ssh_command = shlex.join(['ssh', *SSH_OPTIONS])
    _run(['rsync', '-a', '--exclude=__pycache__', '-e', ssh_command,
          str(source), str(destination)])


def _ssh_json(host, command):
    completed = _ssh(host, shlex.join(command), capture_output=True, text=True)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f'invalid JSON from {host}: {command[0]}') from error


def _host_addresses(host, ifname):
    links = _ssh_json(host, ['ip', '-j', 'address', 'show', 'dev', ifname])
    addresses = {}
    for address in links[0]['addr_info']:
        family = address['family']
        if address['scope'] == 'global' and family in ('inet', 'inet6') \
                and family not in addresses:
            addresses[family] = address['local']
    return addresses


def _prepare_route(host, family, source, destination, ifname):
    current = _ssh_json(
        host, ['ip', '-j', f'-{family}', 'route', 'get', destination])[0]
    if current.get('dev') == ifname and current.get('prefsrc') == source:
        return None

    desired = _ssh_json(host, [
        'ip', '-j', f'-{family}', 'route', 'get', destination,
        'from', source,
    ])[0]
    prefix = f'{destination}/{"32" if family == 4 else "128"}'
    route = ['ip', f'-{family}', 'route', 'add', prefix]
    if desired.get('gateway'):
        route.extend(['via', desired['gateway']])
    route.extend(['dev', ifname, 'src', source, 'proto', 'static'])
    _ssh(host, shlex.join(route))
    route[3] = 'del'
    print(f'{host}: routing {destination} over {ifname}')
    return host, route


def _remove_routes(routes):
    for host, route in reversed(routes):
        completed = _ssh(host, shlex.join(route), check=False)
        if completed.returncode:
            print(f'Warning: failed to remove temporary route on {host}',
                  file=sys.stderr)


def _prepare_routes(dut, peer, netif, peer_netif):
    local = _host_addresses(dut, netif)
    remote = _host_addresses(peer, peer_netif)
    routes = []
    try:
        for address_family, family in (('inet', 4), ('inet6', 6)):
            if address_family not in local or address_family not in remote:
                continue
            route = _prepare_route(
                dut, family, local[address_family], remote[address_family],
                netif)
            if route:
                routes.append(route)
            route = _prepare_route(
                peer, family, remote[address_family], local[address_family],
                peer_netif)
            if route:
                routes.append(route)
    except Exception:
        _remove_routes(routes)
        raise
    if not any(family in local and family in remote
               for family in ('inet', 'inet6')):
        raise RuntimeError('DUT and peer have no common global address family')
    return routes


def _remove_peer_key(peer, marker):
    # Overwrite authorized_keys only with a complete copy, a failure, e.g. on
    # a full disk, must not leave the peer without keys. grep exits with 1
    # when it removed every line, 2 on errors.
    command = (
        'auth="$HOME/.ssh/authorized_keys"; '
        'test -f "$auth" || exit 0; '
        'tmp=$(mktemp "${auth}.nipa.XXXXXX") || exit 1; '
        f'grep -vF {shlex.quote(marker)} "$auth" > "$tmp"; '
        'if [ $? -le 1 ]; then cat "$tmp" > "$auth"; else false; fi; '
        'status=$?; rm -f "$tmp"; exit $status'
    )
    _ssh(peer, command)


def _install_peer_key(dut, peer, remote_root):
    """Authorize an ephemeral key for SSH from the DUT to the peer."""
    marker = f'nipa-local-{uuid.uuid4().hex}'
    remote_key = f'{remote_root}/peer-key'
    with tempfile.TemporaryDirectory(prefix='nipa-peer-key-') as temporary:
        key = Path(temporary) / 'id_ed25519'
        _run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
              '-C', marker, '-f', str(key)])
        public_key = key.with_suffix('.pub').read_text(
            encoding='utf-8').strip()
        command = (
            'umask 077; mkdir -p "$HOME/.ssh"; '
            'auth="$HOME/.ssh/authorized_keys"; touch "$auth"; '
            f'grep -Fqx {shlex.quote(public_key)} "$auth" || '
            f'printf "%s\\n" {shlex.quote(public_key)} >> "$auth"'
        )
        _ssh(peer, command)
        try:
            _rsync(key, f'{dut}:{remote_key}')
            _ssh(dut, f'chmod 600 {shlex.quote(remote_key)}')
        except Exception:
            _remove_peer_key(peer, marker)
            raise
    return marker, remote_key


def _validate_assignment(value):
    key, separator, setting = value.partition('=')
    if not separator or not key or '\n' in value:
        raise argparse.ArgumentTypeError('expected KEY=VALUE')
    return key, setting


def _tree_branch(tree):
    completed = _run(
        ['git', '-C', str(tree), 'branch', '--show-current'],
        capture_output=True, text=True)
    branch = completed.stdout.strip()
    if not branch:
        raise RuntimeError(f'Linux tree is not on a branch: {tree}')
    return branch


def _resolve_tests(tree, tests):
    """Resolve kselftest selectors in a Linux source tree."""
    tree = Path(tree).expanduser().resolve()
    root = (tree / 'tools/testing/selftests').resolve()
    if not (root / 'run_kselftest.sh').is_file():
        raise RuntimeError(f'not a Linux source tree: {tree}')

    selected = []
    for test in tests:
        target, separator, program = test.partition(':')
        if not target or (separator and not program):
            raise RuntimeError(
                f'invalid test {test!r}; expected target[:program]')
        target_path = (root / target).resolve()
        if root == target_path or root not in target_path.parents:
            raise RuntimeError(f'test escapes the selftests tree: {test}')
        if not target_path.is_dir():
            raise RuntimeError(f'target does not exist: {target_path}')
        if separator:
            test_path = (target_path / program).resolve()
            if target_path not in test_path.parents:
                raise RuntimeError(f'test escapes its target: {test}')
            if not test_path.is_file():
                raise RuntimeError(f'test does not exist: {test_path}')
        selected.append(test)

    return root, list(dict.fromkeys(selected))


def _select_installed_tests(installed, selectors):
    """Expand collection selectors using the installed kselftest list."""
    list_path = installed / 'kselftest-list.txt'
    available = [line.strip() for line in list_path.read_text(
        encoding='utf-8').splitlines() if line.strip()]
    selected = []
    for selector in selectors:
        target, separator, _program = selector.partition(':')
        matches = [selector] if separator and selector in available else []
        if not separator:
            matches = [test for test in available
                       if test.startswith(f'{target}:')]
        if not matches:
            raise RuntimeError(f'test was not installed: {selector}')
        selected.extend(matches)
    return list(dict.fromkeys(selected))


def _stage_selftests(source, tests, destination):
    """Install already-built selftests into a temporary runnable tree."""
    targets = list(dict.fromkeys(
        test.partition(':')[0] for test in tests))
    make = ['make', '-C', str(source), f'TARGETS={" ".join(targets)}']

    completed = _run([*make, '-q'], check=False)
    if completed.returncode == 1:
        raise RuntimeError(
            f'selected selftests are not built; run {shlex.join(make)} first')
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, make)

    _run([*make, f'INSTALL_PATH={destination}', 'install'])


def _worker_problems(stdout):
    """Return the sentinels of a worker run a crash or a timeout cut short."""
    return [line for line in stdout.splitlines()
            if line.startswith('NIPA DETECTED ')]


def _write_worker_env(path, args):
    values = {
        'NETIF': args.netif,
        'REMOTE_TYPE': 'ssh',
        'REMOTE_ARGS': args.peer,
        'REMOTE_IFNAME': args.peer_netif,
        'NIPA_SKIP_NET_SETUP': 'yes',
        'NIPA_TEST_TIMEOUT': str(args.timeout),
    }
    values.update(dict(args.env))
    with open(path, 'w', encoding='utf-8') as fp:
        for key, value in values.items():
            fp.write(f'{key}={value}\n')


def _local_run(args):
    tests_dir, selectors = _resolve_tests(args.tree, args.test)
    branch = _tree_branch(args.tree)

    start = datetime.datetime.now(datetime.timezone.utc)
    run_name = start.strftime('%Y%m%d-%H%M%S')
    output = Path(args.output or f'nipa-results/{run_name}').expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    remote_root = f'/tmp/nipa-local-{run_name}-{os.getpid()}'
    remote_tests = f'{remote_root}/tests'
    remote_nipa = f'{remote_root}/nipa'
    remote_results = f'{remote_root}/results'

    selected_tests = []
    staging_dir = Path(tempfile.mkdtemp(prefix='nipa-selftests-'))
    installed_tests = staging_dir / 'selftests'
    worker_status = 0
    worker_stdout = ''
    worker_stderr = ''
    peer_key_marker = None
    remote_peer_key = None
    routes = []
    try:
        _stage_selftests(tests_dir, selectors, installed_tests)
        selected_tests = _select_installed_tests(installed_tests, selectors)
        test_list = ''.join(f'{test}\n' for test in selected_tests)
        _ssh(args.dut, f'mkdir -p {shlex.quote(remote_tests)} '
                        f'{shlex.quote(remote_nipa + "/contest")} '
                        f'{shlex.quote(remote_results)}')
        routes = _prepare_routes(
            args.dut, args.peer, args.netif, args.peer_netif)
        peer_key_marker, remote_peer_key = _install_peer_key(
            args.dut, args.peer, remote_root)
        _rsync(str(installed_tests) + '/', f'{args.dut}:{remote_tests}/')
        _ssh(args.dut,
             f'rm -f -- {shlex.quote(remote_tests + "/.attempted")} '
             f'{shlex.quote(remote_tests + "/.attempted.tmp")}; '
             f'rm -rf -- {shlex.quote(remote_results)}; '
             f'mkdir -p {shlex.quote(remote_results)}')
        _rsync(str(HW_DIR) + '/', f'{args.dut}:{remote_nipa}/contest/hw/')
        _rsync(str(ROOT_DIR / 'contest/remote') + '/',
               f'{args.dut}:{remote_nipa}/contest/remote/')
        _rsync(str(ROOT_DIR / 'contest/__init__.py'),
               f'{args.dut}:{remote_nipa}/contest/__init__.py')

        with tempfile.TemporaryDirectory(prefix='nipa-local-') as temporary:
            temporary = Path(temporary)
            env_path = temporary / 'nic-test.env'
            _write_worker_env(env_path, args)
            _rsync(env_path, f'{args.dut}:{remote_tests}/nic-test.env')
            list_path = temporary / 'kselftest-list.txt'
            list_path.write_text(test_list, encoding='utf-8')
            _rsync(list_path,
                   f'{args.dut}:{remote_tests}/kselftest-list.txt')

        command = (
            'export PATH=/usr/bin:$PATH; '
            'eval "$(ssh-agent -s)" >/dev/null || exit; '
            "trap 'ssh-agent -k >/dev/null 2>&1' EXIT; "
            f'ssh-add {shlex.quote(remote_peer_key)} >/dev/null 2>&1 || exit; '
            f'cd {shlex.quote(remote_nipa)}/contest/hw && '
            f'/usr/bin/python3 hw_worker.py --run-only '
            f'{shlex.quote(remote_tests)} '
            f'--results-dir {shlex.quote(remote_results)}'
        )
        completed = _ssh(args.dut, command, capture_output=True, text=True,
                         check=False)
        worker_status = completed.returncode
        worker_stdout = completed.stdout
        worker_stderr = completed.stderr
        print(worker_stdout, end='')
        if worker_stderr:
            print(worker_stderr, end='', file=sys.stderr)
    finally:
        (output / 'worker.stdout').write_text(worker_stdout, encoding='utf-8')
        (output / 'worker.stderr').write_text(worker_stderr, encoding='utf-8')
        try:
            (output / 'test-outputs').mkdir(exist_ok=True)
            _rsync(f'{args.dut}:{remote_results}/',
                   str(output / 'test-outputs') + '/')
            _run(['scp', *SSH_OPTIONS,
                  f'{args.dut}:{remote_tests}/.attempted',
                  str(output / 'attempted.json')])
        except subprocess.CalledProcessError:
            print('Warning: some result artifacts could not be fetched',
                  file=sys.stderr)
        _remove_routes(routes)
        if remote_peer_key:
            try:
                _ssh(args.dut, f'rm -f -- {shlex.quote(remote_peer_key)}')
            except subprocess.CalledProcessError:
                print('Warning: failed to remove the temporary peer key',
                      file=sys.stderr)
        if peer_key_marker:
            try:
                _remove_peer_key(args.peer, peer_key_marker)
            except subprocess.CalledProcessError:
                print('Warning: failed to revoke the temporary peer key',
                      file=sys.stderr)
        try:
            _ssh(args.dut, f'rm -rf -- {shlex.quote(remote_root)}')
        except subprocess.CalledProcessError:
            print(f'Warning: failed to remove {remote_root} on {args.dut}',
                  file=sys.stderr)
        shutil.rmtree(staging_dir, ignore_errors=True)

    if worker_status:
        raise RuntimeError(f'worker exited with status {worker_status}; '
                           f'see {output}/worker.stderr')

    data = {
        'branch': branch,
        'branch_date': start.strftime('%Y-%m-%d--%H-%M'),
        'remote': 'local',
        'executor': args.executor or args.dut,
        'start': start.isoformat(),
        'dut': args.dut,
        'peer': args.peer,
        'test_tree': str(tests_dir),
        'tests': selected_tests,
        'results': parse_results(str(output), '.'),
    }
    result_path = output / 'result.json'
    result_path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(f'Results: {result_path}')
    # Like the hardware orchestrator, look for the sentinels of the worker
    problems = _worker_problems(worker_stdout)
    for problem in problems:
        print(f'nipa local: {problem}, the run is incomplete',
              file=sys.stderr)
    return 1 if problems else 0


def _find_results(path):
    path = Path(path).expanduser().resolve()
    if path.is_file():
        return [path]
    return sorted(path.rglob('result.json'))


def _metric_rows(result_paths):
    overview = []
    details = []
    artifact_roots = {}
    next_id = 1

    loaded = []
    for path in result_paths:
        with open(path, encoding='utf-8') as fp:
            loaded.append((path, json.load(fp)))
    loaded.sort(key=lambda item: (item[1]['start'], str(item[0])))

    for artifact_id, (path, data) in enumerate(loaded):
        artifact_roots[str(artifact_id)] = path.parent
        for row in extract_result_metrics(data):
            link = row.get('link')
            if link and not urllib.parse.urlparse(link).scheme:
                relative = link.lstrip('./')
                row['link'] = f'/artifacts/{artifact_id}/{relative}'
            branch = data['branch']
            if data['remote'] == 'local':
                branch = f"{branch}-{data['branch_date']}"
            row.update({
                'id': next_id,
                'branch': branch,
                'branch_date': data['branch_date'],
                'remote': data['remote'],
                'executor': data['executor'],
                'start': data['start'],
            })
            details.append(row)
            overview.append({key: value for key, value in row.items()
                             if key != 'full_json'})
            next_id += 1

    overview = evaluate_regressions(overview)
    evaluated = {row['id']: row['short_json'] for row in overview}
    for row in details:
        row['short_json'] = evaluated[row['id']]
    return overview, details, artifact_roots


def _handler(results_path):
    class LocalHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(UI_DIR), **kwargs)

        def _send_json(self, data):
            payload = json.dumps(data, separators=(',', ':')).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        @staticmethod
        def _load_metrics():
            return _metric_rows(_find_results(results_path))

        def do_GET(self):  # pylint: disable=invalid-name
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            params = urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True)
            if path in ('/', '/metric-table.html') and \
                    not any(key == 'data' for key, _value in params):
                params.extend([
                    ('data', '/local/result-metrics.json'),
                    ('detail-data', '/local/result-metric-details.json'),
                ])
                location = '/metric-table.html?' + urllib.parse.urlencode(params)
                self.send_response(302)
                self.send_header('Location', location)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if path == '/local/result-metrics.json':
                overview, _details, _artifact_roots = self._load_metrics()
                self._send_json(overview)
                return
            if path == '/local/result-metric-details.json':
                _overview, details, _artifact_roots = self._load_metrics()
                self._send_json(details)
                return
            if path.startswith('/artifacts/'):
                _overview, _details, artifact_roots = self._load_metrics()
                parts = path.split('/', 3)
                root = artifact_roots.get(parts[2]) if len(parts) > 3 else None
                if root is None:
                    self.send_error(404)
                    return
                requested = (root / urllib.parse.unquote(parts[3])).resolve()
                if root != requested and root not in requested.parents:
                    self.send_error(403)
                    return
                if requested.is_dir():
                    requested = requested / 'stdout'
                if not requested.is_file():
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', self.guess_type(str(requested)))
                self.send_header('Content-Length', str(requested.stat().st_size))
                self.end_headers()
                with open(requested, 'rb') as fp:
                    shutil.copyfileobj(fp, self.wfile)
                return
            super().do_GET()

    return LocalHandler


def _local_view(args):
    result_paths = _find_results(args.results)
    if not result_paths:
        raise RuntimeError(f'no result.json files found under {args.results}')
    overview, _details, _artifact_roots = _metric_rows(result_paths)
    if not overview:
        raise RuntimeError('no testcase metrics found in the selected results')

    handler = _handler(args.results)
    server_class = _ThreadingTCPServerV6 if ':' in args.host else \
        _ThreadingTCPServer
    with server_class((args.host, args.port), handler) as server:
        host, port = server.server_address[:2]
        url_host = f'[{host}]' if ':' in host else host
        query = urllib.parse.urlencode({
            'data': '/local/result-metrics.json',
            'detail-data': '/local/result-metric-details.json',
        })
        url = f'http://{url_host}:{port}/metric-table.html?{query}'
        print(f'Serving {len(overview)} metric attempts from '
              f'{len(result_paths)} runs')
        print(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


def _parser():
    parser = argparse.ArgumentParser(prog='nipa local')
    subcommands = parser.add_subparsers(dest='command', required=True)

    run = subcommands.add_parser('run', help='run prepared kselftests on a DUT')
    run.add_argument('--tree', required=True, help='prepared Linux source tree')
    run.add_argument('--dut', required=True, help='DUT SSH destination')
    run.add_argument('--peer', required=True,
                     help='peer SSH destination, reachable from this host '
                          'and the DUT')
    run.add_argument('--netif', required=True, help='DUT test interface')
    run.add_argument('--peer-netif', required=True, help='peer test interface')
    run.add_argument('--test', action='append', required=True,
                     metavar='TARGET[:PROGRAM]',
                     help='kselftest collection or program; repeatable')
    run.add_argument('--env', action='append', type=_validate_assignment,
                     default=[], metavar='KEY=VALUE',
                     help='extra worker or kselftest environment variable')
    run.add_argument('--timeout', type=int, default=1200)
    run.add_argument('--output')
    run.add_argument('--executor')
    run.set_defaults(func=_local_run)

    view = subcommands.add_parser('view', help='view local result.json files')
    view.add_argument('results', nargs='?', default='nipa-results')
    view.add_argument('--host', default='::')
    view.add_argument('--port', type=int, default=44109)
    view.set_defaults(func=_local_view)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            json.JSONDecodeError) as error:
        print(f'nipa local: {error}', file=sys.stderr)
        return 1
