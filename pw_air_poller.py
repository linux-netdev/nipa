#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""Patchwork AIR Poller - Submit patches from Patchwork to AIR for review

Polls Patchwork for new series, submits them to AIR for AI review,
waits for completion, and posts check results back to Patchwork.

Features:
- Rate limiting: configurable patches in configurable window
- LIFO queue for series when rate limit exceeded
- Persistent state for rate limit tracking
"""

import argparse
import configparser
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, UTC
from typing import Dict, List, Optional, Tuple
import requests

from core import NIPA_DIR, log_init, log, log_open_sec, log_end_sec
from pw import Patchwork


class RateLimiter:
    """Track rate limiting with rolling window"""

    def __init__(self, max_patches: int, window_days: int):
        """Initialize rate limiter

        Args:
            max_patches: Maximum patches allowed in window
            window_days: Window size in days
        """
        self.max_patches = max_patches
        self.window_days = window_days
        # List of (timestamp, patch_count) tuples
        self.submissions: List[Tuple[datetime, int]] = []

    def trim_old(self):
        """Remove submissions older than window"""
        cutoff = datetime.now(UTC) - timedelta(days=self.window_days)
        self.submissions = [
            (ts, count) for ts, count in self.submissions
            if ts > cutoff
        ]

    def patches_in_window(self) -> int:
        """Get count of patches submitted in current window

        Returns:
            Total patches submitted in window
        """
        self.trim_old()
        return sum(count for _, count in self.submissions)

    def can_submit(self, patch_count: int) -> bool:
        """Check if we can submit more patches

        Args:
            patch_count: Number of patches to submit

        Returns:
            True if submission is allowed
        """
        current = self.patches_in_window()
        return current + patch_count <= self.max_patches

    def record_submission(self, patch_count: int):
        """Record a submission

        Args:
            patch_count: Number of patches submitted
        """
        self.submissions.append((datetime.now(UTC), patch_count))

    def to_dict(self) -> Dict:
        """Serialize to dict for persistence

        Returns:
            Dictionary representation
        """
        return {
            'submissions': [
                {'timestamp': ts.isoformat(), 'count': count}
                for ts, count in self.submissions
            ]
        }

    def from_dict(self, data: Dict):
        """Load from dict

        Args:
            data: Dictionary representation
        """
        self.submissions = []
        for entry in data.get('submissions', []):
            try:
                ts = datetime.fromisoformat(entry['timestamp'])
                count = entry['count']
                self.submissions.append((ts, count))
            except (KeyError, ValueError):
                continue
        self.trim_old()


class PwAirPoller:
    """Poll Patchwork for series and submit to AIR for review"""

    def __init__(self, config_path: str):
        """Initialize poller

        Args:
            config_path: Path to configuration file
        """
        self.config = configparser.ConfigParser()
        self.config.read([config_path, 'nipa.config', 'pw.config'])

        # AIR configuration
        self.air_url = self.config.get('air', 'url').rstrip('/')
        self.air_server = self.config.get('air', 'server', fallback=self.air_url).rstrip('/')
        self.air_token = self.config.get('air', 'token')
        self.air_tree = self.config.get('air', 'tree')
        self.air_branch = self.config.get('air', 'branch', fallback=None)

        # Patchwork configuration
        self.check_name = self.config.get('patchwork', 'check_name', fallback='ai-review')

        # Rate limiting configuration
        max_patches = self.config.getint('rate_limit', 'max_patches', fallback=30)
        window_days = self.config.getint('rate_limit', 'window_days', fallback=3)
        self.rate_limiter = RateLimiter(max_patches, window_days)

        # Polling configuration
        self.poll_interval = self.config.getint('poller', 'poll_interval', fallback=120)
        self.review_timeout = self.config.getint('poller', 'review_timeout', fallback=3600)
        self.review_poll_interval = self.config.getint('poller', 'review_poll_interval', fallback=10)
        self.state_file = self.config.get('poller', 'state_file',
                                          fallback='pw-air-poller.state')

        # Initialize logging
        log_dir = self.config.get('log', 'dir', fallback=NIPA_DIR)
        log_init(self.config.get('log', 'type', fallback='org'),
                 self.config.get('log', 'file', fallback=os.path.join(log_dir, 'pw-air-poller.org')),
                 force_single_thread=True)

        # Initialize Patchwork client
        self.patchwork = Patchwork(self.config)

        # State
        self.last_event_ts: Optional[str] = None
        self.queued_series: List[Dict] = []  # LIFO queue
        self.processed_series: set = set()  # Series IDs we've processed

        self.load_state()

    def load_state(self):
        """Load state from disk"""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

            self.last_event_ts = state.get('last_event_ts')
            self.queued_series = state.get('queued_series', [])
            self.processed_series = set(state.get('processed_series', []))

            if 'rate_limiter' in state:
                self.rate_limiter.from_dict(state['rate_limiter'])

            log(f"Loaded state: {len(self.queued_series)} queued, "
                f"{len(self.processed_series)} processed, "
                f"{self.rate_limiter.patches_in_window()} patches in window")
        except Exception as e:
            log(f"Error loading state: {e}")

    def save_state(self):
        """Save state to disk"""
        # Trim processed series to last 1000
        processed_list = list(self.processed_series)
        if len(processed_list) > 1000:
            processed_list = processed_list[-1000:]
            self.processed_series = set(processed_list)

        state = {
            'last_event_ts': self.last_event_ts,
            'queued_series': self.queued_series,
            'processed_series': processed_list,
            'rate_limiter': self.rate_limiter.to_dict(),
            'last_save': datetime.now(UTC).isoformat()
        }

        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log(f"Error saving state: {e}")

    def get_since_timestamp(self) -> str:
        """Get timestamp to poll from

        Returns:
            ISO format timestamp (last check or 3 days ago)
        """
        three_days_ago = datetime.utcnow() - timedelta(days=3)

        if self.last_event_ts:
            try:
                last_ts = datetime.fromisoformat(self.last_event_ts)
                if last_ts > three_days_ago:
                    return self.last_event_ts
            except ValueError:
                pass

        return three_days_ago.strftime('%Y-%m-%dT%H:%M:%S')

    def submit_to_air(self, series_id: int) -> Optional[str]:
        """Submit series to AIR for review

        Args:
            series_id: Patchwork series ID

        Returns:
            Review ID if successful, None otherwise
        """
        api_url = f"{self.air_url}/api/review"

        payload = {
            'token': self.air_token,
            'tree': self.air_tree,
            'patchwork_series_id': series_id,
        }

        if self.air_branch:
            payload['branch'] = self.air_branch

        try:
            response = requests.post(api_url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('review_id')
        except Exception as e:
            log(f"Error submitting series {series_id} to AIR: {e}")
            return None

    def wait_for_review(self, review_id: str) -> Optional[Dict]:
        """Wait for review to complete

        Args:
            review_id: AIR review ID

        Returns:
            Review data if successful, None otherwise
        """
        api_url = f"{self.air_url}/api/review"
        params = {
            'id': review_id,
            'token': self.air_token,
            'format': 'inline'
        }

        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > self.review_timeout:
                log(f"Review {review_id} timed out after {elapsed:.0f}s")
                return None

            try:
                response = requests.get(api_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                status = data.get('status')
                if status == 'done':
                    return data
                elif status == 'error':
                    log(f"Review {review_id} failed: {data.get('message', 'unknown')}")
                    return None

                # Still in progress
                patch_count = data.get('patch_count', 0)
                completed = data.get('completed_patches', 0)
                log(f"Review {review_id}: {status} ({completed}/{patch_count})")

            except Exception as e:
                log(f"Error checking review {review_id}: {e}")

            time.sleep(self.review_poll_interval)

    def post_patchwork_checks(self, series_id: int, review_id: str,
                              review_data: Dict) -> bool:
        """Post check results to Patchwork

        Args:
            series_id: Patchwork series ID
            review_id: AIR review ID
            review_data: Review data with 'review' field

        Returns:
            True if successful
        """
        check_url = f"{self.air_server}/ai-review.html?id={review_id}"
        reviews = review_data.get('review', [])

        try:
            series_data = self.patchwork.get('series', series_id)
            patches = series_data.get('patches', [])

            for i, patch in enumerate(patches):
                patch_id = patch['id']

                if i >= len(reviews):
                    state = 'warning'
                    desc = 'Internal error, no entry for review'
                elif reviews[i] and reviews[i].strip():
                    state = 'warning'
                    desc = 'AI review found issues'
                else:
                    state = 'success'
                    desc = 'AI review completed, no issues found'

                self.patchwork.post_check(patch=patch_id, name=self.check_name,
                                         state=state, url=check_url, desc=desc)

            log(f"Posted checks for {len(patches)} patches")
            return True

        except Exception as e:
            log(f"Error posting checks: {e}")
            return False

    def process_series(self, pw_series: Dict) -> bool:
        """Process a single series

        Args:
            pw_series: Patchwork series data

        Returns:
            True if successfully processed
        """
        series_id = pw_series['id']
        patch_count = pw_series.get('total', len(pw_series.get('patches', [])))
        name = pw_series.get('name', 'Unknown')

        log_open_sec(f"Processing series {series_id}: {name} ({patch_count} patches)")

        try:
            # Check rate limit
            if not self.rate_limiter.can_submit(patch_count):
                current = self.rate_limiter.patches_in_window()
                max_p = self.rate_limiter.max_patches
                log(f"Rate limit: {current}/{max_p} patches, queueing series")
                log_end_sec()
                return False

            # Submit to AIR
            review_id = self.submit_to_air(series_id)
            if not review_id:
                log("Failed to submit to AIR")
                log_end_sec()
                return False

            log(f"Submitted, review ID: {review_id}")

            # Record submission for rate limiting
            self.rate_limiter.record_submission(patch_count)

            # Wait for review
            review_data = self.wait_for_review(review_id)
            if not review_data:
                log("Review failed or timed out")
                log_end_sec()
                return True  # Still mark as processed

            # Post checks to Patchwork
            self.post_patchwork_checks(series_id, review_id, review_data)

            log_end_sec()
            return True

        except Exception as e:
            log(f"Error processing series: {e}")
            traceback.print_exc()
            log_end_sec()
            return False

    def try_process_queued(self) -> bool:
        """Try to process series from queue (LIFO)

        Returns:
            True if processed something
        """
        while self.queued_series:
            # LIFO: take from end
            series = self.queued_series[-1]
            patch_count = series.get('total', len(series.get('patches', [])))

            if not self.rate_limiter.can_submit(patch_count):
                # Still rate limited
                return False

            # Remove from queue and process
            self.queued_series.pop()
            series_id = series['id']

            if series_id in self.processed_series:
                continue

            if self.process_series(series):
                self.processed_series.add(series_id)
                self.save_state()
                return True

        return False

    def poll_once(self):
        """Run one polling iteration"""
        since = self.get_since_timestamp()

        log_open_sec(f"Polling patchwork since {since}")

        try:
            json_resp, new_since = self.patchwork.get_new_series(since=since)
            log(f"Found {len(json_resp)} series")

            # Advance timestamp by 1 usec to avoid duplicates
            if new_since:
                ts = datetime.fromisoformat(new_since)
                ts += timedelta(microseconds=1)
                self.last_event_ts = ts.isoformat()

            # Process new series
            for pw_series in json_resp:
                series_id = pw_series['id']

                if series_id in self.processed_series:
                    continue

                if not pw_series.get('received_all', True):
                    log(f"Series {series_id} incomplete, skipping")
                    continue

                if self.process_series(pw_series):
                    self.processed_series.add(series_id)
                else:
                    # Add to queue (will be at end for LIFO)
                    self.queued_series.append(pw_series)
                    log(f"Queued series {series_id} ({len(self.queued_series)} in queue)")

                self.save_state()

            # Try processing queued series
            while self.try_process_queued():
                pass

        except Exception as e:
            log(f"Error during poll: {e}")
            traceback.print_exc()

        log_end_sec()

    def run(self):
        """Run polling loop"""
        log(f"Starting pw_air_poller")
        log(f"  AIR URL: {self.air_url}")
        log(f"  Tree: {self.air_tree}")
        log(f"  Rate limit: {self.rate_limiter.max_patches} patches / {self.rate_limiter.window_days} days")
        log(f"  Poll interval: {self.poll_interval}s")
        log(f"  Review timeout: {self.review_timeout}s")

        # Try queued series first
        while self.try_process_queued():
            pass

        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                log("Shutting down...")
                self.save_state()
                break
            except Exception as e:
                log(f"Error in main loop: {e}")
                traceback.print_exc()

            time.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(
        description='Poll Patchwork for series and submit to AIR for review',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration file format:

[air]
url = https://air.example.com
token = your_air_token
tree = netdev/net-next
branch = main  # optional

[patchwork]
# Standard NIPA patchwork config
server = patchwork.kernel.org
use_ssl = true
token = your_patchwork_token
user = your_patchwork_user_id
check_name = ai-review  # optional, default: ai-review

[rate_limit]
max_patches = 30  # optional, default: 30
window_days = 3   # optional, default: 3

[poller]
poll_interval = 120      # optional, seconds between patchwork polls
review_timeout = 3600    # optional, max seconds to wait for review
review_poll_interval = 10  # optional, seconds between review status checks
state_file = pw-air-poller.state  # optional

[log]
dir = /path/to/logs  # optional
type = org           # optional
file = pw-air-poller.org  # optional
        """
    )

    parser.add_argument('config', help='Path to configuration file')
    parser.add_argument('--once', action='store_true',
                       help='Run once and exit (no continuous polling)')

    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Error: Config file not found: {args.config}")
        return 1

    try:
        poller = PwAirPoller(args.config)

        if args.once:
            poller.poll_once()
        else:
            poller.run()

        return 0
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
