# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Patch representation """
# TODO: document

import re

import core

patch_id_gen = 0


class Patch(object):
    """Patch class

    Class representing a patch with references to postings etc.

    Attributes
    ----------
    raw_patch : str
        The entire patch as a string, including commit message, diff, etc.
    title : str
        The Subject line/first line of the commit message of the patch.

    Methods
    -------
    write_out(fp)
        Write the raw patch into the given file pointer.
    """
    def __init__(self, raw_patch, ident=None, title=""):
        self.raw_patch = raw_patch
        self.title = title
        self.subject = ""

        subj = re.search(r'Subject: \[.*\](.*)', raw_patch)
        if not subj:
            subj = re.search(r'Subject: (.*)', raw_patch)
        if subj:
            if not self.title:
                self.title = subj.group(1).strip()
            self.subject = subj.group(0)[9:]
        core.log_open_sec("Patch init: " + self.title)
        core.log_end_sec()

        global patch_id_gen
        if ident is not None:
            self.id = ident
        else:
            patch_id_gen += 1
            self.id = patch_id_gen

    def write_out(self, fp):
        fp.write(self.raw_patch.encode('utf-8'))
        fp.flush()
