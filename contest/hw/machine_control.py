#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""NIPA HW machine_control service — Flask REST API."""

import configparser
import datetime
import json
import sys
import threading

import psycopg2
import psycopg2.pool
from flask import Flask, request, jsonify

from lib.bmc import BMC
from lib.health import HealthChecker, MachineState
from lib.reservations import ReservationManager, ReservationTimeoutThread
from lib.sol_listener import SOLCollector


app = Flask("NIPA HW machine_control")

# Global state, initialized in main()
db_pool = None  # pylint: disable=invalid-name
machines = {}      # machine_id -> {'name', 'mgmt_ipaddr', 'state'}
bmc_map = {}       # machine_id -> BMC
nic_cache = {}     # nic_id -> row dict
auth_map = {}      # machine_id -> list of allowed IPs
health_checker = None  # pylint: disable=invalid-name
res_mgr = None  # pylint: disable=invalid-name
sol_listener = None  # pylint: disable=invalid-name


def _get_client_ip():
    """Get the real client IP, respecting X-Forwarded-For from trusted proxies."""
    if request.remote_addr in ('127.0.0.1', '::1'):
        forwarded = request.headers.get('X-Real-IP')
        if forwarded:
            return forwarded
    return request.remote_addr


def _check_auth(machine_id):
    """Return True if the caller IP is authorized for this machine."""
    allowed = auth_map.get(machine_id)
    if allowed is None:
        return True  # no restrictions configured
    client_ip = _get_client_ip()
    if client_ip in allowed:
        return True
    print(f"Auth denied: {client_ip} not in allowed list for machine {machine_id}")
    return False


def load_machines(db_conn):
    """Load machine info and BMC credentials from DB."""
    _machines = {}
    _bmc_map = {}
    _bmc_ip_to_machine = {}
    _auth_map = {}

    with db_conn.cursor() as cur:
        cur.execute("SELECT id, name, mgmt_ipaddr FROM machine_info")
        for row in cur.fetchall():
            mid, name, ipaddr = row
            _machines[mid] = {
                'name': name,
                'mgmt_ipaddr': ipaddr,
                'state': MachineState.HEALTHY,
            }

        cur.execute("SELECT machine_id, bmc_ipaddr, bmc_pass, allowed_ips "
                    "FROM machine_info_sec")
        for row in cur.fetchall():
            mid, bmc_ip, bmc_pass, allowed_ips_json = row
            if mid in _machines:
                _bmc_map[mid] = BMC(bmc_ip, bmc_pass)
                _bmc_ip_to_machine[bmc_ip] = mid
                if allowed_ips_json:
                    try:
                        parsed = json.loads(allowed_ips_json)
                        _auth_map[mid] = parsed
                    except (json.JSONDecodeError, TypeError):
                        # Treat as comma-separated string
                        _auth_map[mid] = [ip.strip() for ip in allowed_ips_json.split(',')
                                          if ip.strip()]

    return _machines, _bmc_map, _bmc_ip_to_machine, _auth_map


def _recover_reservations(pool, mgr):
    """Recover active reservations from DB after restart."""
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT r.id, r.metadata, rm.machine_id "
                "FROM reservations r "
                "JOIN reservation_machines rm ON r.id = rm.reservation_id "
                "WHERE r.status = 'ACTIVE'"
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)

    by_rid = {}
    for rid, caller, mid in rows:
        by_rid.setdefault(rid, {'caller': caller, 'machine_ids': []})
        by_rid[rid]['machine_ids'].append(mid)

    now = datetime.datetime.now(datetime.UTC)
    for rid, info in by_rid.items():
        mgr.active[rid] = {
            'caller': info['caller'],
            'machine_ids': info['machine_ids'],
            'last_refresh': now,
            'timeout': mgr.default_timeout,
        }
        for mid in info['machine_ids']:
            if mid in mgr.machines:
                mgr.machines[mid]['state'] = MachineState.RESERVED
    if by_rid:
        print(f"Recovered {len(by_rid)} active reservations from DB")


def reconstruct_sol_lines(rows):
    """Reconstruct full lines from chunked SOL rows.

    Each row is (id, ts, line, eol). Chunks are concatenated until
    eol=True produces a complete line.
    """
    lines = []
    current_line = ''
    current_ts = None
    for _row_id, ts, line, eol in rows:
        if current_ts is None:
            current_ts = ts
        current_line += line or ''
        if eol:
            lines.append({'ts': str(current_ts), 'line': current_line})
            current_line = ''
            current_ts = None
    # Emit any trailing partial line
    if current_line:
        lines.append({'ts': str(current_ts), 'line': current_line})
    return lines


