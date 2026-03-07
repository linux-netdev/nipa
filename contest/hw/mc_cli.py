#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""CLI for the machine_control REST API."""

import argparse
import json
import os
import sys
import unicodedata

import requests

from lib.mc_client import MCClient, resolve_machines


def _sanitize(text):
    """Strip control characters except newline."""
    return ''.join(c for c in text
                   if c == '\n' or unicodedata.category(c)[0] != 'C')


def cmd_machines(args, mc):
    """List machines and their state."""
    data = mc.get_machine_info()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data:
        print("No machines found")
        return 0
    fmt = "{:<6} {:<20} {:<18} {:<15} {}"
    print(fmt.format("ID", "NAME", "MGMT IP", "STATE", "RESERVED BY"))
    for m in data:
        reserved = m.get('reserved_by', '')
        if reserved:
            reserved = f"{reserved} (res#{m.get('reservation_id', '?')})"
        print(fmt.format(m['id'], m.get('name', ''),
                         m.get('mgmt_ipaddr', ''), m.get('state', ''),
                         reserved))
    return 0


def cmd_nics(args, mc):
    """List NIC info."""
    data = mc.get_nic_info(nic_id=args.nic_id)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data:
        print("No NICs found")
        return 0
    fmt = "{:<6} {:<10} {:<12} {:<12} {:<10} {}"
    print(fmt.format("ID", "MACHINE", "VENDOR", "MODEL", "IFNAME", "PEER"))
    for n in data:
        peer = str(n.get('peer_id') or '-')
        print(fmt.format(n['id'], n.get('machine_id', ''),
                         n.get('vendor', ''), n.get('model', ''),
                         n.get('ifname', ''), peer))
    return 0


def cmd_resolve(args, mc):
    """Resolve machines needed for a NIC test."""
    all_nics = mc.get_nic_info()
    machine_ids, nic = resolve_machines(all_nics, args.nic_id)
    if args.json:
        print(json.dumps({'machine_ids': machine_ids, 'nic': nic}, indent=2))
        return 0
    print(f"NIC {nic['id']}: ifname={nic.get('ifname', '')} "
          f"machine={nic['machine_id']}")
    if nic.get('peer_id'):
        print(f"Peer NIC: {nic['peer_id']}")
    print(f"Machines to reserve: {', '.join(str(m) for m in machine_ids)}")
    return 0


def cmd_sol(args, mc):
    """Fetch SOL logs."""
    if args.follow:
        # Fetch last 10 lines to start, then poll for new ones
        data = mc.get_sol_logs(args.machine_id, start_id=args.start_id,
                               limit=10, sort='desc')
        lines = list(reversed(data.get('lines', [])))
        for entry in lines:
            ts = entry.get('ts', '')
            print(f"{ts}  {_sanitize(entry['line'])}", end='')
        last_id = data.get('last_id', 0)

        import time
        try:
            while True:
                time.sleep(args.interval)
                data = mc.get_sol_logs(args.machine_id, start_id=last_id,
                                       limit=args.limit)
                for entry in data.get('lines', []):
                    ts = entry.get('ts', '')
                    print(f"{ts}  {_sanitize(entry['line'])}", end='', flush=True)
                last_id = data.get('last_id', last_id)
        except KeyboardInterrupt:
            return 0

    data = mc.get_sol_logs(args.machine_id, start_id=args.start_id,
                           limit=args.limit)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    for entry in data.get('lines', []):
        ts = entry.get('ts', '')
        print(f"{ts}  {_sanitize(entry['line'])}", end='')
    last_id = data.get('last_id', 0)
    print(f"last_id={last_id}", file=sys.stderr)
    return 0


