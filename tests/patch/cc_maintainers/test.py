# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

"""
Test if relevant maintainers were CCed
"""

import datetime
import email
import email.utils
import json
import os
import re
import subprocess
import tempfile
from typing import Tuple

emailpat = re.compile(r'([^ <"]*@[^ >"]*)')

ignore_emails = {
    'linux-kernel@vger.kernel.org',  # Don't expect people to CC LKML on everything
    'nipa@patchwork.hopto.org',      # For new files NIPA will get marked as committer
    'jeffrey.t.kirsher@intel.com',
    'sln@onemain.com',
    'rafalo@cadence.com',
    'luoj@codeaurora.org',
    'lokeshvutla@ti.com',
    'grygorii.strashko@ti.com',
    'davem@davemloft.net',
    'raju.lakkaraju@microchip.com',
    'arvid.brodin@alten.se'
}

# Maintainers who don't CC their co-employees
maintainers = {
    'michael.chan@broadcom.com': ['@broadcom.com'],
    'huangguangbin2@huawei.com': ['@huawei.com', '@hisilicon.com'],
    'anthony.l.nguyen@intel.com': ['@intel.com', '@linux.intel.com', '@lists.osuosl.org'],
    'saeed@kernel.org': [
        '@nvidia.com', '@mellanox.com', 'leon@kernel.org', 'linux-rdma@vger.kernel.org'
    ]
}

corp_suffix = ['@broadcom.com', '@huawei.com', '@intel.com', '@nvidia.com']

pull_requesters = {'mkl@pengutronix.de', 'steffen.klassert@secunet.com',
                   'pablo@netfilter.org', 'fw@strlen.de'}

local_map = ["Vladimir Oltean <vladimir.oltean@nxp.com> <olteanv@gmail.com>",
             "Jiri Pirko <jiri@nvidia.com> <jiri@resnulli.us>",
             "Ido Schimmel <idosch@nvidia.com> <idosch@mellanox.com>",
             "Russell King <rmk+kernel@armlinux.org.uk> <linux@armlinux.org.uk>",
             "John Fastabend <john.r.fastabend@intel.com> <john.fastabend@gmail.com>",
             "Sergey Shtylyov <sergei.shtylyov@cogentembedded.com> <s.shtylyov@omp.ru>",
             "Arseniy Krasnov <AVKrasnov@sberdevices.ru> <avkrasnov@salutedevices.com>",
             "Francesco Ruggeri <fruggeri@arista.com> <fruggeri05@gmail.com>",
             "Willem de Bruijn <willemdebruijn.kernel@gmail.com> <willemb@google.com>",
             "Alexander Duyck <alexanderduyck@fb.com> <alexander.duyck@gmail.com>"]

#
# Maintainer auto-staleness checking
#

class StalenessEntry:
    def __init__(self, e, since_months):
        self.email = e

        self._load_time = None
        self._query_output = None
        self._query_depth = None
        self._newest_mid = None

        self._month_age = None

        self.reload(since_months)

    def __repr__(self):
        return f'Staleness({self.email}, age:{self._month_age} search depth:{self._query_depth}, ' + \
               f'mid:{self._newest_mid})'

    def reload(self, since_months):
        res = subprocess.run(['lei', 'q', f"f:{self.email} AND d:{since_months}.months.ago..",
                              '--no-save', '-q', '-O', 'https://lore.kernel.org/netdev'],
                             stdout=subprocess.PIPE)
        output = res.stdout.decode('utf-8', 'replace')

        self._query_output = json.loads(output)
        self._query_depth = since_months
        self._load_time = datetime.datetime.now(datetime.UTC)

        newest = None
        for e in self._query_output:
            # Lei adds a null at the end of the list
            if not e:
                continue
            dt = datetime.datetime.fromisoformat(e["rt"])
            if not newest or dt > newest:
                newest = dt
                self._newest_mid = e["m"]

        if not newest:
            self._month_age = 999
        else:
            self._month_age = (self._load_time - newest).seconds / 60 / 60 / 24 / 30

    def is_stale(self, since_months, dbg=None):
        if datetime.datetime.now(datetime.UTC) - self._load_time > datetime.timedelta(weeks=2):
            if dbg is not None:
                dbg.append("Cache expired for " + self.email)
            self.reload(since_months)

        # We know it's not stale, doesn't matter how deep the entry is
        if self._month_age <= since_months:
            return False
        # The query may have not been deep enough, refresh..
        if self._query_depth < since_months:
            self.reload(since_months)
        return self._month_age > since_months


class StalenessDB:
    def __init__(self):
        self._db = {}

    def is_stale(self, e, since_months, dbg=None):
        if e not in self._db:
            self._db[e] = StalenessEntry(e, since_months)

        ret = self._db[e].is_stale(since_months, dbg)

        if dbg is not None:
            dbg.append(repr(self._db[e]))
        return ret


