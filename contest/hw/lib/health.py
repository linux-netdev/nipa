# SPDX-License-Identifier: GPL-2.0

"""Machine health state machine."""

import enum
import subprocess
import threading
import time


class MachineState(enum.Enum):
    """Machine health states."""

    HEALTHY = "HEALTHY"
    RESERVED = "RESERVED"
    MISS_ONE = "MISS_ONE"
    MISS_TWO = "MISS_TWO"
    SSH_REBOOT_ISSUED = "SSH_REBOOT_ISSUED"
    POWER_CYCLE_ISSUED = "POWER_CYCLE_ISSUED"


# How long to wait for an SSH reboot before escalating to power cycle (sec)
SSH_REBOOT_TIMEOUT = 600


class HealthChecker(threading.Thread):
    """Machine health state machine.

    Periodically SSH-polls each non-RESERVED machine to check health.
    State transitions:
      HEALTHY -> MISS_ONE (SSH fail)
      MISS_ONE -> MISS_TWO (SSH fail again)
      MISS_TWO -> POWER_CYCLE_ISSUED (SSH fail, BMC power cycle)
      SSH_REBOOT_ISSUED -> POWER_CYCLE_ISSUED (>10min and still down)
      SSH_REBOOT_ISSUED -> HEALTHY (SSH succeeds)
      POWER_CYCLE_ISSUED -> MISS_ONE (SSH fail, restart miss counter)
      POWER_CYCLE_ISSUED -> HEALTHY (SSH succeeds)
      Any state -> HEALTHY (SSH succeeds)

    SSH_REBOOT_ISSUED is set by the reservation manager when it reboots
    a machine via SSH after releasing a reservation. The health checker
    monitors it and escalates to power cycle if SSH doesn't come back.
    """
    def __init__(self, machines, bmc_map, interval=300, lock=None):
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
            state = machine['state']
            if state == MachineState.RESERVED:
                return

            if alive:
                machine['state'] = MachineState.HEALTHY
                machine.pop('ssh_reboot_at', None)
            elif state == MachineState.HEALTHY:
                machine['state'] = MachineState.MISS_ONE
                print(f"Health: {machine['name']} missed one check")
            elif state == MachineState.MISS_ONE:
                machine['state'] = MachineState.MISS_TWO
                print(f"Health: {machine['name']} missed two checks")
            elif state == MachineState.MISS_TWO:
                bmc = self.bmc_map.get(machine_id)
                if bmc:
                    bmc.power_cycle()
                machine['state'] = MachineState.POWER_CYCLE_ISSUED
                print(f"Health: {machine['name']} missed three checks, "
                      "power cycling")
            elif state == MachineState.SSH_REBOOT_ISSUED:
                elapsed = time.monotonic() - machine.get('ssh_reboot_at', 0)
                if elapsed >= SSH_REBOOT_TIMEOUT:
                    bmc = self.bmc_map.get(machine_id)
                    if bmc:
                        bmc.power_cycle()
                    machine['state'] = MachineState.POWER_CYCLE_ISSUED
                    machine.pop('ssh_reboot_at', None)
                    print(f"Health: {machine['name']} SSH reboot timed out, "
                          "power cycling")
            elif state == MachineState.POWER_CYCLE_ISSUED:
                machine['state'] = MachineState.MISS_ONE
                print(f"Health: {machine['name']} still down after power cycle")

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
            print("Health: check round complete")
            self._stop_event.wait(self.interval)