def cmd_reserve(args, mc):
    """Reserve machines."""
    machine_ids = [int(x.strip()) for x in args.machine_ids.split(',') if x.strip()]
    result = mc.reserve(machine_ids, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if 'reservation_id' in result else 1
    if 'reservation_id' in result:
        print(f"Reserved: reservation_id={result['reservation_id']} "
              f"timeout={result.get('timeout', '?')}")
        return 0
    print(f"Failed: {result.get('error', 'unknown error')}", file=sys.stderr)
    return 1


def cmd_refresh(args, mc):
    """Refresh a reservation."""
    result = mc.reservation_refresh(args.reservation_id)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get('ok') else 1
    if result.get('ok'):
        print("Refreshed")
        return 0
    print(f"Failed: {result.get('error', 'unknown error')}", file=sys.stderr)
    return 1


def cmd_close(args, mc):
    """Close a reservation."""
    result = mc.reservation_close(args.reservation_id)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get('ok') else 1
    if result.get('ok'):
        print("Closed")
        return 0
    print(f"Failed: {result.get('error', 'unknown error')}", file=sys.stderr)
    return 1


def cmd_power_cycle(args, mc):
    """Power cycle a machine."""
    result = mc.power_cycle(args.machine_id)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    retcode = result.get('retcode', -1)
    print(f"retcode={retcode}")
    if result.get('stdout'):
        print(f"stdout: {result['stdout']}")
    if result.get('stderr'):
        print(f"stderr: {result['stderr']}")
    return 0 if retcode == 0 else 1


def main(argv=None):
    """Entry point: parse args and dispatch subcommand."""
    parser = argparse.ArgumentParser(
        description='CLI for machine_control REST API')
    parser.add_argument('--url', default=os.environ.get('MC_URL'),
                        help='machine_control base URL (or set MC_URL env)')
    parser.add_argument('--caller', default='cli',
                        help='caller attribution string (default: cli)')
    parser.add_argument('--json', action='store_true',
                        help='output raw JSON')

    sub = parser.add_subparsers(dest='command')

    sub.add_parser('machines', help='list machines')

    p_nics = sub.add_parser('nics', help='list NICs')
    p_nics.add_argument('--nic-id', type=int, default=None,
                        help='filter by NIC ID')

    p_resolve = sub.add_parser('resolve',
                               help='resolve machines for a NIC test')
    p_resolve.add_argument('--nic-id', type=int, required=True,
                           help='NIC ID to resolve')

    p_sol = sub.add_parser('sol', help='fetch SOL logs')
    p_sol.add_argument('--machine-id', type=int, required=True,
                       help='machine ID')
    p_sol.add_argument('--start-id', type=int, default=None,
                       help='start from this log ID')
    p_sol.add_argument('--limit', type=int, default=100,
                       help='max lines to fetch (default: 100)')
    p_sol.add_argument('-f', '--follow', action='store_true',
                       help='follow output like tail -f')
    p_sol.add_argument('--interval', type=float, default=2,
                       help='poll interval in seconds for -f (default: 2)')

    p_reserve = sub.add_parser('reserve', help='reserve machines')
    p_reserve.add_argument('--machine-ids', required=True,
                           help='comma-separated machine IDs')
    p_reserve.add_argument('--timeout', type=int, default=None,
                           help='reservation timeout in seconds')

    p_refresh = sub.add_parser('refresh', help='refresh a reservation')
    p_refresh.add_argument('--reservation-id', type=int, required=True,
                           help='reservation ID')

    p_close = sub.add_parser('close', help='close a reservation')
    p_close.add_argument('--reservation-id', type=int, required=True,
                         help='reservation ID')

    p_pc = sub.add_parser('power-cycle', help='power cycle a machine')
    p_pc.add_argument('--machine-id', type=int, required=True,
                      help='machine ID')

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    if not args.url:
        parser.error('--url is required (or set MC_URL env)')

    mc = MCClient(args.url, caller=args.caller)

    commands = {
        'machines': cmd_machines,
        'nics': cmd_nics,
        'resolve': cmd_resolve,
        'sol': cmd_sol,
        'reserve': cmd_reserve,
        'refresh': cmd_refresh,
        'close': cmd_close,
        'power-cycle': cmd_power_cycle,
    }
    try:
        return commands[args.command](args, mc)
    except requests.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
