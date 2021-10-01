# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Series representation """
# TODO: document

import re

series_id_gen = 0


class Series(object):
    """Patch series class

    """
    def __init__(self, ident=None, title=""):
        self.cover_letter = None
        self.cover_pull = None
        self.patches = []
        self.title = title
        self.subject = ""

        global series_id_gen
        if ident is not None:
            self.id = ident
        else:
            series_id_gen += 1
            self.id = series_id_gen

    def set_cover_letter(self, data):
        self.cover_letter = data

        subj = re.search(r'Subject: \[.*\](.*)', data)
        if subj:
            if not self.title:
                self.title = subj.group(1).strip()
            self.subject = subj.group(0)[9:]

    def add_patch(self, patch):
        self.patches.append(patch)
