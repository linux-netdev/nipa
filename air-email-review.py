#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""Send AIR review results as email replies to original patches"""

import argparse
import configparser
import os
import re
import subprocess
import sys
import tempfile
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
    """Load configuration from ~/.air.conf"""
    config = configparser.ConfigParser()
    config_path = Path.home() / '.air.conf'
    if config_path.exists():
        try:
            config.read(config_path)
        except configparser.Error as e:
            print(f"Warning: Failed to read config file {config_path}: {e}",
                  file=sys.stderr)
    return config


class AirReplyClient:
    """Client for fetching AIR reviews and Patchwork data"""

    def __init__(self, air_url: str, patchwork_url: str,
                 user_agent: Optional[str] = None,
                 token: Optional[str] = None):
        self.air_url = air_url.rstrip('/')
        self.patchwork_url = patchwork_url.rstrip('/')
        self.token = token
        self.session = requests.Session()
        if user_agent:
            self.session.headers['User-Agent'] = user_agent

    def get_review(self, review_id: str, fmt: str = 'inline') -> Dict:
        """Fetch review from AIR service"""
        params = {'id': review_id, 'format': fmt}
        if self.token:
            params['token'] = self.token

        response = self.session.get(f"{self.air_url}/api/review",
                                    params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def set_feedback(self, review_id: str, feedback: str) -> bool:
        """Set feedback for a review"""
        data = {'id': review_id, 'feedback': feedback}
        if self.token:
            data['token'] = self.token

        response = self.session.post(f"{self.air_url}/api/review/feedback",
                                     json=data, timeout=30)
        response.raise_for_status()
        return response.json().get('success', False)

    def get_patchwork_series(self, series_id: int) -> Dict:
        """Fetch series info from Patchwork API"""
        response = self.session.get(
            f"{self.patchwork_url}/api/1.3/series/{series_id}/",
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    def get_patchwork_patch(self, patch_id: int) -> Dict:
        """Fetch patch info from Patchwork API"""
        response = self.session.get(
            f"{self.patchwork_url}/api/1.3/patches/{patch_id}/",
            timeout=30
        )
        response.raise_for_status()
        return response.json()


def extract_commit_subject(review_text: str) -> str:
    """Extract the commit subject from review text"""
    # Look for "commit <hash>\nAuthor: ...\n\n    <subject>"
    # or just the first non-empty line after commit header
    lines = review_text.split('\n')
    in_header = False
    for line in lines:
        if line.startswith('commit '):
            in_header = True
            continue
        if in_header:
            # Skip Author line
            if line.startswith('Author:'):
                continue
            # Skip empty lines after Author
            if not line.strip():
                continue
            # This should be the subject (possibly indented)
            subject = line.strip()
            if subject:
                return subject
    return "AI Review"


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


def format_email(review_text: str, patch_info: Dict, from_addr: str,
                 to_addrs: List[str], cc_addrs: List[str],
                 header: Optional[str] = None,
                 footer: Optional[str] = None,
                 pw_bot: Optional[str] = None) -> str:
    """Format review as an email message"""
    # Extract subject from the patch
    original_subject = patch_info.get('name', 'patch')

    # Create reply subject - standard email convention is Re: at the start
    if original_subject.lower().startswith('re:'):
        subject = original_subject
    else:
        subject = f"Re: {original_subject}"

    # Build email body
    body_lines = [
        "This is an AI-generated review of your patch. The human sending this",
        "email has considered the AI review valid, or at least pausible.",
    ]

    # Add optional header lines after the intro (interpret \n as newlines)
    if header:
        header_text = header.replace('\\n', '\n')
        for line in header_text.rstrip().split('\n'):
            body_lines.append(line)

    body_lines.extend([
        "---",
    ])
    # Strip commit/Author header lines before adding to email
    clean_review = strip_commit_header(review_text)
    body_lines.extend(clean_review.rstrip().split('\n'))

    # Add footer with standard email signature separator (interpret \n as newlines)
    if footer or pw_bot:
        body_lines.extend([
            "-- ",
        ])
    if pw_bot:
        body_lines.extend([
            f"pw-bot: {pw_bot}",
        ])
        if footer:
            body_lines.extend([
                "",
            ])
    if footer:
        footer_text = footer.replace('\\n', '\n')
        for line in footer_text.rstrip().split('\n'):
            body_lines.append(line)

    # Add pw-bot directive if specified

    body = '\n'.join(body_lines)

    # Build email headers
    email_headers = [f"From: {from_addr}"]
    if to_addrs:
        email_headers.append(f"To: {', '.join(to_addrs)}")
    if cc_addrs:
        email_headers.append(f"Cc: {', '.join(cc_addrs)}")
    email_headers.append(f"Subject: {subject}")

    # Format as email (git send-email compatible format)
    email = '\n'.join(email_headers) + '\n\n' + body + '\n'
    return email


def validate_git_send_email() -> bool:
    """Check if git send-email is available and configured"""
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
    """Send email using git send-email

    The email file should contain From, To, Cc, and Subject headers.
    """
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


def collect_review_data(client: AirReplyClient, review_id: str) -> Tuple[Dict, List[Dict]]:
    """Collect all required data before sending emails

    Returns:
        Tuple of (review_data, patch_info_list)

    Raises:
        ValueError if required data is missing
    """
    # Fetch the review
    print(f"Fetching review {review_id}...")
    review = client.get_review(review_id, fmt='inline')

    if review.get('status') != 'done':
        raise ValueError(f"Review status is '{review.get('status')}', expected 'done'")

    reviews = review.get('review', [])
    if not reviews:
        raise ValueError("No review content found")

    patchwork_series_id = review.get('patchwork_series_id')
    if not patchwork_series_id:
        raise ValueError("Review does not have a patchwork_series_id - "
                        "cannot determine In-Reply-To headers")

    # Fetch series info from Patchwork
    print(f"Fetching patchwork series {patchwork_series_id}...")
    series = client.get_patchwork_series(patchwork_series_id)

    patches = series.get('patches', [])
    if not patches:
        raise ValueError("No patches found in patchwork series")

    # Verify we have the same number of patches
    if len(reviews) != len(patches):
        print(f"Warning: Review has {len(reviews)} patches, "
              f"patchwork series has {len(patches)} patches",
              file=sys.stderr)

    # Fetch detailed info for each patch (to get message_id and recipients)
    patch_info_list = []
    for i, patch_ref in enumerate(patches):
        patch_id = patch_ref.get('id')
        if not patch_id:
            raise ValueError(f"Patch {i+1} missing ID in series data")

        print(f"Fetching patch {i+1}/{len(patches)} info...")
        patch_info = client.get_patchwork_patch(patch_id)

        message_id = patch_info.get('msgid')
        if not message_id:
            raise ValueError(f"Patch {i+1} missing message_id")

        patch_info_list.append(patch_info)

    return review, patch_info_list


def extract_recipients(patch_info_list: List[Dict]) -> Tuple[List[str], List[str]]:
    """Extract To and Cc addresses from patch info

    Returns:
        Tuple of (to_addrs, cc_addrs)
    """
    # Collect all unique recipients
    to_set = set()
    cc_set = set()

    for patch_info in patch_info_list:
        # The submitter should be in To
        submitter = patch_info.get('submitter', {})
        if submitter.get('email'):
            to_set.add(submitter['email'])

        # Headers may contain additional recipients
        headers = patch_info.get('headers', {})
        if isinstance(headers, dict):
            # Parse To header
            to_header = headers.get('To', '')
            for addr in parse_email_list(to_header):
                cc_set.add(addr)

            # Parse Cc header
            cc_header = headers.get('Cc', '')
            for addr in parse_email_list(cc_header):
                cc_set.add(addr)

    # Remove To addresses from Cc
    cc_set -= to_set

    return list(to_set), list(cc_set)


def parse_email_list(header: str) -> List[str]:
    """Parse a comma-separated email header into list of addresses"""
    if not header:
        return []

    addrs = []
    # Simple parsing - split by comma, extract email
    for part in header.split(','):
        part = part.strip()
        if not part:
            continue

        # Extract email from "Name <email>" format
        match = re.search(r'<([^>]+)>', part)
        if match:
            addrs.append(match.group(1))
        elif '@' in part:
            addrs.append(part)

    return addrs


def main():
    config = load_config()

    parser = argparse.ArgumentParser(
        description='Send AIR review results as email replies to patches',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to check what would be sent
  %(prog)s --review-id abc-123-def --dry-run

  # Send replies for a review
  %(prog)s --review-id abc-123-def

  # With custom endpoints and user agent
  %(prog)s --review-id abc-123-def --air-url https://example.com \\
           --patchwork-url https://patchwork.kernel.org --user-agent "mybot/1.0"

  # Add extra Cc recipients
  %(prog)s --review-id abc-123-def --cc extra@example.com

Configuration file:
  You can set defaults in ~/.air.conf:

    [air]
    url = https://netdev-ai.bots.linux.dev
    token = mytoken
    user_agent = air-reply/1.0

    [air-email-review]
    from = AI Reviewer <ai@example.com>
    header = Full review at: https://netdev-ai.bots.linux.dev/ai-review.html
    footer = This is an AI-generated review. Report issues at https://example.com

    [patchwork]
    url = https://patchwork.kernel.org

  Command-line arguments always override config file values.
  To unset a config value, pass an empty string:
    %(prog)s --from= --review-id abc-123-def  # Use git config for from
        """
    )

    parser.add_argument('--review-id', required=True,
                        help='AIR review ID to send replies for')
    parser.add_argument('--air-url',
                        help='AIR service URL (required, or set in config)')
    parser.add_argument('--patchwork-url',
                        help='Patchwork API URL (required, or set in config)')
    parser.add_argument('--token',
                        help='AIR API token (for accessing non-public reviews)')
    parser.add_argument('--user-agent',
                        help='HTTP User-Agent string (optional)')
    parser.add_argument('--from', dest='from_addr',
                        help='From address for emails (default: git config user.email)')
    parser.add_argument('--footer',
                        help='Footer text to add to emails (added after "-- " separator)')
    parser.add_argument('--header',
                        help='Header text to add after the intro line')
    parser.add_argument('--to', action='append', dest='extra_to', default=[],
                        help='Additional To addresses (can be repeated)')
    parser.add_argument('--cc', action='append', dest='extra_cc', default=[],
                        help='Additional Cc addresses (can be repeated)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be sent without actually sending')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')
    parser.add_argument('--show-email', action='store_true',
                        help='Show the full email content that would be sent')
    parser.add_argument('--only', type=int, action='append', dest='only_patches',
                        metavar='N', default=[],
                        help='Only send replies for specific patch numbers (1-based, can be repeated)')
    parser.add_argument('--pw-bot', dest='pw_bot', metavar='STRING',
                        help='Add "pw-bot: STRING" footer to the first review email')
    parser.add_argument('--no-feedback', dest='no_feedback', action='store_true',
                        help='Do not set feedback on the review (default: sets "emailed")')

    args = parser.parse_args()

    # Fill in missing arguments from config file
    # Use 'is None' to allow empty string ("") to explicitly unset a config value
    if config.has_section('air'):
        if args.air_url is None and config.has_option('air', 'url'):
            args.air_url = config.get('air', 'url')
        if args.token is None and config.has_option('air', 'token'):
            args.token = config.get('air', 'token')
        if args.user_agent is None and config.has_option('air', 'user_agent'):
            args.user_agent = config.get('air', 'user_agent')

    if config.has_section('air-email-review'):
        if args.from_addr is None and config.has_option('air-email-review', 'from'):
            args.from_addr = config.get('air-email-review', 'from')
        if args.footer is None and config.has_option('air-email-review', 'footer'):
            args.footer = config.get('air-email-review', 'footer')
        if args.header is None and config.has_option('air-email-review', 'header'):
            args.header = config.get('air-email-review', 'header')

    if config.has_section('patchwork'):
        if args.patchwork_url is None and config.has_option('patchwork', 'url'):
            args.patchwork_url = config.get('patchwork', 'url')

    # Convert empty strings to None (allows unsetting config values)
    if args.token == '':
        args.token = None
    if args.from_addr == '':
        args.from_addr = None
    if args.user_agent == '':
        args.user_agent = None
    if args.footer == '':
        args.footer = None
    if args.header == '':
        args.header = None

    # Check required settings
    if not args.air_url:
        print("Error: --air-url is required (or set 'url' in [air] section of ~/.air.conf)",
              file=sys.stderr)
        sys.exit(1)
    if not args.patchwork_url:
        print("Error: --patchwork-url is required (or set 'url' in [patchwork] section of ~/.air.conf)",
              file=sys.stderr)
        sys.exit(1)

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

    # Verify git send-email is available
    if not validate_git_send_email():
        print("Error: git send-email is not available or not configured",
              file=sys.stderr)
        sys.exit(1)

    # Create client
    client = AirReplyClient(args.air_url, args.patchwork_url,
                            args.user_agent, args.token)

    # Collect all required data before sending
    try:
        review, patch_info_list = collect_review_data(client, args.review_id)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    reviews = review.get('review', [])

    # Set feedback to "emailed" unless --no-feedback is set
    if args.no_feedback:
        print("Skipping feedback (--no-feedback)")
    else:
        print("Setting feedback to 'emailed'...")
        try:
            client.set_feedback(args.review_id, 'emailed')
            print(colorize("Feedback set: emailed", Colors.GREEN))
        except requests.exceptions.RequestException as e:
            print(f"Warning: Failed to set feedback: {e}", file=sys.stderr)

    # Extract recipients from patches
    to_addrs, cc_addrs = extract_recipients(patch_info_list)

    # Add extra recipients
    to_addrs.extend(args.extra_to)
    cc_addrs.extend(args.extra_cc)

    if not to_addrs:
        print("Error: No recipients found", file=sys.stderr)
        sys.exit(1)

    # Summary before sending
    print()
    print(colorize("=== Review Summary ===", Colors.BOLD))
    print(f"Review ID: {args.review_id}")
    print(f"Patchwork Series: {review.get('patchwork_series_id')}")
    print(f"Patches: {len(reviews)}")
    print(f"From: {args.from_addr}")
    print(f"To: {', '.join(to_addrs)}")
    if cc_addrs:
        print(f"Cc: {', '.join(cc_addrs)}")
    print()

    if args.dry_run:
        print(colorize("=== DRY RUN MODE ===", Colors.YELLOW + Colors.BOLD))
        print()

    # Process each patch review
    success_count = 0
    skip_count = 0
    fail_count = 0
    first_email_sent = False

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, review_text in enumerate(reviews):
            patch_num = i + 1

            # Check if this patch is in the --only list
            if args.only_patches and patch_num not in args.only_patches:
                continue

            # Check if we have patch info for this review
            if i >= len(patch_info_list):
                print(f"Warning: No patch info for review {patch_num}, skipping",
                      file=sys.stderr)
                skip_count += 1
                continue

            patch_info = patch_info_list[i]
            message_id = patch_info.get('msgid', '')

            # Skip empty reviews (None or empty string)
            if review_text is None or review_text.strip() == '':
                if args.verbose:
                    print(f"Patch {patch_num}: {colorize('SKIP', Colors.YELLOW)} (no comments)")
                skip_count += 1
                continue

            # Format the email (add pw-bot footer only to the first email)
            pw_bot_arg = args.pw_bot if not first_email_sent else None
            email_content = format_email(
                review_text, patch_info, args.from_addr, to_addrs, cc_addrs,
                args.header, args.footer, pw_bot_arg
            )
            first_email_sent = True

            # Write to temp file
            email_file = os.path.join(tmpdir, f"review-{patch_num:04d}.txt")
            with open(email_file, 'w', encoding='utf-8') as f:
                f.write(email_content)

            patch_name = patch_info.get('name', f'patch {patch_num}')[:60]
            print(f"Patch {patch_num}: {patch_name}")

            if args.verbose:
                print(f"  In-Reply-To: {message_id}")
                print(f"  File: {email_file}")

            if args.show_email:
                print(colorize("  --- Email Content ---", Colors.CYAN))
                for line in email_content.split('\n')[:50]:
                    print(f"  {line}")
                if len(email_content.split('\n')) > 50:
                    print(f"  ... ({len(email_content.split(chr(10))) - 50} more lines)")
                print(colorize("  --- End Email ---", Colors.CYAN))

            # Send (or dry-run)
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