# --- Flask API endpoints ---

@app.route('/get_machine_info')
def get_machine_info():
    """Return public machine info and reservation status."""
    result = []
    for mid, m in machines.items():
        info = {
            'id': mid,
            'name': m['name'],
            'mgmt_ipaddr': m['mgmt_ipaddr'],
            'state': m['state'].value,
        }
        # Add reservation details if reserved
        for rid, rinfo in res_mgr.active.items():
            if mid in rinfo['machine_ids']:
                info['reservation_id'] = rid
                info['reserved_by'] = rinfo['caller']
                break
        result.append(info)
    return jsonify(result)


@app.route('/get_nic_info')
def get_nic_info():
    """Return NIC info, optionally filtered by nic_id."""
    nic_id = request.args.get('nic_id', type=int)
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            if nic_id is not None:
                cur.execute(
                    "SELECT id, machine_id, vendor, model, peer_id, "
                    "ifname, ip4addr, ip6addr FROM nic_info WHERE id = %s",
                    (nic_id,)
                )
            else:
                cur.execute(
                    "SELECT id, machine_id, vendor, model, peer_id, "
                    "ifname, ip4addr, ip6addr FROM nic_info"
                )
            rows = cur.fetchall()
    except Exception:
        db_pool.putconn(conn, close=True)
        raise
    else:
        db_pool.putconn(conn)

    result = []
    for row in rows:
        result.append({
            'id': row[0],
            'machine_id': row[1],
            'vendor': row[2],
            'model': row[3],
            'peer_id': row[4],
            'ifname': row[5],
            'ip4addr': row[6],
            'ip6addr': row[7],
        })
    return jsonify(result)


@app.route('/get_sol_logs')
def get_sol_logs():
    """Return reconstructed SOL logs with pagination."""
    machine_id = request.args.get('machine_id', type=int)
    start_id = request.args.get('start_id', 0, type=int)
    limit = request.args.get('limit', 100, type=int)
    sort = request.args.get('sort', 'asc')

    if machine_id is None:
        return jsonify({'error': 'machine_id required'}), 400

    if not _check_auth(machine_id):
        return jsonify({'error': 'unauthorized'}), 403

    order = 'ASC' if sort == 'asc' else 'DESC'
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, ts, line, eol FROM sol "
                f"WHERE machine_id = %s AND id > %s "
                f"ORDER BY id {order} LIMIT %s",
                (machine_id, start_id, limit)
            )
            rows = cur.fetchall()
    except Exception:
        db_pool.putconn(conn, close=True)
        raise
    else:
        db_pool.putconn(conn)

    last_id = max(r[0] for r in rows) if rows else start_id
    lines = reconstruct_sol_lines(rows)

    return jsonify({
        'machine_id': machine_id,
        'last_id': last_id,
        'lines': lines,
    })


@app.route('/power_cycle', methods=['POST'])
def power_cycle():
    """Power cycle a machine via its BMC."""
    data = request.get_json() or {}
    machine_id = data.get('machine_id')
    if machine_id is None:
        return jsonify({'error': 'machine_id required'}), 400

    if not _check_auth(machine_id):
        return jsonify({'error': 'unauthorized'}), 403

    bmc = bmc_map.get(machine_id)
    if bmc is None:
        return jsonify({'error': f'No BMC for machine {machine_id}'}), 404

    rc, stdout, stderr = bmc.power_cycle()
    print(f"Power cycle: machine {machine_id} by {_get_client_ip()}, rc={rc}")
    return jsonify({
        'machine_id': machine_id,
        'retcode': rc,
        'stdout': stdout,
        'stderr': stderr,
    })


@app.route('/reserve', methods=['POST'])
def reserve():
    """Atomically reserve a group of machines."""
    data = request.get_json() or {}
    caller = data.get('caller', 'unknown')
    machine_ids = data.get('machine_ids', [])
    timeout = data.get('timeout')

    if not machine_ids:
        return jsonify({'error': 'machine_ids required'}), 400

    for mid in machine_ids:
        if not _check_auth(mid):
            return jsonify({'error': 'unauthorized'}), 403

    reservation_id, error = res_mgr.reserve(caller, machine_ids, timeout)
    if error:
        print(f"Reserve: denied caller={caller} machines={machine_ids}: {error}")
        return jsonify({'error': error}), 409

    print(f"Reserve: caller={caller} machines={machine_ids} res#{reservation_id}")
    return jsonify({
        'reservation_id': reservation_id,
        'timeout': timeout or res_mgr.default_timeout,
    })


