#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""CLI tool for submitting patches to AIR and monitoring review status"""

import argparse
import configparser
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional
import requests


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    RED = '\033[91m'
    GREEN = '\033[32m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'


def colorize(text: str, color: str) -> str:
    """Add color to text if output is a TTY

    Args:
        text: Text to colorize
        color: Color code

    Returns:
        Colored text if stdout is a TTY, otherwise plain text
    """
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text


def load_config() -> configparser.ConfigParser:
    """Load configuration from ~/.air.conf

    Returns:
        ConfigParser instance (may be empty if file doesn't exist)
    """
    config = configparser.ConfigParser()
    config_path = Path.home() / '.air.conf'

    if config_path.exists():
        try:
            config.read(config_path)
        except Exception as e:
            print(f"Warning: Failed to read config file {config_path}: {e}", file=sys.stderr)

    return config


def read_patch_files(patch_files: List[str]) -> List[str]:
    """Read patch content from files

    Args:
        patch_files: List of paths to patch files

    Returns:
        List of patch contents
    """
    patches = []
    for path in patch_files:
        try:
            with open(path, 'r') as f:
                patches.append(f.read())
        except FileNotFoundError:
            print(f"Error: Patch file not found: {path}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error reading patch file {path}: {e}", file=sys.stderr)
            sys.exit(1)
    return patches


