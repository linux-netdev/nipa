# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

import re

from core import Series
from core import Patch
from core import log, log_open_sec, log_end_sec

# TODO: document


class PwSeries(Series):
    def __init__(self, pw, pw_series):
        super().__init__(ident=pw_series['id'])

        self.pw = pw
        self.pw_series = pw_series

        if pw_series['cover_letter']:
            pw_cover_letter = pw.get_mbox('cover', pw_series['cover_letter']['id'])
            self.set_cover_letter(pw_cover_letter)
        elif self.pw_series['patches']:
            self.subject = self.pw_series['patches'][0]['name']
            self.title = self.pw_series['patches'][0]['name']
        else:
            self.subject = ""
            self.title = ""

        # Add patches to series
        #
        # Fast path incomplete series
        if not pw_series['received_all']:
            for p in self.pw_series['patches']:
                raw_patch = pw.get_mbox('patch', p['id'])
                self.patches.append(Patch(raw_patch, p['id']))
            return

        # Do more magic around series which are complete
        # Patchwork 2.2.2 orders them by arrival time
        pids = []
        for p in self.pw_series['patches']:
            pids.append(p['id'])
        total = self.pw_series['total']
        if total == len(self.pw_series['patches']):
            for i in range(total):
                found = False
                name = self.pw_series['patches'][i]['name']
                pid = self.pw_series['patches'][i]['id']
                for j in range(total):
                    # scanning PW-parsed name - tags are separated by commas
                    if name.find(f" {j + 1}/{total}") >= 0 or \
                       name.find(f",{j + 1}/{total}") >= 0 or \
                       name.find(f"[{j + 1}/{total}") >= 0 or \
                       name.find(f"0{j + 1}/{total}") >= 0:
                        if pids[j] != pid:
                            log(f"Patch order - reordering {i} => {j + 1}")
                            pids[j] = pid
                        found = True
                        break
                if not found:
                    log("Patch order - not all patches were found!", "")
                    pids = []
                    for p in self.pw_series['patches']:
                        pids.append(p['id'])
                    break
        else:
            log("Patch order - count does not add up?!", "")

        for pid in pids:
            raw_patch = pw.get_mbox('patch', pid)
            self.patches.append(Patch(raw_patch, pid))

        if not pw_series['cover_letter'] and len(self.patches) > 1:
            self.fixup_pull_covers()

    def __getitem__(self, key):
        return self.pw_series[key]

    def fixup_pull_covers(self):
        # For pull requests posted as series patchwork treats the cover letter
        # as a patch so the cover is null. Try to figure that out but still
        # use first patch for prefix, pulls don't have dependable subjects.
        all_reply = None

        log_open_sec("Searching for implicit cover/pull request")
        for p in self.patches:
            lines = p.raw_patch.split('\n')
            r_in_reply = re.compile(r'^In-Reply-To: <(.*)>$')
            reply_to = None

            for line in lines:
                if line == "":   # end of headers
                    if reply_to is None:
                        log("Patch had no reply header", "")
                        all_reply = False
                    break
                match = r_in_reply.match(line)
                if not match:
                    continue

                reply_to = match.group(1)
                log("Patch reply header", reply_to)
                if all_reply is None:
                    all_reply = reply_to
                elif all_reply != reply_to:
                    all_reply = False
                    log("Mismatch in replies", "")
        log("Result", all_reply)
        if all_reply:
            covers = self.pw.get_all('patches', filters={'msgid': all_reply}, api='1.2')
            if len(covers) != 1:
                log('Unique cover letter not found', len(covers))
            else:
                cover = covers[0]
                if 'pull_url' in cover and cover['pull_url']:
                    self.cover_pull = cover
                    log('Attached pull cover', '')
                else:
                    log('Pull URL not present in cover', '')
        log_end_sec()
