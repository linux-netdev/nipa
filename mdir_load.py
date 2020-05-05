#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

import configparser
import glob
import os
import tempfile
from typing import List
import shutil

from core import NIPA_DIR


def series_worker_done(series_dir, worker, summary=None, status=0):
    if summary is None:
        summary = worker + " finished with status " + str(status)

    worker_dir = os.path.join(series_dir, 'all', worker)

    summary_file = os.path.join(worker_dir, "summary")
    status_file = os.path.join(worker_dir, "status")
    done_file = os.path.join(worker_dir, "done")

    with open(summary_file, 'w') as fp:
        fp.write(summary)
    with open(status_file, 'w') as fp:
        fp.write(str(status))

    os.mknod(done_file)


def write_raw(directory: str, filename: str) -> None:
    raw = os.path.join(directory, "raw")
    with open(filename, 'rb') as rfp:
        with open(raw, 'wb') as wfp:
            wfp.write(rfp.read())


def write_out(result_dir: str, series_name: str,
              cover_letter: str, patches: List[str]) -> None:
    series_dir = os.path.join(result_dir, series_name)

    all_dir = os.path.join(series_dir, "all")
    load_dir = os.path.join(all_dir, "load")
    done_file = os.path.join(load_dir, 'done')

    if os.path.exists(series_dir):
        if os.path.exists(done_file):
            print("Name collision, try again")
            return

        shutil.rmtree(series_dir)
    os.mkdir(series_dir)
    os.mkdir(all_dir)
    os.mkdir(load_dir)

    if cover_letter:
        cover_dir = os.path.join(series_dir, "cover")
        os.mkdir(cover_dir)
        write_raw(cover_dir, cover_letter)

    patches_dir = os.path.join(series_dir, "patches")
    os.mkdir(patches_dir)
    for patch_id, patch in enumerate(patches):
        patch_dir = os.path.join(patches_dir, str(patch_id))
        os.mkdir(patch_dir)
        write_raw(patch_dir, patch)

    series_worker_done(series_dir, "load")


def get_series_name() -> str:
    name = tempfile.NamedTemporaryFile().name[8:]
    return "mdir_" + name


def usage(exit_code: int) -> None:
    print(f"Usage: {os.sys.argv[0]} <patch dir>")
    os.sys.exit(exit_code)


def mdir_load() -> None:
    if len(os.sys.argv) < 2:
        usage(1)
    if os.sys.argv[1] == "-h" or os.sys.argv[1] == "--help":
        usage(0)

    config = configparser.ConfigParser()
    config.read(['nipa.config', 'mdir.config'])

    result_dir = config.get('results', 'dir', fallback=os.path.join(NIPA_DIR, "results"))
    if not os.path.isdir(result_dir):
        os.mkdir(result_dir)

    patch_dir = os.sys.argv[1]
    patches = glob.glob(f"{patch_dir}/0*.patch")
    patches.sort()

    cover_letter = None
    cover_letter_name = os.path.join(patch_dir, "0000-cover-letter.patch")
    if cover_letter_name in patches:
        patches.remove(cover_letter_name)
        cover_letter = cover_letter_name

    name = get_series_name()
    write_out(result_dir, name, cover_letter, patches)

    msg = f"{len(patches)} patches"
    if cover_letter:
        msg += " and cover letter"
    msg += f" loaded into '{name}' under {result_dir}"
    print(msg)


if __name__ == '__main__':
    mdir_load()
