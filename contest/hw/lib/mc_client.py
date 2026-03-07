# SPDX-License-Identifier: GPL-2.0

"""Client for the machine_control REST API."""

import requests


class MCClient:
    """Client for the machine_control REST API."""

    def __init__(self, base_url, caller="hwksft"):
        self.base_url = base_url.rstrip('/')
        self.caller = caller

    def get_machine_info(self, machine_id=None):
        """Fetch machine info from the control service."""
        params = {'caller': self.caller}
        if machine_id is not None:
            params['machine_id'] = machine_id
        r = requests.get(f'{self.base_url}/get_machine_info', params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_nic_info(self, nic_id=None):
        """Fetch NIC info from the control service."""
        params = {'caller': self.caller}
        if nic_id is not None:
            params['nic_id'] = nic_id
        r = requests.get(f'{self.base_url}/get_nic_info', params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_sol_logs(self, machine_id, start_id=None, limit=100, sort=None):
        """Fetch SOL logs for a machine."""
        params = {
            'caller': self.caller,
            'machine_id': machine_id,
            'limit': limit,
        }
        if start_id is not None:
            params['start_id'] = start_id
        if sort is not None:
            params['sort'] = sort
        r = requests.get(f'{self.base_url}/get_sol_logs', params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def reserve(self, machine_ids, timeout=None):
        """Reserve machines. Returns response dict."""
        data = {
            'caller': self.caller,
            'machine_ids': machine_ids,
        }
        if timeout is not None:
            data['timeout'] = timeout
        r = requests.post(f'{self.base_url}/reserve', json=data, timeout=30)
        if r.status_code >= 500:
            r.raise_for_status()
        return r.json()

    def reservation_refresh(self, reservation_id):
        """Refresh a reservation's timeout."""
        data = {
            'caller': self.caller,
            'reservation_id': reservation_id,
        }
        r = requests.post(f'{self.base_url}/reservation_refresh', json=data, timeout=30)
        return r.json()

    def reservation_close(self, reservation_id):
        """Close (release) a reservation."""
        data = {
            'caller': self.caller,
            'reservation_id': reservation_id,
        }
        r = requests.post(f'{self.base_url}/reservation_close', json=data, timeout=30)
        return r.json()

    def power_cycle(self, machine_id):
        """Power cycle a machine via BMC."""
        data = {
            'caller': self.caller,
            'machine_id': machine_id,
        }
        r = requests.post(f'{self.base_url}/power_cycle', json=data, timeout=30)
        r.raise_for_status()
        return r.json()


def resolve_nic_id(nic_info_list, vendor, model):
    """Resolve a NIC id from vendor and model strings."""
    for n in nic_info_list:
        if n.get('vendor') == vendor and n.get('model') == model:
            return n['id']
    raise RuntimeError(f"NIC not found: vendor={vendor}, model={model}")


def resolve_machines(nic_info_list, nic_id):
    """Resolve which machines need to be reserved for a NIC test.

    A NIC test needs the machine hosting the NIC, plus the machine
    hosting the peer NIC (if different).
    """
    nic = None
    for n in nic_info_list:
        if n['id'] == nic_id:
            nic = n
            break
    if nic is None:
        raise RuntimeError(f"NIC {nic_id} not found")

    machine_ids = [nic['machine_id']]

    # Find peer if it exists and is on a different machine
    if nic.get('peer_id'):
        for n in nic_info_list:
            if n['id'] == nic['peer_id']:
                if n['machine_id'] != nic['machine_id']:
                    machine_ids.append(n['machine_id'])
                break

    return machine_ids, nic
