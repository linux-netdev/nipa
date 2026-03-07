# SPDX-License-Identifier: GPL-2.0

"""BMC/IPMI control wrapper."""

import os
import subprocess


class BMC:
    """Wrapper around ipmitool for BMC operations."""

    def __init__(self, bmc_ipaddr, bmc_pass, bmc_user="admin"):
        self.bmc_ipaddr = bmc_ipaddr
        self.bmc_pass = bmc_pass
        self.bmc_user = bmc_user

    def _ipmitool(self, args, timeout=30):
        """Run an ipmitool command. Returns (retcode, stdout, stderr).

        Uses -E flag so ipmitool reads the password from the
        IPMI_PASSWORD environment variable instead of -P, which would
        be visible in ps output.
        """
        cmd = [
            'ipmitool', '-I', 'lanplus',
            '-H', self.bmc_ipaddr,
            '-U', self.bmc_user,
            '-E',
        ] + args
        env = {**os.environ, 'IPMI_PASSWORD': self.bmc_pass}
        try:
            ret = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                 check=False, env=env)
        except subprocess.TimeoutExpired:
            return (1, '', 'timeout')
        return (ret.returncode,
                ret.stdout.decode('utf-8', 'ignore'),
                ret.stderr.decode('utf-8', 'ignore'))

    def power_cycle(self):
        """Issue chassis power cycle."""
        return self._ipmitool(['chassis', 'power', 'cycle'])

    def power_on(self):
        """Issue chassis power on."""
        return self._ipmitool(['chassis', 'power', 'on'])

    def power_off(self):
        """Issue chassis power off."""
        return self._ipmitool(['chassis', 'power', 'off'])

    def power_status(self):
        """Query chassis power status."""
        return self._ipmitool(['chassis', 'power', 'status'])

    def sol_activate(self):
        """Activate SOL session."""
        return self._ipmitool(['sol', 'activate'], timeout=5)

    def sol_deactivate(self):
        """Deactivate SOL session."""
        return self._ipmitool(['sol', 'deactivate'], timeout=5)
