#!/usr/bin/env python3
"""
Script to validate Makefile variable assignment format.

Expected format:
- Variable assignment starts with "VARIABLE = \", "VARIABLE := \", or
  "VARIABLE += \" (with optional space)
- Each item on its own line, indented with a tab
- Each line ends with " \" (except the last item line)
- Items are sorted alphabetically
- Last line is a comment starting with "#" allowing the previous line to end with "\"
- Variables should only be assigned once (no duplicate assignments)
"""

import re
import sys


def _extract_items(lines, line_nums):
    """Extract and validate items from the middle lines."""
    errors = []
    items = []

    # Skip last line if it's the terminating comment
    end = len(lines)
    if lines[-1].strip().startswith("#"):
        end = -1

    for i, line in enumerate(lines[:end]):
        line_num = line_nums[i]

        # Check indentation (should be a tab)
        if not line.startswith("\t"):
            errors.append(f"Line {line_num}: Should start with tab, got '{line[:1]}'")

        # Remove tab and trailing " \"
        item = line[1:]  # Remove tab
        if item.endswith(" \\"):
            item = item[:-2].strip()

        if ' ' in item and '$' not in item:
            errors.append(f"Line {line_num}: contains a splace, multiple values? '{item}'")

        items.append((item, line_num))

    return items, errors


def _directory_sort_key(item):
    """Generate sort key considering directory depth first, then alphabetical order."""
    directory_count = item.count("/")
    return (directory_count, item.lower())


def _validate_sorting(items):
    """Validate directory-aware alphabetical sorting of items."""
    errors = []

    # Filter out function calls (items starting with $) as they don't need sorting
    sortable_items = []
    for item, line_num in items:
        if not item.startswith("$"):
            sortable_items.append((item, line_num))

    # Only validate sorting among sortable items
    for i in range(len(sortable_items) - 1):
        current_item, current_line = sortable_items[i]
        next_item, next_line = sortable_items[i + 1]

        if current_item < next_item:
            continue

        current_key = _directory_sort_key(current_item)
        next_key = _directory_sort_key(next_item)

        if current_key > next_key:
            current_dirs = current_item.count("/")
            next_dirs = next_item.count("/")

            if current_dirs != next_dirs:
                errors.append(
                    f"Lines {current_line}-{next_line}: Items not in directory-aware order: "
                    f"'{current_item}' ({current_dirs} dirs) should come after "
                    f"'{next_item}' ({next_dirs} dirs)"
                )
            else:
                errors.append(
                    f"Lines {current_line}-{next_line}: Items not in alphabetical order: "
                    f"'{current_item}' should come after '{next_item}'"
                )
    return errors


def validate_variable_block(var_name, lines, line_nums):
    """Validate a single variable assignment block."""
    errors = []

    if not lines:
        return errors

    # Extract and validate items from the middle lines
    items, item_errors = _extract_items(lines, line_nums)
    errors.extend(item_errors)

    # Check last line starts with "#"
    if len(lines) > 1:
        if not lines[-1].strip().startswith("#"):
            errors.append(
                f"Line {line_nums[-1]}: Trailing comment should start with '#',"
                f" got '{lines[-1].strip()}'"
            )
        elif len(lines[-1].strip()) > 5 and var_name not in lines[-1]:
            errors.append(
                f"Line {line_nums[-1]}: Trailing comment should contain the "
                f"variable name ({var_name}), got '{lines[-1].strip()}'"
            )

    # Check alphabetical sorting
    if len(items) > 1:
        errors.extend(_validate_sorting(items))

    return errors


def check_multiple_blocks(var_name, lines, line_nums):
    """Check for multiple variable assignment blocks."""
    errors = []

    # Check for multiple blocks
    for i, line_no in enumerate(line_nums):
        if i == 0:
            continue
        if line_no != line_nums[i - 1] + 1:
            errors.append(f"Line {line_no}: Multiple variable assignment blocks, first block starts at line {line_nums[0]}")

    return errors


def _process_entry(variable_blocks, var_name, entry, line_num):
    """Process a single entry and update the variable_blocks dictionary."""
    if var_name not in variable_blocks:
        variable_blocks[var_name] = ([], [], )
    variable_blocks[var_name][0].append(entry)
    variable_blocks[var_name][1].append(line_num)


def parse_makefile(content):
    """Parse Makefile and extract variable assignment blocks."""
    lines = content.split("\n")
    variable_blocks = {}

    i = 0
    var_name = None
    while i < len(lines):
        # Look for variable assignment with backslash continuation (=, :=, +=)
        match = re.match(r"^([A-Z_][A-Z0-9_]*)\s*(:?=|\+=)(.*)$", lines[i])
        if match:
            var_name = match.group(1)
            entry = match.group(3).strip()
            if entry.startswith("$") and not entry.startswith("\\"):
                # Special entry, probably for a good reason. Ignore completely.
                var_name = None
            elif len(var_name) < 3 or "FLAGS" in var_name or 'LIBS' in var_name:
                # Special case for CFLAGS, which is often used for multiple values
                # and is not sorted alphabetically.
                var_name = None
            elif entry.strip() != "\\":
                _process_entry(variable_blocks, var_name, '\t' + entry, i + 1)
        elif var_name:
            _process_entry(variable_blocks, var_name, lines[i], i + 1)

        if var_name and not lines[i].endswith('\\'):
            var_name = None
        i += 1

    return variable_blocks


def validate_makefile(file_path):
    """Validate a Makefile for proper variable assignment format."""

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    variable_blocks = parse_makefile(content)

    if not variable_blocks:
        print(f"No multi-line variable assignments found in {file_path}")
        return True

    all_errors = []

    # Validate each variable block
    for var_name, (block_lines, line_nums) in variable_blocks.items():
        errors  = validate_variable_block(var_name, block_lines, line_nums)
        errors += check_multiple_blocks(var_name, block_lines, line_nums)
        if errors:
            all_errors.extend(
                [f"Variable {var_name}:"] + [f"  {error}" for error in errors]
            )

    if all_errors:
        print(f"Validation errors in {file_path}:")
        for error in all_errors:
            print(error)
        return False

    print(f"✓ {file_path} is properly formatted")
    return True


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 2:
        print("Usage: validate_makefile_format.py <makefile_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    if not validate_makefile(file_path):
        sys.exit(1)


if __name__ == "__main__":
    main()
