#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Send Sashiko review results as email replies to original patches"""

import argparse
import configparser
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests


class Colors:
    RESET = '\033[0m'
    RED = '\033[91m'
    GREEN = '\033[32m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'


def colorize(text: str, color: str) -> str:
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text


def load_config() -> configparser.ConfigParser:
    """Load configuration from ~/.sashiko.conf"""
    config = configparser.ConfigParser()
    config_path = Path.home() / '.sashiko.conf'
    if config_path.exists():
        try:
            config.read(config_path)
        except configparser.Error as e:
            print(f"Warning: Failed to read config file {config_path}: {e}",
                  file=sys.stderr)
    return config


class SashikoClient:
    """Client for fetching Sashiko patchset and review data"""

    def __init__(self, base_url: str, user_agent: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        if user_agent:
            self.session.headers['User-Agent'] = user_agent

    def get_patchset(self, patchset_id: str) -> Dict:
        """Fetch patchset details (patches, reviews, recipients)"""
        response = self.session.get(
            f"{self.base_url}/api/patch",
            params={'id': patchset_id},
            timeout=30
        )
        response.raise_for_status()
        return response.json()


def strip_commit_header(review_text: str) -> str:
    """Strip commit/Author header lines and subsequent empty lines from review text"""
    lines = review_text.split('\n')
    start_idx = 0

    # Skip "commit ..." line if present
    if lines and lines[start_idx].startswith('commit '):
        start_idx += 1

    # Skip "Author: ..." line if present
    if start_idx < len(lines) and lines[start_idx].startswith('Author:'):
        start_idx += 1

    # Skip any subsequent empty lines
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    return '\n'.join(lines[start_idx:])


def format_email(review_text: str, subject: str, from_addr: str,
                 to_addrs: List[str], cc_addrs: List[str],
                 header: Optional[str] = None,
                 footer: Optional[str] = None,
                 pw_bot: Optional[str] = None,
                 say: Optional[str] = None) -> str:
    """Format review as an email message"""
    # Create reply subject
    if subject.lower().startswith('re:'):
        reply_subject = subject
    else:
        reply_subject = f"Re: {subject}"

    # Build email body
    if say:
        say = f'says "{say}"'
    else:
        say = 'has considered the AI review valid, or at least plausible.'

    intro = f'This is an AI-generated review of your patch. The human sending this email {say}'
    body_lines = textwrap.wrap(intro, width=68)

    if header:
        header_text = header.replace('\\n', '\n')
        for line in header_text.rstrip().split('\n'):
            body_lines.append(line)

    body_lines.append("---")

    clean_review = strip_commit_header(review_text)
    body_lines.extend(clean_review.rstrip().split('\n'))

    if footer or pw_bot:
        body_lines.append("-- ")
    if pw_bot:
        body_lines.append(f"pw-bot: {pw_bot}")
        if footer:
            body_lines.append("")
    if footer:
        footer_text = footer.replace('\\n', '\n')
        for line in footer_text.rstrip().split('\n'):
            body_lines.append(line)

    body = '\n'.join(body_lines)

    email_headers = [f"From: {from_addr}"]
    if to_addrs:
        email_headers.append(f"To: {', '.join(to_addrs)}")
    if cc_addrs:
        email_headers.append(f"Cc: {', '.join(cc_addrs)}")
    email_headers.append(f"Subject: {reply_subject}")

    return '\n'.join(email_headers) + '\n\n' + body + '\n'


def validate_git_send_email() -> bool:
    """Check if git send-email is available"""
    try:
        subprocess.run(
            ['git', 'send-email', '--help'],
            capture_output=True, check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_git_config(key: str) -> Optional[str]:
    """Get a git config value"""
    try:
        result = subprocess.run(
            ['git', 'config', '--get', key],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def send_email(email_file: str, in_reply_to: str, dry_run: bool = False,
               verbose: bool = False) -> bool:
    """Send email using git send-email"""
    cmd = ['git', 'send-email', '--to', '']

    if dry_run:
        cmd.append('--dry-run')

    cmd.extend(['--in-reply-to', in_reply_to])
    cmd.append(email_file)

    if verbose:
        import shlex
        print(f"  Command: {' '.join(shlex.quote(arg) for arg in cmd)}")

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error sending email: {e.stderr}", file=sys.stderr)
        return False


def has_significant_findings(review: Dict) -> bool:
    """Check if a review has any Critical or High severity findings"""
    output = review.get('output', '')
    if not output:
        return False
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return False
    for finding in parsed.get('findings', []):
        severity = finding.get('severity', '')
        if severity in ('Critical', 'High'):
            return True
    return False


def parse_email_list(header: str) -> List[str]:
    """Parse a comma-separated email header into list of addresses"""
    if not header:
        return []

    addrs = []
    for part in header.split(','):
        part = part.strip()
        if not part:
            continue
        match = re.search(r'<([^>]+)>', part)
        if match:
            addrs.append(match.group(1))
        elif '@' in part:
            addrs.append(part)
    return addrs


def build_review_map(patchset: Dict) -> Dict[int, Dict]:
    """Build a map from patch_id to the latest review for that patch"""
    review_map = {}
    for review in patchset.get('reviews', []):
        patch_id = review.get('patch_id')
        if patch_id is None:
            continue
        # If multiple reviews for same patch, keep the latest (highest id)
        if patch_id not in review_map or review['id'] > review_map[patch_id]['id']:
            review_map[patch_id] = review
    return review_map


def main():
    config = load_config()

    parser = argparse.ArgumentParser(
        description='Send Sashiko review results as email replies to patches',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to check what would be sent
  %(prog)s --patchset-id 4762 --dry-run

  # Send replies with custom sashiko instance
  %(prog)s --patchset-id 4762 --sashiko-url https://my-sashiko.example.com

  # Show full email content
  %(prog)s --patchset-id 4762 --dry-run --show-email

  # Only send reply for patch 1 of a multi-patch series
  %(prog)s --patchset-id 4755 --only 1 --dry-run

Configuration file:
  You can set defaults in ~/.sashiko.conf:

    [sashiko]
    url = https://sashiko.dev
    user_agent = sashiko-email/1.0

    [sashiko-email-review]
    from = AI Reviewer <ai@example.com>
    header = Full review at: https://sashiko.dev
    footer = This is an AI-generated review.
    say = has reviewed the AI findings and agrees.

  Command-line arguments override config file values.
        """
    )

    parser.add_argument('--patchset-id', required=True,
                        help='Sashiko patchset ID (numeric) or message-id to send replies for')
    parser.add_argument('--sashiko-url', default=None,
                        help='Sashiko service URL (default: https://sashiko.dev)')
    parser.add_argument('--user-agent',
                        help='HTTP User-Agent string')
    parser.add_argument('--from', dest='from_addr',
                        help='From address (default: git config user.email)')
    parser.add_argument('--footer',
                        help='Footer text after "-- " separator')
    parser.add_argument('--header',
                        help='Header text after the intro line')
    parser.add_argument('--say',
                        help='Override the default "considered the AI review valid" text')
    parser.add_argument('--to', action='append', dest='extra_to', default=[],
                        help='Additional To addresses (can be repeated)')
    parser.add_argument('--cc', action='append', dest='extra_cc', default=[],
                        help='Additional Cc addresses (can be repeated)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be sent without actually sending')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')
    parser.add_argument('--show-email', action='store_true',
                        help='Show full email content')
    parser.add_argument('--only', type=int, action='append', dest='only_patches',
                        metavar='N', default=[],
                        help='Only send replies for specific patch numbers (1-based, can be repeated)')
    parser.add_argument('--pw-bot', dest='pw_bot', metavar='STRING',
                        help='Add "pw-bot: STRING" footer to the first review email')

    args = parser.parse_args()

    # Fill in from config
    if config.has_section('sashiko'):
        if args.sashiko_url is None and config.has_option('sashiko', 'url'):
            args.sashiko_url = config.get('sashiko', 'url')
        if args.user_agent is None and config.has_option('sashiko', 'user_agent'):
            args.user_agent = config.get('sashiko', 'user_agent')

    if config.has_section('sashiko-email-review'):
        sect = 'sashiko-email-review'
        if args.from_addr is None and config.has_option(sect, 'from'):
            args.from_addr = config.get(sect, 'from')
        if args.footer is None and config.has_option(sect, 'footer'):
            args.footer = config.get(sect, 'footer')
        if args.header is None and config.has_option(sect, 'header'):
            args.header = config.get(sect, 'header')
        if args.say is None and config.has_option(sect, 'say'):
            args.say = config.get(sect, 'say')

    # Defaults
    if not args.sashiko_url:
        args.sashiko_url = 'https://sashiko.dev'

    # Convert empty strings to None
    for attr in ('from_addr', 'user_agent', 'footer', 'header', 'say'):
        if getattr(args, attr) == '':
            setattr(args, attr, None)

    # Get From address from git config if not specified
    if not args.from_addr:
        user_name = get_git_config('user.name')
        user_email = get_git_config('user.email')
        if user_email:
            if user_name:
                args.from_addr = f"{user_name} <{user_email}>"
            else:
                args.from_addr = user_email
        else:
            print("Error: No From address specified and git user.email not configured",
                  file=sys.stderr)
            sys.exit(1)

    if not validate_git_send_email():
        print("Error: git send-email is not available or not configured",
              file=sys.stderr)
        sys.exit(1)

    # Fetch patchset
    client = SashikoClient(args.sashiko_url, args.user_agent)

    print(f"Fetching patchset {args.patchset_id} from {args.sashiko_url}...")
    try:
        patchset = client.get_patchset(args.patchset_id)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching patchset: {e}", file=sys.stderr)
        sys.exit(1)

    patches = patchset.get('patches', [])
    if not patches:
        print("Error: No patches found in patchset", file=sys.stderr)
        sys.exit(1)

    review_map = build_review_map(patchset)

    # Extract recipients: reply To the original author, Cc the original To and Cc
    author = patchset.get('author', '')
    to_addrs = parse_email_list(author)
    cc_addrs = parse_email_list(patchset.get('to', ''))
    cc_addrs.extend(parse_email_list(patchset.get('cc', '')))

    # Remove To addresses from Cc to avoid duplicates
    to_set = set(to_addrs)
    cc_addrs = [a for a in cc_addrs if a not in to_set]

    # Add extra recipients
    to_addrs.extend(args.extra_to)
    cc_addrs.extend(args.extra_cc)

    if not to_addrs:
        print("Error: No recipients found", file=sys.stderr)
        sys.exit(1)

    # Sort patches by part_index
    patches.sort(key=lambda p: p.get('part_index', 0))

    # Summary
    print()
    print(colorize("=== Review Summary ===", Colors.BOLD))
    print(f"Patchset ID: {args.patchset_id}")
    print(f"Subject: {patchset.get('subject', '(none)')}")
    print(f"Status: {patchset.get('status', '(unknown)')}")
    print(f"Patches: {len(patches)}")
    print(f"Reviews: {len(review_map)}")
    print(f"From: {args.from_addr}")
    print(f"To: {', '.join(to_addrs)}")
    if cc_addrs:
        print(f"Cc: {', '.join(cc_addrs)}")
    print()

    if args.dry_run:
        print(colorize("=== DRY RUN MODE ===", Colors.YELLOW + Colors.BOLD))
        print()

    # Process each patch
    success_count = 0
    skip_count = 0
    fail_count = 0
    first_email_sent = False

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, patch in enumerate(patches):
            patch_num = i + 1

            if args.only_patches and patch_num not in args.only_patches:
                continue

            patch_id = patch.get('id')
            message_id = patch.get('message_id', '')
            subject = patch.get('subject', f'patch {patch_num}')

            review = review_map.get(patch_id)
            if not review:
                if args.verbose:
                    print(f"Patch {patch_num}: {colorize('SKIP', Colors.YELLOW)} (no review)")
                skip_count += 1
                continue

            inline_review = review.get('inline_review', '')
            if not inline_review or inline_review.strip() == '':
                if args.verbose:
                    print(f"Patch {patch_num}: {colorize('SKIP', Colors.YELLOW)} (empty review)")
                skip_count += 1
                continue

            # Check if the review has any Critical or High severity findings
            if not has_significant_findings(review):
                if args.verbose:
                    print(f"Patch {patch_num}: {colorize('SKIP', Colors.YELLOW)} (no Critical/High findings)")
                skip_count += 1
                continue

            # Format the email (add pw-bot footer only to the first email)
            pw_bot_arg = args.pw_bot if not first_email_sent else None
            email_content = format_email(
                inline_review, subject, args.from_addr, to_addrs, cc_addrs,
                args.header, args.footer, pw_bot_arg, args.say
            )
            first_email_sent = True

            # Write to temp file
            email_file = os.path.join(tmpdir, f"review-{patch_num:04d}.txt")
            with open(email_file, 'w', encoding='utf-8') as f:
                f.write(email_content)

            print(f"Patch {patch_num}: {subject[:60]}")

            if args.verbose:
                print(f"  In-Reply-To: {message_id}")
                print(f"  Review ID: {review['id']}")

            if args.show_email:
                print(colorize("  --- Email Content ---", Colors.CYAN))
                lines = email_content.split('\n')
                for line in lines[:50]:
                    print(f"  {line}")
                if len(lines) > 50:
                    print(f"  ... ({len(lines) - 50} more lines)")
                print(colorize("  --- End Email ---", Colors.CYAN))

            if send_email(email_file, message_id, args.dry_run, args.verbose):
                if args.dry_run:
                    print(f"  {colorize('WOULD SEND', Colors.CYAN)}")
                else:
                    print(f"  {colorize('SENT', Colors.GREEN)}")
                success_count += 1
            else:
                print(f"  {colorize('FAILED', Colors.RED)}")
                fail_count += 1

    # Final summary
    print()
    print(colorize("=== Summary ===", Colors.BOLD))
    print(f"Sent: {success_count}, Skipped: {skip_count}, Failed: {fail_count}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
