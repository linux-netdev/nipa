# SPDX-License-Identifier: GPL-2.0

"""Machine health state machine."""

import enum
import subprocess
import threading


class MachineState(enum.Enum):
    """Machine health states."""

    HEALTHY = "HEALTHY"
    RESERVED = "RESERVED"
    MISS_ONE = "MISS_ONE"
    MISS_TWO = "MISS_TWO"
    REBOOT_ISSUED = "REBOOT_ISSUED"


class HealthChecker(threading.Thread):
    """Machine health state machine.

    Periodically SSH-polls each non-RESERVED machine to check health.
    State transitions:
      HEALTHY -> MISS_ONE (SSH fail)
      MISS_ONE -> MISS_TWO (SSH fail again)
      MISS_TWO -> REBOOT_ISSUED (SSH fail, triggers power cycle)
      REBOOT_ISSUED -> HEALTHY (SSH succeeds)
      Any state -> HEALTHY (SSH succeeds)
    """
    def __init__(self, machines, bmc_map, interval=300, lock=None):
        """
        Parameters
        ----------
        machines : dict
            machine_id -> {'name': str, 'mgmt_ipaddr': str, 'state': MachineState}
        bmc_map : dict
            machine_id -> BMC instance
        interval : int
            Seconds between health check rounds
        lock : threading.Lock, optional
            External lock for machines dict; creates own if not provided
        """
        super().__init__(daemon=True)
        self.machines = machines
        self.bmc_map = bmc_map
        self.interval = interval
        self._stop_event = threading.Event()
        self.lock = lock if lock is not None else threading.Lock()

    def stop(self):
        """Signal the checker to stop."""
        self._stop_event.set()

    def _ssh_check(self, ipaddr):
        try:
            ret = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=10',
                 '-o', 'StrictHostKeyChecking=no',
                 '-o', 'BatchMode=yes',
                 f'root@{ipaddr}', 'uptime'],
                capture_output=True, timeout=20, check=False
            )
            return ret.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def check_machine(self, machine_id, machine):
        """Check one machine's health and update state."""
        with self.lock:
            state = machine['state']

        if state == MachineState.RESERVED:
            return

        ipaddr = machine['mgmt_ipaddr']
        alive = self._ssh_check(ipaddr)

        with self.lock:
            # Re-check in case state changed while we were polling
            state = machine['state']
            if state == MachineState.RESERVED:
                return

            if alive:
                machine['state'] = MachineState.HEALTHY
            elif state == MachineState.HEALTHY:
                machine['state'] = MachineState.MISS_ONE
                print(f"Health: {machine['name']} missed one check")
            elif state == MachineState.MISS_ONE:
                machine['state'] = MachineState.MISS_TWO
                print(f"Health: {machine['name']} missed two checks")
            elif state == MachineState.MISS_TWO:
                machine['state'] = MachineState.REBOOT_ISSUED
                print(f"Health: {machine['name']} missed three checks, rebooting")
                bmc = self.bmc_map.get(machine_id)
                if bmc:
                    bmc.power_cycle()
            elif state == MachineState.REBOOT_ISSUED:
                # Still waiting for reboot to take effect
                pass

    def run(self):
        while not self._stop_event.is_set():
            try:
                for machine_id, machine in self.machines.items():
                    if self._stop_event.is_set():
                        break
                    try:
                        self.check_machine(machine_id, machine)
                    except Exception as e:
                        print(f"Health: error checking {machine.get('name', machine_id)}: {e}")
            except Exception as e:
                print(f"Health: error in check round: {e}")
            self._stop_event.wait(self.interval)