def _check_reservation_auth(reservation_id):
    """Return True if caller IP is authorized for all machines in a reservation."""
    info = res_mgr.active.get(reservation_id)
    if info is None:
        return True
    for mid in info['machine_ids']:
        if not _check_auth(mid):
            return False
    return True


@app.route('/reservation_refresh', methods=['POST'])
def reservation_refresh():
    """Refresh a reservation's timeout."""
    data = request.get_json() or {}
    caller = data.get('caller', 'unknown')
    reservation_id = data.get('reservation_id')

    if reservation_id is None:
        return jsonify({'error': 'reservation_id required'}), 400

    if not _check_reservation_auth(reservation_id):
        return jsonify({'error': 'unauthorized'}), 403

    ok, error = res_mgr.refresh(caller, reservation_id)
    if not ok:
        return jsonify({'error': error}), 400

    return jsonify({'ok': True})


@app.route('/reservation_close', methods=['POST'])
def reservation_close():
    """Close (release) a reservation."""
    data = request.get_json() or {}
    caller = data.get('caller', 'unknown')
    reservation_id = data.get('reservation_id')

    if reservation_id is None:
        return jsonify({'error': 'reservation_id required'}), 400

    if not _check_reservation_auth(reservation_id):
        return jsonify({'error': 'unauthorized'}), 403

    ok, error = res_mgr.close(caller, reservation_id)
    if not ok:
        return jsonify({'error': error}), 400

    print(f"Reservation close: caller={caller} res#{reservation_id}")
    return jsonify({'ok': True})


def main():
    """Initialize services and run Flask app."""
    global db_pool, health_checker, res_mgr, sol_listener  # pylint: disable=global-statement

    config = configparser.ConfigParser()
    cfg_paths = ['hw.config', 'machine_control.config']
    if len(sys.argv) > 1:
        cfg_paths += sys.argv[1:]
    config.read(cfg_paths)

    db_name = config.get('db', 'name', fallback='nipa')
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, database=db_name)

    # Use a temporary connection for startup queries
    conn = db_pool.getconn()
    try:
        conn.autocommit = True
        _machines, _bmc_map, bmc_ip_map, _auth_map = load_machines(conn)
    finally:
        db_pool.putconn(conn)

    machines.update(_machines)
    bmc_map.update(_bmc_map)
    auth_map.update(_auth_map)

    print(f"Loaded {len(machines)} machines, {len(bmc_map)} BMCs")

    # Shared lock for machines dict — used by both HealthChecker and
    # ReservationManager to prevent TOCTOU races
    machines_lock = threading.Lock()

    # Start SOL collector (one ipmitool session per machine)
    sol_listener = SOLCollector(db_pool, bmc_map)
    sol_listener.start()

    # Start health checker
    health_interval = config.getint('control', 'health_check_interval', fallback=300)
    health_checker = HealthChecker(machines, bmc_map, interval=health_interval,
                                   lock=machines_lock)
    health_checker.start()

    # Start reservation timeout checker
    res_timeout = config.getint('control', 'reservation_timeout', fallback=600)
    res_mgr = ReservationManager(db_pool, res_timeout, machines, bmc_map,
                                 lock=machines_lock)

    # Recover any active reservations that survived a restart
    _recover_reservations(db_pool, res_mgr)

    res_timeout_thread = ReservationTimeoutThread(res_mgr)
    res_timeout_thread.start()

    # Run with gunicorn if available, otherwise fall back to Flask dev server
    flask_host = config.get('flask', 'host', fallback='0.0.0.0')
    flask_port = config.getint('flask', 'port', fallback=5050)
    try:
        import gunicorn.app.base  # pylint: disable=import-outside-toplevel

        class MCGunicorn(gunicorn.app.base.BaseApplication):
            """Gunicorn wrapper for machine_control."""
            def load_config(self):
                self.cfg.set('bind', f'{flask_host}:{flask_port}')
                self.cfg.set('workers', 1)

            def load(self):
                return app

        MCGunicorn().run()
    except ImportError:
        app.run(host=flask_host, port=flask_port)


if __name__ == '__main__':
    main()
