# SPDX-License-Identifier: GPL-2.0

import json
import os
import subprocess
import traceback
from typing import Tuple

LOCAL_DIR = os.path.dirname(__file__)

LOCAL_CONF = None
LOCAL_CONF_MTIME = None


def test_series(tree, thing, result_dir) -> Tuple[int, str]:
    # Read in the config, cache it between executions
    global LOCAL_CONF, LOCAL_CONF_MTIME

    if len(thing.patches) > 24:
        return 250, "Series too long, not submitting"

    config_path = os.path.join(LOCAL_DIR, "config.json")
    if not os.path.exists(config_path):
        return 250, "Config file not found"
    try:
        current_mtime = os.path.getmtime(config_path)
        if not LOCAL_CONF or LOCAL_CONF_MTIME != current_mtime:
            with open(config_path, "r", encoding="utf-8") as fp:
                new_config = json.load(fp)

                # We expect something like
                # {"args": ["cmd", "arg1", "arg2"], "trees": {"pfx": "name, "pfx": "name"} }
                # do some accesses to prove the format is okay
                for _ in new_config['args']:
                    pass
                if tree.pfx in new_config["trees"]:
                    pass

                LOCAL_CONF = new_config
                LOCAL_CONF_MTIME = current_mtime
    except Exception:
        return 250, "Error reading config", traceback.format_exc()

    if tree.pfx not in LOCAL_CONF["trees"].keys():
        return 0, f"Not submitting (tree: {tree.pfx})"

    # Config will use AI review CLI without arguments, insert them
    cmd = []
    for arg in LOCAL_CONF["args"]:
        cmd.append(arg)
        if arg == '--pw-series':
            cmd.append(str(thing.id))
        elif arg == '--tree':
            cmd.append(LOCAL_CONF["trees"][tree.pfx])

    # Execute command and capture output
    series_output_dir = os.path.join(result_dir, "ai-review")
    os.makedirs(series_output_dir, exist_ok=True)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )

        # Hide the secret
        cmd[cmd.index("--token") + 1] = "$TOKEN"

        # Write stdout/stderr to result_dir/series_id/outputs
        stdout_path = os.path.join(series_output_dir, "outputs")
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write(f"Exit code: {result.returncode}\n")
            f.write(f"\n--- STDOUT ---\n{result.stdout}")
            f.write(f"\n--- STDERR ---\n{result.stderr}")

        if result.returncode != 0:
            return 250, "Submit tool error"

        return 111, "Submitted for review"

    except subprocess.TimeoutExpired:
        return 250, "Command timed out after 30 seconds"
    except Exception as e:
        # Write error to stdout file
        stdout_path = os.path.join(series_output_dir, "outputs")
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write(f"Exception: {str(e)}\n")
        return 250, "Submit tool exception"
