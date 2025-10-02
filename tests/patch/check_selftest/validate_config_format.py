#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import sys


def extract_key(raw):
    k = raw.split("=")[0]
    k = k.strip()
    k = k.replace('_', '')
    return k


def check_one(a, b, line):
    _a = extract_key(a)
    _b = extract_key(b)

    if _a >= _b:
        return None

    return f"Lines {line}-{line+1} invalid order, {a} should be after {b}"


def validate_config(file_path):
    """Validate a Makefile for proper variable assignment format."""

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")

    all_errors = []

    prev = ""
    for i, line in enumerate(lines):
        # ignore comments
        if line.strip().startswith('#'):
            continue
        # ignore bad lines
        if "=" not in line:
            continue
        if not prev:
            prev = line
            continue

        err = check_one(line, prev, i)
        if err:
            all_errors.append(err)

        prev = line

    if all_errors:
        print(f"Validation errors in {file_path}:")
        for error in all_errors:
            print(error)
        return False

    print(f"✓ {file_path} is properly formatted")
    return True


def fix(file_path):
    """Fix the config file by sorting entries alphabetically."""

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")

    output = []

    while lines:
        idx = 0
        first = lines[0]
        for i, line in enumerate(lines):
            # ignore comments
            if line.strip().startswith('#'):
                continue
            # ignore bad lines
            if "=" not in line:
                continue

            err = check_one(line, first, i)
            if err:
                first = line
                idx = i
        output.append(first)
        lines.pop(idx)

    # Write the fixed content back to the file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output))

    print(f"✓ Fixed {file_path} - config entries sorted alphabetically")


def main():
    """Main entry point for the script."""
    if len(sys.argv) < 2:
        print("Usage: validate_config_format.py <config_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    if file_path == "--fix":
        file_path = sys.argv[2]

    code = 0
    if not validate_config(file_path):
        code = 1
        if sys.argv[1] == "--fix":
            fix(file_path)

    sys.exit(code)


if __name__ == "__main__":
    main()
