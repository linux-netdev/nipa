# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

"""Logging helpers

Internally log files are implemented as objects, but external users
should call the logging functions which output to the global log file.
Global log file is configured by the config, and instantiated by core
at the start and destroyed at the end of the run.
"""

import atexit
import datetime
import lzma
import os
import pprint
from xml.sax.saxutils import escape as xml_escape

logger = None  # pylint: disable-msg=C0103

# TODO: document


class Logger:
    """Logger base class

    Base class for logger implementations, each looger implementation
    should implement a different output file format.

    Attributes
    ----------
    _log_file
        File to which formatted log is written.

    Methods
    -------
    fini()
        Close the log file.
    open_sec(header)
        Start a new log file section with given header.
    close_sec()
        Close the last log file section.
    log_data()
        Create a new log file section with given header and contents.
    """
    def __init__(self, path):
        self.printer = pprint.PrettyPrinter()
        self._path = path
        self._level = 0

        self._log_file = open(self._path, "w+")
        self._log_open()

    def fini(self):
        self._log_close()

    def open_sec(self, header):
        self._level += 1
        self._sec_start(self._escape(header))
        self._log_file.flush()

    def end_sec(self):
        self._sec_end()
        self._level -= 1
        self._log_file.flush()

        self._maybe_close()

    def log(self, header, data):
        self.open_sec(self._escape(header))

        if not isinstance(data, str):
            data = self.printer.pformat(data)

        self._log_data(self._escape(data))

        self.end_sec()
        self._log_file.flush()

    def _maybe_close(self):
        if self._level:
            return
        if os.stat(self._path).st_size < 4 * 1000 * 1000:
            return

        # close the old log off
        self._log_close()
        self._log_file.flush()
        self._log_file.close()
        self._log_file = None

        # copy the data in to a compressed file now
        name = self._path + '-' + datetime.datetime.now().isoformat() + '.xz'

        with open(self._path, "rb") as f:
            with lzma.open(name, "w") as zf:
                data = f.read()
                while data:
                    zf.write(data)
                    data = f.read()

        # truncate the main log by re-opening
        self._log_file = open(self._path, "w+")
        self._log_open()

    def _log_open(self):
        pass

    def _log_close(self):
        pass

    def _escape(self, data):
        pass

    def _sec_start(self, header):
        pass

    def _sec_end(self):
        pass

    def _log_data(self, data):
        pass


class XmlLogger(Logger):
    def _log_open(self):
        self._log_file.write('<?xml version="1.0" encoding="UTF-8" ?>\n')
        self._log_file.write("<log>\n")

    def _log_close(self):
        self._log_file.write("</log>")

    def _escape(self, data):
        return xml_escape(data)

    def _sec_start(self, header):
        self._log_file.write("<sec>\n")
        self._log_file.write(f"<header>{header}</header>\n")

    def _sec_end(self):
        self._log_file.write("</sec>\n")

    def _log_data(self, data):
        self._log_file.write(f"<data>{data}</data>\n")


class OrgLogger(Logger):
    def _log_open(self):
        self._log_file.write('# -*-Org-*-\n')
        self._nl = True

    def _log_close(self):
        self._nl_write()

    def _nl_write(self):
        if not self._nl:
            self._log_file.write("\n")

    def _escape(self, data):
        if not data:
            return data
        if data[0] == '*':
            data = ' ' + data
        return data.replace("\n*", "\n *")

    def _sec_start(self, header):
        self._nl_write()
        self._log_file.write("*" * self._level + " " + header + "\n")
        self._nl = True

    def _log_data(self, data):
        self._nl_write()
        self._log_file.write(data)
        if data:
            self._nl = data[:-1] == "\n"


def log_init(name, path):
    global logger

    if name.lower() == "org":
        logger = OrgLogger(path)
    elif name.lower() == "xml":
        logger = XmlLogger(path)
    else:
        raise Exception("Logger type unknown", name)

    atexit.register(log_fini)


def log_fini():
    global logger

    logger.fini()


def log_open_sec(header):
    global logger

    logger.open_sec(header)


def log_end_sec():
    global logger

    logger.end_sec()


def log(header, data=''):
    global logger

    logger.log(header, data)
