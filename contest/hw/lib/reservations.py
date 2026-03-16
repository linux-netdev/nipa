# SPDX-License-Identifier: GPL-2.0

"""Reservation manager with atomic multi-machine support."""

import datetime
import subprocess
import threading

import psycopg2

from .health import MachineState


class ReservationManager:
    """Manages machine reservations with atomic multi-machine support.

    Uses PostgreSQL advisory locks for atomicity. Tracks active
    reservations in memory with timeout management.
    """
    def __init__(self, db_pool, timeout, machine_states, bmc_map, lock=None):
        """
        Parameters
        ----------
        db_pool : psycopg2.pool.ThreadedConnectionPool
            Database connection pool
        timeout : int
            Default reservation timeout in seconds
        machine_states : dict
            machine_id -> machine dict (with 'state' key)
        bmc_map : dict
            machine_id -> BMC instance
        lock : threading.Lock, optional
            External lock for machines dict; creates own if not provided
        """
        self.db_pool = db_pool
        self.default_timeout = timeout
        self.machines = machine_states
        self.bmc_map = bmc_map
        self.lock = lock if lock is not None else threading.Lock()
        # reservation_id -> {'caller': str, 'machine_ids': list,
        #                     'last_refresh': datetime, 'timeout': int}
        self.active = {}

    def reserve(self, caller, machine_ids, timeout=None):
        """Atomically reserve machines. Returns (reservation_id, error)."""
        if timeout is None:
            timeout = self.default_timeout

        with self.lock:
            # Check all machines are HEALTHY
            for mid in machine_ids:
                if mid not in self.machines:
                    return None, f"Unknown machine {mid}"
                if self.machines[mid]['state'] != MachineState.HEALTHY:
                    return None, f"Machine {mid} is {self.machines[mid]['state'].value}"

            # All machines available, do atomic DB insert
            now = datetime.datetime.now(datetime.UTC)
            conn = self.db_pool.getconn()
            try:
                with conn.cursor() as cur:
                    # Advisory lock to prevent races
                    cur.execute("SELECT pg_advisory_lock(42)")
                    try:
                        cur.execute(
                            "INSERT INTO reservations (ts_start, status, metadata) "
                            "VALUES (%s, 'ACTIVE', %s) RETURNING id",
                            (now, caller)
                        )
                        reservation_id = cur.fetchone()[0]

                        for mid in machine_ids:
                            cur.execute(
                                "INSERT INTO reservation_machines "
                                "(reservation_id, machine_id) VALUES (%s, %s)",
                                (reservation_id, mid)
                            )
                    finally:
                        cur.execute("SELECT pg_advisory_unlock(42)")
                conn.commit()
            except psycopg2.Error as e:
                conn.rollback()
                self.db_pool.putconn(conn, close=True)
                return None, str(e)
            else:
                self.db_pool.putconn(conn)

            # Update in-memory state
            for mid in machine_ids:
                self.machines[mid]['state'] = MachineState.RESERVED

            self.active[reservation_id] = {
                'caller': caller,
                'machine_ids': machine_ids,
                'reserved_at': now,
                'last_refresh': now,
                'timeout': timeout,
            }

            return reservation_id, None

    def refresh(self, caller, reservation_id):
        """Refresh reservation timeout. Returns (ok, error)."""
        with self.lock:
            info = self.active.get(reservation_id)
            if info is None:
                return False, "Reservation not found"
            if info['caller'] != caller:
                return False, "Wrong caller"
            info['last_refresh'] = datetime.datetime.now(datetime.UTC)
            return True, None

    def close(self, caller, reservation_id):
        """Close a reservation. Returns (ok, error)."""
        with self.lock:
            info = self.active.get(reservation_id)
            if info is None:
                return False, "Reservation not found"
            if info['caller'] != caller:
                return False, "Wrong caller"
            machine_ids = info['machine_ids']
            for mid in machine_ids:
                self.machines[mid]['state'] = MachineState.HEALTHY
            del self.active[reservation_id]

        # DB update and power cycle outside the lock
        self._finish_close(reservation_id, machine_ids)
        return True, None

    def _finish_close(self, reservation_id, machine_ids):
        """DB update and reboot machines — called outside the lock."""
        now = datetime.datetime.now(datetime.UTC)
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reservations SET ts_end = %s, status = 'CLOSED' "
                    "WHERE id = %s",
                    (now, reservation_id)
                )
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            self.db_pool.putconn(conn, close=True)
            print(f"Reservation: DB error closing {reservation_id}: {e}")
            return
        else:
            self.db_pool.putconn(conn)

        for mid in machine_ids:
            machine = self.machines.get(mid)
            if not machine:
                continue
            ipaddr = machine.get('mgmt_ipaddr')
            # Try SSH reboot first (faster, no BMC involved)
            if ipaddr and self._ssh_reboot(ipaddr):
                print(f"Reservation: SSH reboot sent to machine {mid} ({ipaddr})")
                with self.lock:
                    machine['state'] = MachineState.REBOOT_ISSUED
            else:
                # Fall back to BMC power cycle
                bmc = self.bmc_map.get(mid)
                if bmc:
                    print(f"Reservation: BMC power cycle for machine {mid}")
                    bmc.power_cycle()
                    with self.lock:
                        machine['state'] = MachineState.REBOOT_ISSUED

    @staticmethod
    def _ssh_reboot(ipaddr):
        """Try to reboot a machine via SSH. Returns True on success."""
        try:
            # Probe if SSH is responsive before issuing reboot
            ret = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5',
                 '-o', 'StrictHostKeyChecking=no',
                 '-o', 'BatchMode=yes',
                 f'root@{ipaddr}', 'true'],
                capture_output=True, timeout=10, check=False
            )
            if ret.returncode != 0:
                return False
            subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5',
                 '-o', 'StrictHostKeyChecking=no',
                 '-o', 'BatchMode=yes',
                 f'root@{ipaddr}', 'reboot'],
                capture_output=True, timeout=10, check=False
            )
            return True
        except subprocess.TimeoutExpired:
            return False
        except OSError:
            return False

    def check_timeouts(self):
        """Check for and close timed-out reservations."""
        now = datetime.datetime.now(datetime.UTC)
        timed_out = []

        with self.lock:
            for rid, info in list(self.active.items()):
                elapsed = (now - info['last_refresh']).total_seconds()
                if elapsed > info['timeout']:
                    print(f"Reservation: {rid} timed out (caller={info['caller']})")
                    timed_out.append((rid, info['machine_ids']))
                    for mid in info['machine_ids']:
                        self.machines[mid]['state'] = MachineState.HEALTHY
                    del self.active[rid]

        # DB updates and power cycles outside the lock
        for rid, machine_ids in timed_out:
            self._finish_close(rid, machine_ids)


class ReservationTimeoutThread(threading.Thread):
    """Periodically checks for timed-out reservations."""
    def __init__(self, res_mgr, check_interval=60):
        super().__init__(daemon=True)
        self.res_mgr = res_mgr
        self.check_interval = check_interval
        self._stop_event = threading.Event()

    def stop(self):
        """Signal the timeout checker to stop."""
        self._stop_event.set()

    def run(self):
        """Periodically check for timed-out reservations."""
        while not self._stop_event.is_set():
            self.res_mgr.check_timeouts()
            self._stop_event.wait(self.check_interval)
