# SPDX-License-Identifier: GPL-2.0

import json
import os
import subprocess
import traceback
from typing import Tuple

LOCAL_DIR = os.path.dirname(__file__)

LOCAL_CONF = None
LOCAL_CONF_MTIME = None


def _write_output(output_dir, cmd, exit_code=None,
                  stdout=None, stderr=None, exception=None):
    """Write sanitized command output to the outputs file."""
    # Extract the token before building output
    token_idx = cmd.index("--token") + 1
    token = cmd[token_idx]

    # Build the output string
    output = f"Command: {' '.join(cmd)}\n"
    if exit_code is not None:
        output += f"Exit code: {exit_code}\n"
        output += f"\n--- STDOUT ---\n{stdout}"
        output += f"\n--- STDERR ---\n{stderr}"
    if exception is not None:
        output += f"Exception: {exception}\n"

    # Sanitize and write
    output = output.replace(token, "$TOKEN")
    stdout_path = os.path.join(output_dir, "outputs")
    with open(stdout_path, "w", encoding="utf-8") as f:
        f.write(output)


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
            timeout=20,
            check=False
        )

        _write_output(series_output_dir, cmd, exit_code=result.returncode,
                      stdout=result.stdout, stderr=result.stderr)

        if result.returncode != 0:
            return 250, "Submit tool error"

        return 111, "Submitted for review"

    except subprocess.TimeoutExpired:
        return 250, "Command timed out after 20 seconds"
    except Exception as e:
        _write_output(series_output_dir, cmd, exception=str(e))
        return 250, "Submit tool exception"