stale_db = StalenessDB()


def get_stale(sender_from, missing, out):
    sender_corp = None
    for corp in corp_suffix:
        if sender_from.endswith(corp):
            sender_corp = corp
            break

    ret = set()
    for e in missing:
        months = 36
        # People within the same corp know sooner when others leave
        if sender_corp and e.endswith(sender_corp):
            months = 12
        if stale_db.is_stale(e, months, out):
            ret.add(e)
    return ret

#
# Main
#

def cc_maintainers(tree, thing, result_dir) -> Tuple[int, str, str]:
    """ Main test entry point """
    out = []
    raw_gm = []
    patch = thing

    if patch.series and patch.series.cover_pull:
        return 0, "Pull request co-post, skipping", ""

    msg = email.message_from_string(patch.raw_patch)
    addrs = msg.get_all('to', [])
    addrs += msg.get_all('cc', [])
    addrs += msg.get_all('from', [])
    addrs += msg.get_all('sender', [])
    included = set([e.lower() for n, e in email.utils.getaddresses(addrs)])
    out += ["=== Email ===",
            f"From: {msg.get_all('from')}",
            f"Included: {included}", ""]

    ignore_domains = []
    sender_from = msg.get_all('from', ['nobody@nothing'])[0]
    match = emailpat.search(sender_from)
    if match:
        sender = match.group(1)
        if sender in maintainers:
            ignore_domains = maintainers[sender]

    expected = set()
    blamed = set()
    pure_blamed = set()
    ignored = set()
    with tempfile.NamedTemporaryFile() as fp:
        patch.write_out(fp)
        command = ['./scripts/get_maintainer.pl', '--git-min-percent', '35', '--', fp.name]
        with subprocess.Popen(command, cwd=tree.path, stdout=subprocess.PIPE) as p:
            line = p.stdout.readline().decode('utf8', 'replace')
            while line:
                raw_gm.append(line.strip())
                match = emailpat.search(line)
                if match:
                    addr = match.group(1).lower()
                    expected.add(addr)
                    if 'blamed_fixes' in line:
                        blamed.add(addr)
                        if 'maintainer' not in line:
                            pure_blamed.add(addr)
                for domain in ignore_domains:
                    if domain in addr:
                        ignored.add(addr)
                line = p.stdout.readline().decode('utf8', 'replace')
            p.wait()

    expected.difference_update(ignore_emails)
    blamed.difference_update(ignore_emails)

    out += ["=== get_maint wants ===",
            f"Expected: {expected}",
            f"Blamed: {blamed}",
            f"Pure blames: {pure_blamed}",
            f"Ignored: {ignored}", ""]

    expected.difference_update(ignored)
    blamed.difference_update(ignored)

    found = expected.intersection(included)
    missing = expected.difference(included)
    missing_blamed = blamed.difference(included)

    stale_log = []
    stale = get_stale(sender_from, missing_blamed, stale_log)
    out.append(f"Stale: {stale}")

    # Ditch all stale from blames, and from missing only those stales who aren't maintainers.
    missing_blamed = missing_blamed.difference(stale)
    stale_pure_blames = pure_blamed.intersection(stale)
    missing = missing.difference(stale_pure_blames)

    # Last resort, sift thru aliases
    if len(missing):
        with open(os.path.join(tree.path, '.mailmap'), 'r') as f:
            mmap_lines = f.readlines()
        mmap_lines += local_map

        mapped = set()
        for m in missing:
            for line in mmap_lines:
                if m in line:
                    mmap_emails = emailpat.findall(line)
                    if m not in mmap_emails:  # re-check the match with the real regex
                        continue
                    for have in included:
                        if have in mmap_emails:
                            mapped.add(m.lower())

        found.update(mapped)
        missing.difference_update(mapped)
        missing_blamed.difference_update(mapped)
        out.append(f"Mapped: {mapped}")

    out += ["=== Final ===",
            f"Missing: {missing}",
            f"Missing blames: {missing_blamed}"]
    out += ["", "=== stale log ==="] + stale_log
    out += ["", "=== get_maintainer ==="] + raw_gm

    if len(missing_blamed):
        return 1, f"{len(missing_blamed)} blamed authors not CCed: {' '.join(missing_blamed)}; " + \
                  f"{len(missing)} maintainers not CCed: {' '.join(missing)}", '\n'.join(out)
    if len(missing):
        ret = 250 if len(found) > 1 else 1
        return ret, f"{len(missing)} maintainers not CCed: {' '.join(missing)}", '\n'.join(out)
    return 0, f"CCed {len(found)} of {len(expected)} maintainers", '\n'.join(out)
