#!/usr/bin/env python3
"""
Collect system state info. Save it to a JSON file,
if file already exists, compare it first and report deltas.
"""

import json
import os
import subprocess
import sys


def run_cmd_text(cmd):
    """Execute a shell command and return its output as text."""
    result = subprocess.run(cmd, shell=True, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    return result.stdout


def run_cmd_json(cmd):
    """Execute a shell command and return its output parsed as JSON."""
    result = subprocess.run(cmd, shell=True, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    if result.returncode != 0:
        return {"error": result.stderr.strip()}

    ret = json.loads(result.stdout)
    # "decapsulate" the one element arrays that ip and ethtool like return
    if isinstance(ret, list) and len(ret) == 1:
        ret = ret[0]
    return ret


def collect_system_state():
    """Collect network interface information."""
    state = {
        "links": {},
        "chans": {},
        "feat": {},
        "rings": {},
        "rss": {},
        "ntuple": {},
    }

    interfaces = run_cmd_json("ip -j -d link show")

    for iface in interfaces:
        ifname = iface['ifname']

        state["links"][ifname] = iface

        state["chans"][ifname] = run_cmd_json(f"ethtool -j -l {ifname}")
        state["feat" ][ifname] = run_cmd_json(f"ethtool -j -k {ifname}")
        state["rings"][ifname] = run_cmd_json(f"ethtool -j -g {ifname}")
        state["rss"  ][ifname] = run_cmd_json(f"ethtool -j -x {ifname}")
        if "rss-hash-key" in state["rss"][ifname]:
            del state["rss"][ifname]["rss-hash-key"]
        state["ntuple"][ifname] = run_cmd_text(f"ethtool -n {ifname}")

    return state


def compare_states(current, saved, path=""):
    """Compare current system state with saved state."""

    ret = 0

    if isinstance(current, dict) and isinstance(saved, dict):
        for k in current.keys() | saved.keys():
            if k in current and k in saved:
                ret |= compare_states(current[k], saved[k], path=f"{path}.{k}")
            else:
                print(f"Saved {path}.{k}:", saved.get(k))
                print(f"Current {path}.{k}:", current.get(k))
                ret = 1
    else:
        if current != saved:
            print(f"Saved {path}:", saved)
            print(f"Current {path}:", current)
            ret = 1

    return ret


def main():
    """Main function to collect and compare network interface states."""
    output_file = "/tmp/nipa-env-state.json"
    if len(sys.argv) > 1:
        output_file = sys.argv[1]

    # Collect current system state
    current_state = collect_system_state()
    exit_code = 0

    # Check if the file already exists
    if os.path.exists(output_file):
        print("Comparing to existing state file: ", end="")
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                saved_state = json.load(f)

            # Compare states
            exit_code = compare_states(current_state, saved_state)
            if exit_code == 0:
                print("no differences detected.")
        except (json.JSONDecodeError, IOError, OSError) as e:
            print("Error loading or comparing:")
            print(e)
    # Save current state to file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(current_state, f, indent=2)
    print(f"Current system state saved to {output_file}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
