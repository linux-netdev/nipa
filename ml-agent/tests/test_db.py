# SPDX-License-Identifier: GPL-2.0
# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=missing-class-docstring,wrong-import-position
# pylint: disable=import-error

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db import AgentDB


class TestIdentityResolution(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")

    def tearDown(self):
        self.db.close()

    def test_new_identity(self):
        iid = self.db.resolve_identity("Alice", "alice@example.com")
        self.assertIsNotNone(iid)
        self.assertEqual(self.db.get_identity_emails(iid), ["alice@example.com"])
        self.assertEqual(self.db.get_identity_names(iid), ["Alice"])

    def test_email_match_returns_same(self):
        iid1 = self.db.resolve_identity("Alice", "alice@example.com")
        iid2 = self.db.resolve_identity("Alice", "alice@example.com")
        self.assertEqual(iid1, iid2)

    def test_email_match_adds_new_name(self):
        iid1 = self.db.resolve_identity("Alice Smith", "alice@example.com")
        iid2 = self.db.resolve_identity("Alice S.", "alice@example.com")
        self.assertEqual(iid1, iid2)
        names = self.db.get_identity_names(iid1)
        self.assertIn("Alice Smith", names)
        self.assertIn("Alice S.", names)

    def test_name_match_adds_new_email(self):
        iid1 = self.db.resolve_identity("Bob", "bob@work.com")
        iid2 = self.db.resolve_identity("Bob", "bob@personal.com")
        self.assertEqual(iid1, iid2)
        emails = self.db.get_identity_emails(iid1)
        self.assertIn("bob@work.com", emails)
        self.assertIn("bob@personal.com", emails)

    def test_no_match_creates_new(self):
        iid1 = self.db.resolve_identity("Alice", "alice@example.com")
        iid2 = self.db.resolve_identity("Bob", "bob@example.com")
        self.assertNotEqual(iid1, iid2)

    def test_cross_conflict_prefers_email(self):
        iid_a = self.db.resolve_identity("Alice", "alice@example.com")
        self.db.resolve_identity("Bob", "bob@example.com")
        # email known as Alice's, name known as Bob's
        iid_c = self.db.resolve_identity("Bob", "alice@example.com")
        self.assertEqual(iid_c, iid_a)

    def test_empty_name(self):
        iid = self.db.resolve_identity("", "bare@example.com")
        self.assertIsNotNone(iid)
        self.assertEqual(self.db.get_identity_names(iid), [])

    def test_empty_name_then_name(self):
        iid1 = self.db.resolve_identity("", "x@example.com")
        iid2 = self.db.resolve_identity("Xavier", "x@example.com")
        self.assertEqual(iid1, iid2)
        self.assertEqual(self.db.get_identity_names(iid1), ["Xavier"])


class TestKnownDev(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")

    def tearDown(self):
        self.db.close()

    def test_default_unchecked(self):
        iid = self.db.resolve_identity("A", "a@a.com")
        known_dev, welcomed = self.db.get_identity(iid)
        self.assertEqual(known_dev, 0)
        self.assertEqual(welcomed, 0)

    def test_set_known(self):
        iid = self.db.resolve_identity("A", "a@a.com")
        self.db.set_known_dev(iid, 1)
        self.assertEqual(self.db.get_identity(iid)[0], 1)

    def test_set_noob(self):
        iid = self.db.resolve_identity("A", "a@a.com")
        self.db.set_known_dev(iid, 2)
        self.assertEqual(self.db.get_identity(iid)[0], 2)

    def test_set_welcomed(self):
        iid = self.db.resolve_identity("A", "a@a.com")
        self.db.set_welcomed(iid)
        self.assertEqual(self.db.get_identity(iid)[1], 1)


class TestSubmission(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.iid = self.db.resolve_identity("Dev", "dev@example.com")

    def tearDown(self):
        self.db.close()

    def test_add_and_no_duplicate(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        dup = self.db.find_recent_duplicate(
            self.iid, "net: fix bar", "2026-04-20T12:00:00")
        self.assertIsNone(dup)

    def test_duplicate_within_24h(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        dup = self.db.find_recent_duplicate(
            self.iid, "net: fix foo", "2026-04-20T20:00:00")
        self.assertIsNotNone(dup)
        self.assertEqual(dup[0], "<msg1>")

    def test_no_duplicate_after_24h(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        dup = self.db.find_recent_duplicate(
            self.iid, "net: fix foo", "2026-04-22T10:00:00")
        self.assertIsNone(dup)

    def test_duplicate_different_identity(self):
        iid2 = self.db.resolve_identity("Other", "other@example.com")
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        dup = self.db.find_recent_duplicate(
            iid2, "net: fix foo", "2026-04-20T12:00:00")
        self.assertIsNone(dup)

    def test_previous_version_v1_to_v2(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        prev = self.db.find_previous_version(self.iid, "net: fix foo", 2)
        self.assertIsNotNone(prev)
        self.assertEqual(prev[0], "<msg1>")

    def test_previous_version_v0_to_v2(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", None,
                               "2026-04-20T10:00:00")
        prev = self.db.find_previous_version(self.iid, "net: fix foo", 2)
        self.assertIsNotNone(prev)

    def test_previous_version_no_match(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        prev = self.db.find_previous_version(self.iid, "net: fix foo", 5)
        self.assertIsNone(prev)

    def test_warned_bitmask(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        self.db.set_submission_warned("<msg1>", 1)
        self.db.set_submission_warned("<msg1>", 2)
        cur = self.db.conn.cursor()
        cur.execute("SELECT warned FROM submission WHERE message_id = ?",
                    ("<msg1>",))
        self.assertEqual(cur.fetchone()[0], 3)

    def test_ignore_duplicate_message_id(self):
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 1,
                               "2026-04-20T10:00:00")
        self.db.add_submission("<msg1>", self.iid, "net: fix foo", 2,
                               "2026-04-21T10:00:00")
        cur = self.db.conn.cursor()
        cur.execute("SELECT version FROM submission WHERE message_id = ?",
                    ("<msg1>",))
        self.assertEqual(cur.fetchone()[0], 1)


class TestPvBotAction(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.iid = self.db.resolve_identity("Dev", "dev@example.com")

    def tearDown(self):
        self.db.close()

    def test_add_and_query(self):
        self.db.add_pv_bot_action("<msg1>", self.iid, "cc",
                                   "2026-04-20T10:00:00")
        actions = self.db.get_pv_bot_actions(self.iid)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][2], "cc")

    def test_multiple_tags(self):
        self.db.add_pv_bot_action("<msg1>", self.iid, "cc",
                                   "2026-04-20T10:00:00")
        self.db.add_pv_bot_action("<msg1>", self.iid, "nit",
                                   "2026-04-20T10:00:00")
        actions = self.db.get_pv_bot_actions(self.iid)
        self.assertEqual(len(actions), 2)

    def test_query_all(self):
        iid2 = self.db.resolve_identity("Other", "other@example.com")
        self.db.add_pv_bot_action("<m1>", self.iid, "cc",
                                   "2026-04-20T10:00:00")
        self.db.add_pv_bot_action("<m2>", iid2, "nit",
                                   "2026-04-20T11:00:00")
        actions = self.db.get_pv_bot_actions()
        self.assertEqual(len(actions), 2)


if __name__ == '__main__':
    unittest.main()
