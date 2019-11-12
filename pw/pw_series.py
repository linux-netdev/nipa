# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

from core import Series
from core import Patch

# TODO: document


class PwSeries(Series):
    def __init__(self, pw, pw_series):
        super().__init__(ident=pw_series['id'])

        self.pw = pw
        self.pw_series = pw_series

        if pw_series['cover_letter']:
            pw_cover_letter = pw.get_mbox('cover',
                                          pw_series['cover_letter']['id'])
            self.set_cover_letter(pw_cover_letter.text)
        elif self.pw_series['patches']:
            self.subject = self.pw_series['patches'][0]['name']
            self.title = self.pw_series['patches'][0]['name']
        else:
            self.subject = ""
            self.title = ""

        for p in self.pw_series['patches']:
            raw_patch = pw.get_mbox('patch', p['id'])
            self.patches.append(Patch(raw_patch.text, p['id']))

    def __getitem__(self, key):
        return self.pw_series[key]
