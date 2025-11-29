#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""NIPA AIR Upload - AIR to Patchwork synchronization service

Polls AIR for public reviews and posts check results to Patchwork
for matching series.
"""

import argparse
import configparser
import json
import os
import sys
import time
import traceback
from datetime import datetime, UTC
from typing import Optional, Dict, List
import requests

from core import NIPA_DIR, log_init
from pw import Patchwork


class PatchworkSeries:
    """Represents a Patchwork series with its patches"""

    def __init__(self, patchwork: Patchwork, series_id: int, check_name: str):
        """Initialize series

        Args:
            patchwork: Patchwork client
            series_id: Series ID
            check_name: Check name to look for
        """
        self.patchwork = patchwork
        self.series_id = series_id
        self.check_name = check_name
        self.series_data = None
        self.patches = []
        self.patches_ready = []

        self._fetch()

    def _fetch(self):
        """Fetch series and check which patches are ready"""
        self.series_data = self.patchwork.get('series', self.series_id)
        self.patches = self.series_data.get('patches', [])

        # Check each patch for existing check, this prevents the race
        # of us uploading the result before poller marked the review as
        # pending, as poller would just override us
        self.patches_ready = []
        for i, patch in enumerate(self.patches):
            patch_id = patch['id']
            try:
                # Fetch checks for this patch
                existing_checks = self.patchwork.get_all(f'patches/{patch_id}/checks')
                check_exists = any(c.get('context') == self.check_name for c in existing_checks)
                self.patches_ready.append(check_exists)
            except Exception as e:
                print(f"    Warning: Error fetching checks for patch {i+1} (id={patch_id}): {e}")
                self.patches_ready.append(False)

    def all_patches_ready(self) -> bool:
        """Check if all patches have the check entry

        Returns:
            True if all patches have the check entry
        """
        return all(self.patches_ready)

    def ready_count(self) -> int:
        """Get count of patches that are ready

        Returns:
            Number of patches with check entry
        """
        return sum(self.patches_ready)


class AirPatchworkSync:
    """Synchronize AIR reviews to Patchwork checks"""

    def __init__(self, config_path: str):
        """Initialize sync service

        Args:
            config_path: Path to configuration file
        """
        self.config = configparser.ConfigParser()
        self.config.read([config_path, "nipa.config"])

        # AIR configuration
        self.air_url = self.config.get('air', 'url').rstrip('/')
        self.air_server = self.config.get('air', 'server', fallback=self.air_url).rstrip('/')
        self.air_token = self.config.get('air', 'token', fallback=None)

        # Patchwork configuration
        self.check_name = self.config.get('patchwork', 'check_name', fallback='ai-review')

        # Service configuration
        self.poll_interval = self.config.getint('service', 'poll_interval', fallback=300)
        self.state_file = self.config.get('service', 'state_file',
                                         fallback='nipa-air-upload.state')

        # Initialize logging
        log_dir = self.config.get('log', 'dir', fallback=NIPA_DIR)
        log_init(self.config.get('log', 'type', fallback='org'),
                 self.config.get('log', 'file', fallback=os.path.join(log_dir, "air-upload.org")),
                 force_single_thread=True)


        # Initialize Patchwork client
        self.patchwork = Patchwork(self.config)

        # Load state
        self.uploaded_reviews = self.load_state()

    def load_state(self) -> set:
        """Load set of already uploaded review IDs from state file

        Returns:
            Set of review IDs that have been uploaded
        """
        if not os.path.exists(self.state_file):
            return set()

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
                return set(state.get('uploaded_reviews', []))
        except Exception as e:
            print(f"Error loading state file: {e}")
            return set()

    def save_state(self, uploaded_reviews: set):
        """Save set of uploaded review IDs to state file

        Args:
            uploaded_reviews: Set of review IDs that have been uploaded
        """
        state = {
            'uploaded_reviews': list(uploaded_reviews),
            'last_update': datetime.now(UTC).isoformat(),
            'count': len(uploaded_reviews)
        }

        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving state file: {e}")

    def get_public_reviews(self) -> List[Dict]:
        """Fetch public reviews from AIR

        Returns:
            List of review dictionaries
        """
        try:
            url = f"{self.air_url}/api/reviews?limit=100"
            if self.air_token:
                url += f"&token={self.air_token}"
            else:
                url += '&public_only=true'
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('reviews', [])
        except Exception as e:
            print(f"Error fetching public reviews from AIR: {e}")
            return []

    def get_review_details(self, review_id: str) -> Optional[Dict]:
        """Fetch full review details from AIR

        Args:
            review_id: Review ID

        Returns:
            Review details dictionary or None
        """
        try:
            url = f"{self.air_url}/api/review?id={review_id}&format=inline"
            if self.air_token:
                url += f"&token={self.air_token}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching review {review_id}: {e}")
            return None

    def post_patchwork_check(self, pw_series: PatchworkSeries, review_id: str,
                            review_data: Dict) -> bool:
        """Post check result to Patchwork

        Args:
            pw_series: PatchworkSeries object with patches
            review_id: AIR review ID
            review_data: Review data with 'review' field containing per-patch results

        Returns:
            True if successful
        """
        check_url = f"{self.air_server}/ai-review.html?id={review_id}"

        reviews = review_data.get('review', [])

        print(f"  Posting checks to {len(pw_series.patches)} patches in series {pw_series.series_id}")

        try:
            for i, patch in enumerate(pw_series.patches):
                patch_id = patch['id']

                # Check if this patch has review comments
                if i >= len(reviews):
                    state = 'warning'
                    desc = 'Internal error, no entry for review'
                elif reviews[i] and reviews[i].strip():
                    state = 'warning'
                    desc = 'AI review found issues'
                else:
                    state = 'success'
                    desc = 'AI review completed, no issues found'

                print(f"    Patch {i+1}/{len(pw_series.patches)} (id={patch_id}): {state}")

                self.patchwork.post_check(patch=patch_id, name=self.check_name,
                                         state=state, url=check_url, desc=desc)

            return True
        except Exception as e:
            print(f"  Error posting check to Patchwork: {e}")
            return False

    def process_review(self, review: Dict) -> bool:
        """Process a single review

        Args:
            review: Review summary from AIR

        Returns:
            True if processed successfully (checks posted to all patches)
        """
        review_id = review.get('review_id')
        status = review.get('status')

        print(f"Processing review {review_id} (status: {status})")

        if status != 'done':
            print(f"  Skipping: status is {status}, not done")
            return False

        review_data = self.get_review_details(review_id)
        if not review_data:
            print("  Error: Could not fetch review details")
            return False

        pw_series_id = review_data.get('patchwork_series_id')
        if not pw_series_id:
            print("  Skipping: No patchwork series ID")
            return True

        print(f"  Patchwork series ID: {pw_series_id}")

        try:
            pw_series = PatchworkSeries(self.patchwork, pw_series_id,
                                        self.check_name)
        except Exception as e:
            print(f"  Error fetching series: {e}")
            return False

        if not pw_series.patches:
            print("  Warning: Series has no patches")
            return True

        # Check if all patches have the check entry (prevents race with initial scan)
        if not pw_series.all_patches_ready():
            ready = pw_series.ready_count()
            total = len(pw_series.patches)
            print(f"  Not ready: only {ready}/{total} patches have check '{self.check_name}' (will retry later)")
            return False

        # Post check to Patchwork
        success = self.post_patchwork_check(pw_series, review_id, review_data)
        if success:
            print("  Successfully posted checks to all patches")

        return success

    def run_once(self):
        """Run one sync iteration"""
        print("Polling for new reviews...")

        # Fetch public reviews (100 most recent)
        reviews = self.get_public_reviews()
        if not reviews:
            print("No reviews found")
            return

        print(f"Found {len(reviews)} public reviews")
        api_returned_full_set = len(reviews) >= 100

        fetched_review_ids = {r.get('review_id') for r in reviews if r.get('review_id')}

        # Filter to reviews we haven't uploaded yet
        new_reviews = [r for r in reviews if r.get('review_id') not in self.uploaded_reviews]

        if not new_reviews:
            if api_returned_full_set and self.uploaded_reviews - fetched_review_ids:
                # Trim state to only reviews we saw in this fetch
                self.uploaded_reviews &= fetched_review_ids
                self.save_state(self.uploaded_reviews)
                print(f"Trimmed state to {len(self.uploaded_reviews)} reviews")
            return

        print(f"Processing {len(new_reviews)} new reviews (already uploaded: {len(self.uploaded_reviews)})...")

        # Track newly uploaded reviews in this run
        newly_uploaded = set()

        # Process each review
        for review in new_reviews:
            review_id = review.get('review_id')
            if not review_id:
                continue

            try:
                processed = self.process_review(review)
                if processed:
                    newly_uploaded.add(review_id)
            except Exception as e:
                print(f"Error processing review {review_id}: {e}")
                traceback.print_exc()

        # Update uploaded reviews set
        if newly_uploaded:
            self.uploaded_reviews.update(newly_uploaded)
            print(f"Uploaded {len(newly_uploaded)} new reviews")

        # Trim state if API returned full set (100 reviews)
        # Avoid state growing but also losing all state on bad fetch
        if api_returned_full_set:
            old_count = len(self.uploaded_reviews)
            self.uploaded_reviews &= fetched_review_ids
            trimmed = old_count - len(self.uploaded_reviews)
            if trimmed > 0:
                print(f"Trimmed {trimmed} old reviews from state")

        # Save state
        self.save_state(self.uploaded_reviews)
        print(f"State updated: tracking {len(self.uploaded_reviews)} uploaded reviews")

    def run(self):
        """Run sync service continuously"""
        print("Starting NIPA AIR Upload service")
        print(f"  AIR URL: {self.air_url}")
        print(f"  Check name: {self.check_name}")
        print(f"  Poll interval: {self.poll_interval}s")
        print(f"  State file: {self.state_file}")

        if self.uploaded_reviews:
            print(f"  Already uploaded: {len(self.uploaded_reviews)} reviews")
        else:
            print("  No previous state found (will process all reviews)")

        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                print("\nShutting down...")
                break
            except Exception as e:
                print(f"Error in sync loop: {e}")
                traceback.print_exc()

            print(f"\nSleeping for {self.poll_interval} seconds...")
            time.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(
        description='Upload AIR reviews to Patchwork as checks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration file format:

[air]
url = https://air.example.com
server = https://air.example.com  # Optional, defaults to url
token = your_air_token  # Optional, for authenticated access

[patchwork]
# Standard NIPA patchwork config
server = patchwork.kernel.org
use_ssl = true
token = your_patchwork_token
user = your_patchwork_user_id

# Sync-specific config
check_name = ai-review  # Optional, default: ai-review

[service]
poll_interval = 300  # Optional, default: 300 seconds
state_file = nipa-air-upload.state  # Optional
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
        sync = AirPatchworkSync(args.config)

        if args.once:
            sync.run_once()
        else:
            sync.run()

        return 0
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
