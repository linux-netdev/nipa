#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

import argparse
import configparser

import requests
from core import log, log_end_sec, log_init, log_open_sec
from pw import Patchwork


def main():
    parser = argparse.ArgumentParser(
        description="AI Code Reviewer for Patchwork",
        usage="%(prog)s --series SERIES_ID [--output OUTPUT]\n\n"
        "Examples:\n"
        "  %(prog)s --series 1016234\n"
        "  %(prog)s --series 1016234 --output series.html",
    )
    parser.add_argument(
        "--series", "-s", required=True, type=int, help="Series ID to fetch"
    )
    parser.add_argument("--output", "-o", help="Output file (default: print to stdout)")

    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(["nipa.config", "pw.config", "review.config"])

    log_init(
        config.get("log", "type", fallback="stdout"),
        config.get("log", "file", fallback=None),
    )

    pw = Patchwork(config)

    series = pw.get("series", args.series)
    log_open_sec(f"Logging series {args.series} data")
    log("Series information", series)
    log_end_sec()


if __name__ == "__main__":
    main()