def submit_review(url: str, token: str, tree: str, branch: Optional[str],
                  patches: Optional[List[str]] = None,
                  patchwork_series_id: Optional[int] = None,
                  chash: Optional[str] = None,
                  model: Optional[str] = None) -> str:
    """Submit patches for review

    Args:
        url: AIR service URL
        token: API token
        tree: Git tree name
        branch: Optional branch name
        patches: List of patch contents (or None if using patchwork/hash)
        patchwork_series_id: Patchwork series ID (or None if using patches/hash)
        chash: Git hash or range (or None if using patches/patchwork)
        model: Optional model name (sonnet, opus, haiku)

    Returns:
        Review ID
    """
    api_url = f"{url}/api/review"

    payload = {
        'token': token,
        'tree': tree,
    }

    if patches:
        payload['patches'] = patches
    elif patchwork_series_id:
        payload['patchwork_series_id'] = patchwork_series_id
    elif chash:
        payload['hash'] = chash
    else:
        print("Error: Either patches, patchwork_series_id, or hash must be provided", file=sys.stderr)
        sys.exit(1)

    if branch:
        payload['branch'] = branch

    if model:
        payload['model'] = model

    try:
        response = requests.post(api_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data['review_id']
    except requests.exceptions.RequestException as e:
        print(f"Error submitting review: {e}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)


def get_review_status(url: str, token: Optional[str], review_id: str,
                      fmt: Optional[str] = None) -> dict:
    """Get review status

    Args:
        url: AIR service URL
        token: API token (optional for public reviews)
        review_id: Review ID
        fmt: Optional format (json, markup, inline)

    Returns:
        Review status dictionary
    """
    api_url = f"{url}/api/review"
    params = {
        'id': review_id,
    }

    if token:
        params['token'] = token

    if fmt:
        params['format'] = fmt

    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error querying review status: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)


def format_status_line(status: dict) -> str:
    """Format one-line status summary with color

    Args:
        status: Review status dictionary

    Returns:
        Formatted status string with color codes
    """
    state = status['status']
    patch_count = status.get('patch_count', 0)
    completed = status.get('completed_patches', 0)

    if state == 'queued':
        queue_len = status.get('queue-len', '?')
        return f"Status: {colorize('queued', Colors.CYAN)} (patches ahead: {queue_len})"

    if state == 'in-progress':
        if patch_count <= 0:
            return f"Status: {colorize('in-progress', Colors.YELLOW)} (setting up...)"
        progress = f"{completed}/{patch_count}"
        return f"Status: {colorize('in-progress', Colors.YELLOW)} ({progress} patches completed)"

    if state == 'done':
        return f"Status: {colorize('done', Colors.GREEN + Colors.BOLD)} ({patch_count} patches reviewed)"

    if state == 'error':
        msg = status.get('message', 'unknown error')
        return f"Status: {colorize('error', Colors.RED + Colors.BOLD)} - {msg}"

    return f"Status: {state}"


def print_reviews(reviews: List[Optional[str]], patch_count: int):
    """Print review results

    Args:
        reviews: List of review contents (may contain None for failed patches)
        patch_count: Total number of patches
    """
    for i, review in enumerate(reviews, 1):
        separator = '='*80
        patch_header = f"PATCH {i}/{patch_count}"

        print(f"\n{separator}")
        print(colorize(patch_header, Colors.BOLD + Colors.BLUE), end='')
        if review is None:
            print(" - " + colorize("No review comments", Colors.GREEN))
        else:
            print()
        print(separator)

        if review:
            print(review)


def main():
    # Load config file first to get defaults
    config = load_config()

    parser = argparse.ArgumentParser(
        description='Submit patches to AIR for review or check existing review status',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Submit patch files
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next 0001-fix.patch 0002-feat.patch

  # Submit git hash/range
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next --hash abc123def..456789abc

  # Submit patchwork series
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next --pw-series 1026553

  # Check existing review
  %(prog)s --url https://example.com/air --token mytoken --review-id abc-123-def

  # Check public review (no token needed)
  %(prog)s --url https://example.com/air --review-id abc-123-def

  # Submit and exit without waiting / polling for it to finish
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next --no-wait 0001-fix.patch

  # Fetch full Claude output (by default we only print regression report)
  %(prog)s --url https://example.com/air --token mytoken --review-id abc-123-def --no-wait --format markup

Configuration file:
  You can create ~/.air.conf to avoid repeating common parameters:

    [air]
    url = https://example.com/air
    token = mytoken
    tree = netdev/net-next
    branch = main
    model = sonnet

  Command-line arguments always override config file values.
  To unset a config value, pass an empty string:
    %(prog)s --token= --review-id abc-123-def  # Query without token
    %(prog)s --branch= 0001-fix.patch          # Submit without branch
    %(prog)s --model= 0001-fix.patch           # Use default model
        """
    )

    parser.add_argument('--url',
                       help='AIR service URL (e.g., https://example.com/air)')
    parser.add_argument('--token',
                       help='API authentication token (required for submission, optional for public reviews)')
    parser.add_argument('--tree',
                       help='Git tree name (e.g., netdev/net-next) [required for submission]')
    parser.add_argument('--branch',
                       help='Git branch name (optional)')
    parser.add_argument('--model',
                       help='Claude model to use (e.g., sonnet, opus, haiku) [optional]')
    parser.add_argument('--format', choices=['json', 'markup', 'inline'],
                       default='inline',
                       help='Review output format (default: inline)')
    parser.add_argument('--poll-interval', type=int, default=5,
                       help='Status polling interval in seconds (default: 5)')
    parser.add_argument('--no-wait', action='store_true',
                       help='Do not wait for review completion (submit or check once and exit)')
    parser.add_argument('--pw-series', type=int, metavar='SERIES_ID',
                       help='Patchwork series ID to review')
    parser.add_argument('--hash', metavar='HASH',
                       help='Git commit hash or range (e.g., abc123 or abc123..def456)')
    parser.add_argument('--review-id', metavar='ID',
                       help='Existing review ID to check (skip submission)')
    parser.add_argument('patches', nargs='*', metavar='PATCH_FILE',
                       help='Patch files to submit')

    args = parser.parse_args()

    # Fill in missing arguments from config file
    # Use 'is None' to allow empty string ("") to explicitly unset a config value
    if config.has_section('air'):
        if args.url is None and config.has_option('air', 'url'):
            args.url = config.get('air', 'url')
        if args.token is None and config.has_option('air', 'token'):
            args.token = config.get('air', 'token')
        if args.tree is None and config.has_option('air', 'tree'):
            args.tree = config.get('air', 'tree')
        if args.branch is None and config.has_option('air', 'branch'):
            args.branch = config.get('air', 'branch')
        if args.model is None and config.has_option('air', 'model'):
            args.model = config.get('air', 'model')

    # Convert empty strings to None (allows unsetting config values)
    if args.token == '':
        args.token = None
    if args.branch == '':
        args.branch = None
    if args.model == '':
        args.model = None

    # Validate that we have URL
    if not args.url:
        parser.error('--url is required (either via command-line or ~/.air.conf)')

    args.url = args.url.rstrip('/')

    # Validate arguments
    if args.review_id:
        review_id = args.review_id
    else:
        if not args.token:
            parser.error('--token is required when submitting new review')
        if not args.tree:
            parser.error('--tree is required when submitting new review')

        # Count how many input methods are specified
        input_count = sum([
            bool(args.pw_series),
            bool(args.patches),
            bool(args.hash)
        ])

        if input_count > 1:
            parser.error('Cannot specify more than one of: --pw-series, --hash, or patch files')
        if input_count == 0:
            parser.error('Must specify one of: --pw-series, --hash, or patch files')

        if args.pw_series:
            print(f"Submitting patchwork series {args.pw_series} to {args.tree}...")
            review_id = submit_review(args.url, args.token, args.tree,
                                      args.branch,
                                      patchwork_series_id=args.pw_series,
                                      model=args.model)
        elif args.hash:
            print(f"Submitting git hash/range {args.hash} to {args.tree}...")
            review_id = submit_review(args.url, args.token, args.tree,
                                      args.branch, chash=args.hash,
                                      model=args.model)
        else:
            print(f"Reading {len(args.patches)} patch file(s)...")
            patches = read_patch_files(args.patches)
            print(f"Submitting to {args.tree}...")
            review_id = submit_review(args.url, args.token, args.tree,
                                      args.branch, patches=patches,
                                      model=args.model)

        print(f"Review ID: {review_id}")

        # Print link to review UI
        ui_url = f"{args.url}/ai-review.html?id={review_id}&token={args.token}"
        print(f"Review URL: {colorize(ui_url, Colors.CYAN)}")

        if args.no_wait:
            print("Submission complete (--no-wait specified)")
            return

        print(f"Monitoring status (polling every {args.poll_interval}s)...\n")

    # Poll until complete
    last_line_len = 0
    try:
        while not args.no_wait:
            status = get_review_status(args.url, args.token, review_id)

            # Format and print status line (overwriting previous)
            status_line = format_status_line(status)
            # Clear previous line and print new status
            print(f"\r{' ' * last_line_len}\r{status_line}", end='',
                  flush=True)
            last_line_len = len(status_line)

            # Check if done
            if status['status'] in ('done', 'error'):
                print()  # New line after final status
                break

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print(f"\n\nInterrupted by user. Review ID: {review_id}")
        sys.exit(1)

    # Fetch final results
    print("\nFetching review results...")
    final_status = get_review_status(args.url, args.token, review_id,
                                     fmt=args.format)

    reviews = final_status.get('review', [])
    patch_count = final_status.get('patch_count', 0)

    if not reviews:
        print("No reviews available")
        if final_status['status'] == 'error':
            msg = final_status.get('message', 'Unknown error')
            print(f"Error: {msg}")
            sys.exit(1)
    else:
        print_reviews(reviews, patch_count)

    # Exit with error if review failed
    if final_status['status'] == 'error':
        sys.exit(1)


if __name__ == '__main__':
    main()
