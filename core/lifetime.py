# SPDX-License-Identifier: GPL-2.0

import subprocess
import signal
import sys
import time
import os


sig_initialized = False
got_sigusr1 = False


def sig_handler(signum, frame) -> None:
    global got_sigusr1

    got_sigusr1 |= signum == signal.SIGUSR1
    print('signal received, SIGUSR1:', got_sigusr1)


def sig_init():
    global sig_initialized

    if not sig_initialized:
        signal.signal(signal.SIGUSR1, sig_handler)


def nipa_git_version():
    cwd = os.path.dirname(os.path.abspath(__file__))
    res = subprocess.run(["git", "show", "HEAD", "--format=quote", "--no-patch"],
                         capture_output=True, cwd=cwd, check=True)
    return res.stdout.decode("utf-8").strip()


class NipaLifetime:
    def __init__(self, config):
        self.config = config

        # Load exit criteria
        self.use_usrsig = config.getboolean('life', 'sigusr1', fallback=True)
        if self.use_usrsig:
            sig_init()
        self._nipa_version = nipa_git_version()
        self.use_nipa_version = config.getboolean('life', 'nipa_version', fallback=True)
        if self.use_nipa_version:
            self._nipa_version = nipa_git_version()

        print("NIPA version:", self._nipa_version)

        # Load params
        self._sleep = config.getint('life', 'poll_ival', fallback=60)
        self._single_shot = config.getboolean('life', 'single_shot', fallback=False)
        # Set initial state
        self._first_run = True
        self._restart = False

    def next_poll(self):
        global got_sigusr1

        if self._first_run:
            self._first_run = False
            return True
        elif self._single_shot:
            return False

        if self.use_nipa_version and nipa_git_version() != self._nipa_version:
            self._restart = True

        to_sleep = self._sleep
        while not self._restart and to_sleep > 0:
            if self.use_usrsig and got_sigusr1:
                self._restart = True
                break
            try:
                time.sleep(min(to_sleep, 1))
            except KeyboardInterrupt:
                return False
            to_sleep -= 1

        return not self._restart

    def exit(self):
        if self._restart:
            print("NIPA restarting!")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        print("NIPA quitting!")
