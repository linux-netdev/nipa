# SPDX-License-Identifier: GPL-2.0

"""SOL log collector via ipmitool sol activate sessions."""

import datetime
import os
import pty
import subprocess
import threading
import time


LINE_MAX = 200
RECONNECT_DELAY = 10


class SOLCollector:
    """Collect SOL logs by running persistent ipmitool sol activate sessions.

    Spawns one ipmitool process per machine, reads stdout, and inserts
    lines into the sol table.  Automatically reconnects if a session drops.
    """

    def __init__(self, db_pool, bmc_map):
        """
        Parameters
        ----------
        db_pool : psycopg2.pool.ThreadedConnectionPool
            Database connection pool
        bmc_map : dict
            machine_id -> BMC instance
        """
        self.db_pool = db_pool
        self.bmc_map = bmc_map
        self._stop_event = threading.Event()
        self._threads = []

    def start(self):
        """Start a reader thread for each machine."""
        for machine_id, bmc in self.bmc_map.items():
            t = threading.Thread(target=self._run_session,
                                 args=(machine_id, bmc),
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        """Signal all sessions to stop."""
        self._stop_event.set()

    def _insert_chunk(self, machine_id, line, eol):
        ts = datetime.datetime.now(datetime.UTC)
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sol (machine_id, ts, line, eol) "
                    "VALUES (%s, %s, %s, %s)",
                    (machine_id, ts, line[:LINE_MAX], eol)
                )
        finally:
            self.db_pool.putconn(conn)

    def _process_data(self, machine_id, data):
        """Insert SOL data into the database, chunked at LINE_MAX."""
        for i in range(0, len(data), LINE_MAX):
            chunk = data[i:i + LINE_MAX]
            is_last = i + LINE_MAX >= len(data)
            eol = is_last and data.endswith('\n')
            self._insert_chunk(machine_id, chunk, eol)

    def _deactivate(self, bmc):
        """Deactivate any stale SOL session before starting a new one."""
        env = {**os.environ, 'IPMI_PASSWORD': bmc.bmc_pass}
        subprocess.run(
            ['ipmitool', '-I', 'lanplus',
             '-H', bmc.bmc_ipaddr, '-U', bmc.bmc_user, '-E',
             'sol', 'deactivate'],
            capture_output=True, timeout=10, check=False, env=env
        )

    def _run_session(self, machine_id, bmc):
        """Run ipmitool sol activate in a loop, reconnecting on failure."""
        while not self._stop_event.is_set():
            self._deactivate(bmc)

            env = {**os.environ, 'IPMI_PASSWORD': bmc.bmc_pass}
            # ipmitool sol activate requires a TTY (it calls tcgetattr),
            # so we allocate a pseudo-TTY for its stdin/stdout.
            master_fd, slave_fd = pty.openpty()
            try:
                proc = subprocess.Popen(
                    ['ipmitool', '-I', 'lanplus',
                     '-H', bmc.bmc_ipaddr, '-U', bmc.bmc_user, '-E',
                     'sol', 'activate'],
                    stdin=slave_fd, stdout=slave_fd, stderr=subprocess.PIPE,
                    env=env
                )
            except OSError as e:
                os.close(master_fd)
                os.close(slave_fd)
                print(f"SOL: failed to start ipmitool for machine {machine_id}: {e}")
                if self._stop_event.wait(RECONNECT_DELAY):
                    return
                continue
            finally:
                os.close(slave_fd)

            print(f"SOL: session started for machine {machine_id} "
                  f"(BMC {bmc.bmc_ipaddr})")

            try:
                while not self._stop_event.is_set():
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    text = data.decode('utf-8', 'ignore')
                    if text:
                        try:
                            self._process_data(machine_id, text)
                        except Exception as e:
                            print(f"SOL: DB error for machine {machine_id}: {e}")
            finally:
                os.close(master_fd)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

            stderr = proc.stderr.read().decode('utf-8', 'ignore').strip()
            if stderr:
                print(f"SOL: ipmitool stderr for machine {machine_id}: {stderr}")

            print(f"SOL: session ended for machine {machine_id}, "
                  f"reconnecting in {RECONNECT_DELAY}s")
            if self._stop_event.wait(RECONNECT_DELAY):
                return
