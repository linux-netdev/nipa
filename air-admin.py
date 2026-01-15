#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""CLI tool for administrative operations on AIR reviews"""

import argparse
import configparser
import json
import sys
from pathlib import Path
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


def delete_review(url: str, token: str, review_id: str) -> bool:
    """Delete a review (superuser only)

    Args:
        url: AIR service URL
        token: API token (must be superuser)
        review_id: Review ID to delete

    Returns:
        True if successful
    """
    api_url = f"{url}/api/review"
    params = {
        'id': review_id,
        'token': token,
    }

    try:
        response = requests.delete(api_url, params=params, timeout=30)
        response.raise_for_status()
        return response.json().get('success', False)
    except requests.exceptions.RequestException as e:
        print(f"Error deleting review: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)


def create_token(url: str, token: str, name: str) -> str:
    """Create a new token (superuser only)

    Args:
        url: AIR service URL
        token: API token (must be superuser)
        name: Human-readable name for the new token

    Returns:
        The newly created token string
    """
    api_url = f"{url}/api/token"
    payload = {
        'token': token,
        'name': name,
    }

    try:
        response = requests.post(api_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('token')
    except requests.exceptions.RequestException as e:
        print(f"Error creating token: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    # Load config file first to get defaults
    config = load_config()

    parser = argparse.ArgumentParser(
        description='Administrative operations for AIR reviews',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Delete a review
  %(prog)s --url https://example.com/air --token mytoken --delete abc-123-def

  # Create a new token
  %(prog)s --url https://example.com/air --token mytoken --create-token "User Name"

Configuration file:
  You can create ~/.air.conf to avoid repeating common parameters:

    [air]
    url = https://example.com/air
    token = mytoken

  Command-line arguments always override config file values.
        """
    )

    parser.add_argument('--url',
                       help='AIR service URL (e.g., https://example.com/air)')
    parser.add_argument('--token',
                       help='API authentication token (required for admin operations)')
    parser.add_argument('--delete', metavar='REVIEW_ID',
                       help='Delete the specified review (requires superuser token)')
    parser.add_argument('--create-token', metavar='NAME',
                       help='Create a new token with the given name (requires superuser token)')

    args = parser.parse_args()

    # Fill in missing arguments from config file
    if config.has_section('air'):
        if args.url is None and config.has_option('air', 'url'):
            args.url = config.get('air', 'url')
        if args.token is None and config.has_option('air', 'token'):
            args.token = config.get('air', 'token')

    # Convert empty strings to None (allows unsetting config values)
    if args.token == '':
        args.token = None

    # Validate that we have URL
    if not args.url:
        parser.error('--url is required (either via command-line or ~/.air.conf)')

    args.url = args.url.rstrip('/')

    # Check that at least one operation is specified
    if not args.delete and not getattr(args, 'create_token', None):
        parser.error('No operation specified. Use --delete REVIEW_ID or --create-token NAME')

    # Handle --delete operation
    if args.delete:
        if not args.token:
            parser.error('--delete requires --token (must be superuser)')

        review_id = args.delete
        print(f"Deleting review {review_id}...")
        success = delete_review(args.url, args.token, review_id)
        if success:
            print(colorize(f"Review {review_id} deleted successfully", Colors.GREEN))
        else:
            print(colorize("Failed to delete review", Colors.RED), file=sys.stderr)
            sys.exit(1)
        return

    # Handle --create-token operation
    if getattr(args, 'create_token', None):
        if not args.token:
            parser.error('--create-token requires --token (must be superuser)')

        name = args.create_token
        print(f"Creating token for '{name}'...")
        new_token = create_token(args.url, args.token, name)
        if new_token:
            print(colorize("Token created successfully", Colors.GREEN))
            print(f"Name: {name}")
            print(f"Token: {colorize(new_token, Colors.CYAN)}")
        else:
            print(colorize("Failed to create token", Colors.RED), file=sys.stderr)
            sys.exit(1)
        return


if __name__ == '__main__':
    main()
