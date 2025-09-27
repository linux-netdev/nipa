# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Patch representation """
# TODO: document

import re

import core


class Patch:
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

    PATCH_ID_GEN = 0

    def __init__(self, raw_patch, ident=None, title="", series=None):
        self.raw_patch = raw_patch
        self.title = title
        self.subject = ""
        self.series = series

        # Whether the patch is first in the series, set by series.add_patch()
        self.first_in_series = None

        subj = re.search(r'Subject: \[.*\](.*)', raw_patch)
        if not subj:
            subj = re.search(r'Subject: (.*)', raw_patch)
        if subj:
            if not self.title:
                self.title = subj.group(1).strip()
            self.subject = subj.group(0)[9:]
        core.log_open_sec("Patch init: " + self.title)
        core.log_end_sec()

        if ident is not None:
            self.id = ident
        else:
            Patch.PATCH_ID_GEN += 1
            self.id = Patch.PATCH_ID_GEN

    def write_out(self, fp):
        """ Write patch contents to a file """
        fp.write(self.raw_patch.encode('utf-8'))
        fp.flush()
