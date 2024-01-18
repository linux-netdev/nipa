# SPDX-License-Identifier: GPL-2.0

import configparser


class CbArg:
    def __init__(self, config_paths):
        self._config_paths = config_paths

        self.config = None

        self.refresh_config()

    def refresh_config(self):
        self.config = configparser.ConfigParser()
        self.config.read(self._config_paths)
