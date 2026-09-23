# SPDX-License-Identifier: GPL-2.0
# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=missing-class-docstring,wrong-import-position
# pylint: disable=import-error,unused-argument

import configparser
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db import AgentDB
from ml_email import parse_email_str
from agent import process_email, check_known_developer, tag_addr


def _make_raw(subject="[PATCH] test", from_hdr="Dev <dev@example.com>",
              to_hdr="list@vger.kernel.org", message_id="<msg@test>",
              date="Mon, 20 Apr 2026 10:00:00 +0000",
              in_reply_to="", references="", body="patch content"):
    parts = []
    parts.append(f"Subject: {subject}")
    parts.append(f"From: {from_hdr}")
    parts.append(f"To: {to_hdr}")
    parts.append(f"Message-ID: {message_id}")
    parts.append(f"Date: {date}")
    if in_reply_to:
        parts.append(f"In-Reply-To: {in_reply_to}")
    if references:
        parts.append(f"References: {references}")
    parts.append("Content-Type: text/plain")
    parts.append("")
    parts.append(body)
    return "\n".join(parts)


class TestProcessSubmission(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.templates = {
            'welcome': 'Welcome!',
            'resubmit-warn': 'Too fast!',
            'threaded-warn': 'Do not thread!',
        }

    def tearDown(self):
        self.db.close()

    @patch('agent.check_known_developer', return_value=1)
    def test_submission_recorded(self, mock_known):
        raw = _make_raw(subject="[PATCH net] net: fix foo")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        cur = self.db.conn.cursor()
        cur.execute("SELECT title, version FROM submission "
                    "WHERE message_id = ?", ("<msg@test>",))
        row = cur.fetchone()
        self.assertEqual(row[0], "net: fix foo")
        self.assertIsNone(row[1])

    @patch('agent.check_known_developer', return_value=1)
    def test_non_submission_not_recorded(self, mock_known):
        raw = _make_raw(subject="Re: some discussion")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        cur = self.db.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM submission")
        self.assertEqual(cur.fetchone()[0], 0)

    @patch('agent.check_known_developer', return_value=1)
    def test_reply_not_submission(self, mock_known):
        raw = _make_raw(subject="[PATCH net] net: fix foo",
                        in_reply_to="<prev@test>")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        cur = self.db.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM submission")
        self.assertEqual(cur.fetchone()[0], 0)


class TestWelcomeEmail(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.templates = {
            'welcome': 'Welcome!',
            'resubmit-warn': 'Too fast!',
            'threaded-warn': 'Do not thread!',
        }

    def tearDown(self):
        self.db.close()

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=2)
    def test_welcome_sent_to_noob(self, mock_known, mock_send):
        raw = _make_raw(subject="[PATCH] net: fix foo")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        mock_send.assert_called()
        call_args = mock_send.call_args_list
        welcome_calls = [c for c in call_args
                         if c[0][3] == 'Welcome!']
        self.assertEqual(len(welcome_calls), 1)

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=2)
    def test_welcome_not_sent_twice(self, mock_known, mock_send):
        raw1 = _make_raw(subject="[PATCH] net: fix foo",
                         message_id="<msg1@test>")
        msg1 = parse_email_str(raw1)
        process_email(msg1, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        mock_send.reset_mock()
        raw2 = _make_raw(subject="[PATCH] net: fix bar",
                         message_id="<msg2@test>")
        msg2 = parse_email_str(raw2)
        process_email(msg2, "<msg2@test>", "2026-04-21T10:00:00+00:00",
                      self.config, self.db, self.templates)

        welcome_calls = [c for c in mock_send.call_args_list
                         if len(c[0]) >= 4 and c[0][3] == 'Welcome!']
        self.assertEqual(len(welcome_calls), 0)

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_no_welcome_for_known_dev(self, mock_known, mock_send):
        raw = _make_raw(subject="[PATCH] net: fix foo")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        welcome_calls = [c for c in mock_send.call_args_list
                         if len(c[0]) >= 4 and c[0][3] == 'Welcome!']
        self.assertEqual(len(welcome_calls), 0)


class TestResubmitWarning(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.templates = {
            'welcome': 'Welcome!',
            'resubmit-warn': 'Too fast!',
            'threaded-warn': 'Do not thread!',
        }

    def tearDown(self):
        self.db.close()

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_resubmit_within_24h(self, mock_known, mock_send):
        raw1 = _make_raw(subject="[PATCH] net: fix foo",
                         message_id="<msg1@test>")
        msg1 = parse_email_str(raw1)
        process_email(msg1, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        mock_send.reset_mock()
        raw2 = _make_raw(subject="[PATCH v2] net: fix foo",
                         message_id="<msg2@test>")
        msg2 = parse_email_str(raw2)
        process_email(msg2, "<msg2@test>", "2026-04-20T20:00:00+00:00",
                      self.config, self.db, self.templates)

        warn_calls = [c for c in mock_send.call_args_list
                      if len(c[0]) >= 4 and c[0][3] == 'Too fast!']
        self.assertEqual(len(warn_calls), 1)

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_no_resubmit_after_24h(self, mock_known, mock_send):
        raw1 = _make_raw(subject="[PATCH] net: fix foo",
                         message_id="<msg1@test>")
        msg1 = parse_email_str(raw1)
        process_email(msg1, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        mock_send.reset_mock()
        raw2 = _make_raw(subject="[PATCH v2] net: fix foo",
                         message_id="<msg2@test>")
        msg2 = parse_email_str(raw2)
        process_email(msg2, "<msg2@test>", "2026-04-22T10:00:00+00:00",
                      self.config, self.db, self.templates)

        warn_calls = [c for c in mock_send.call_args_list
                      if len(c[0]) >= 4 and c[0][3] == 'Too fast!']
        self.assertEqual(len(warn_calls), 0)


class TestThreadedWarning(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.templates = {
            'welcome': 'Welcome!',
            'resubmit-warn': 'Too fast!',
            'threaded-warn': 'Do not thread!',
        }

    def tearDown(self):
        self.db.close()

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_threaded_v2_warns(self, mock_known, mock_send):
        # First: a proper v1 submission
        raw1 = _make_raw(subject="[PATCH] net: fix foo",
                         message_id="<msg1@test>")
        msg1 = parse_email_str(raw1)
        process_email(msg1, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        mock_send.reset_mock()
        # Then: v2 as a reply (threaded)
        raw2 = _make_raw(subject="[PATCH v2] net: fix foo",
                         message_id="<msg2@test>",
                         in_reply_to="<msg1@test>")
        msg2 = parse_email_str(raw2)
        process_email(msg2, "<msg2@test>", "2026-04-21T10:00:00+00:00",
                      self.config, self.db, self.templates)

        warn_calls = [c for c in mock_send.call_args_list
                      if len(c[0]) >= 4 and c[0][3] == 'Do not thread!']
        self.assertEqual(len(warn_calls), 1)

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_threaded_no_prev_version_no_warn(self, mock_known, mock_send):
        # v5 as reply, but no v3 or v4 recorded
        raw = _make_raw(subject="[PATCH v5] net: fix foo",
                        message_id="<msg@test>",
                        in_reply_to="<prev@test>")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        warn_calls = [c for c in mock_send.call_args_list
                      if len(c[0]) >= 4 and c[0][3] == 'Do not thread!']
        self.assertEqual(len(warn_calls), 0)

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_rfc_reply_not_threaded_warn(self, mock_known, mock_send):
        # v1 submission
        raw1 = _make_raw(subject="[PATCH] net: fix foo",
                         message_id="<msg1@test>")
        msg1 = parse_email_str(raw1)
        process_email(msg1, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        mock_send.reset_mock()
        # RFC v2 as reply — should NOT warn (RFC excluded)
        raw2 = _make_raw(subject="[RFC PATCH v2] net: fix foo",
                         message_id="<msg2@test>",
                         in_reply_to="<msg1@test>")
        msg2 = parse_email_str(raw2)
        process_email(msg2, "<msg2@test>", "2026-04-21T10:00:00+00:00",
                      self.config, self.db, self.templates)

        warn_calls = [c for c in mock_send.call_args_list
                      if len(c[0]) >= 4 and c[0][3] == 'Do not thread!']
        self.assertEqual(len(warn_calls), 0)


class TestAiReviewWarning(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.config.read_dict({'ml-agent': {
            'sashiko-url': 'https://sashiko.example/'}})
        self.templates = {
            'welcome': 'Welcome!',
            'resubmit-warn': 'Too fast!',
            'threaded-warn': 'Do not thread!',
            'ai-review-warn': 'Wait for AI: {url}',
        }

    def tearDown(self):
        self.db.close()

    def _post_v1_v2(self, v2_ts="2026-04-21T12:00:00+00:00"):
        msg = parse_email_str(_make_raw(subject="[PATCH] net: fix foo",
                                        message_id="<msg1@test>"))
        process_email(msg, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)
        msg = parse_email_str(_make_raw(subject="[PATCH v2] net: fix foo",
                                        message_id="<msg2@test>"))
        process_email(msg, "<msg2@test>", v2_ts,
                      self.config, self.db, self.templates)

    @staticmethod
    def _ai_warns(mock_send):
        return [c for c in mock_send.call_args_list
                if c[0][3].startswith('Wait for AI')]

    @patch('agent.requests.get')
    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_embargoed_warns(self, mock_known, mock_send, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {'status': 'Embargoed'})
        self._post_v1_v2()

        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args[1]['params'],
                         {'id': 'msg1@test'})
        warns = self._ai_warns(mock_send)
        self.assertEqual(len(warns), 1)
        self.assertEqual(
            warns[0][0][3],
            'Wait for AI: https://sashiko.example/#/patchset/msg1%40test')
        self.assertEqual(warns[0][1]['tag'], 'pv')

    @patch('agent.requests.get')
    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_reviewed_no_warn(self, mock_known, mock_send, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {'status': 'Reviewed'})
        self._post_v1_v2()
        self.assertEqual(len(self._ai_warns(mock_send)), 0)

    @patch('agent.requests.get')
    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_unknown_to_sashiko_no_warn(self, mock_known, mock_send,
                                        mock_get):
        mock_get.return_value = MagicMock(status_code=404)
        self._post_v1_v2()
        self.assertEqual(len(self._ai_warns(mock_send)), 0)

    @patch('agent.requests.get')
    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_24h_warn_takes_precedence(self, mock_known, mock_send,
                                       mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {'status': 'Pending'})
        self._post_v1_v2(v2_ts="2026-04-20T12:00:00+00:00")
        self.assertEqual(len(self._ai_warns(mock_send)), 0)
        mock_get.assert_not_called()

    @patch('agent.requests.get')
    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_no_url_configured(self, mock_known, mock_send, mock_get):
        self.config.remove_option('ml-agent', 'sashiko-url')
        self._post_v1_v2()
        mock_get.assert_not_called()
        self.assertEqual(len(self._ai_warns(mock_send)), 0)


class TestPvBotRecording(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.templates = {
            'welcome': 'Welcome!',
            'resubmit-warn': 'Too fast!',
            'threaded-warn': 'Do not thread!',
        }

    def tearDown(self):
        self.db.close()

    @patch('agent.check_known_developer', return_value=1)
    def test_pv_bot_recorded(self, mock_known):
        raw = _make_raw(
            subject="Re: [PATCH] net: fix foo",
            from_hdr="Reviewer <rev@example.com>",
            to_hdr="Author <author@example.com>",
            body="Looks not great\npv-bot: nit\n",
            in_reply_to="<prev@test>")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        # The identity should be the To: header person
        to_name, to_email = "Author", "author@example.com"
        to_iid = self.db.resolve_identity(to_name, to_email)
        actions = self.db.get_pv_bot_actions(to_iid)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][2], "nit")

    @patch('agent.check_known_developer', return_value=1)
    def test_multiple_pv_bot_lines(self, mock_known):
        raw = _make_raw(
            subject="Re: [PATCH] net: fix foo",
            from_hdr="Reviewer <rev@example.com>",
            to_hdr="Author <author@example.com>",
            body="pv-bot: cc\npv-bot: nit\n",
            in_reply_to="<prev@test>")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        actions = self.db.get_pv_bot_actions()
        self.assertEqual(len(actions), 2)
        tags = {a[2] for a in actions}
        self.assertEqual(tags, {"cc", "nit"})

    @patch('agent.check_known_developer', return_value=1)
    def test_no_pv_bot(self, mock_known):
        raw = _make_raw(body="normal email text")
        msg = parse_email_str(raw)
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        actions = self.db.get_pv_bot_actions()
        self.assertEqual(len(actions), 0)


class TestKnownDeveloper(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.config.read_dict({'ml-agent': {'linux-tree': '/fake/linux'}})

    def tearDown(self):
        self.db.close()

    @patch('agent.subprocess.run')
    def test_no_commits_is_noob(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        iid = self.db.resolve_identity("Newbie", "new@example.com")
        result = check_known_developer(self.config, self.db, iid)
        self.assertEqual(result, 2)

    @patch('agent.subprocess.run')
    def test_many_old_commits_is_known(self, mock_run):
        lines = "\n".join(
            [f"abc{i} 2020-0{i+1}-01" for i in range(10)])
        mock_run.return_value = MagicMock(stdout=lines)
        iid = self.db.resolve_identity("Veteran", "vet@example.com")
        result = check_known_developer(self.config, self.db, iid)
        self.assertEqual(result, 1)

    @patch('agent.subprocess.run')
    def test_few_recent_is_noob(self, mock_run):
        mock_run.return_value = MagicMock(stdout="abc1 2026-03-01\nabc2 2026-04-01")
        iid = self.db.resolve_identity("Newish", "newish@example.com")
        result = check_known_developer(self.config, self.db, iid)
        self.assertEqual(result, 2)

    @patch('agent.subprocess.run')
    def test_few_with_old_is_known(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="abc1 2020-01-01\nabc2 2026-04-01")
        iid = self.db.resolve_identity("Oldish", "old@example.com")
        result = check_known_developer(self.config, self.db, iid)
        self.assertEqual(result, 1)

    @patch('agent.subprocess.run')
    def test_cached_skips_git(self, mock_run):
        iid = self.db.resolve_identity("Cached", "cached@example.com")
        self.db.set_known_dev(iid, 1)
        result = check_known_developer(self.config, self.db, iid)
        self.assertEqual(result, 1)
        mock_run.assert_not_called()

    def test_no_linux_tree_config(self):
        config_empty = configparser.ConfigParser()
        iid = self.db.resolve_identity("NoConf", "nc@example.com")
        result = check_known_developer(config_empty, self.db, iid)
        self.assertEqual(result, 0)


class TestTagAddr(unittest.TestCase):
    def test_bare_addr(self):
        self.assertEqual(tag_addr("bot@example.com", "welcome"),
                         "bot+welcome@example.com")

    def test_with_display_name(self):
        self.assertEqual(tag_addr("The Bot <bot@example.com>", "pv"),
                         "The Bot <bot+pv@example.com>")

    def test_no_tag(self):
        self.assertEqual(tag_addr("bot@example.com", None),
                         "bot@example.com")

    def test_malformed_addr(self):
        self.assertEqual(tag_addr("not-an-address", "pv"), "not-an-address")


class TestSendTags(unittest.TestCase):
    def setUp(self):
        self.db = AgentDB(":memory:")
        self.config = configparser.ConfigParser()
        self.templates = {'welcome': 'Welcome!',
                          'resubmit-warn': 'Too fast!',
                          'threaded-warn': 'Not threaded!'}

    def tearDown(self):
        self.db.close()

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=2)
    def test_welcome_tagged(self, mock_known, mock_send):
        msg = parse_email_str(_make_raw(subject="[PATCH] net: fix foo"))
        process_email(msg, "<msg@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        welcome = [c for c in mock_send.call_args_list
                   if c[0][3] == 'Welcome!']
        self.assertEqual(len(welcome), 1)
        self.assertEqual(welcome[0][1]['tag'], 'welcome')

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_resubmit_warn_tagged(self, mock_known, mock_send):
        for i, mid in enumerate(["<msg1@test>", "<msg2@test>"]):
            msg = parse_email_str(_make_raw(subject="[PATCH] net: fix foo",
                                            message_id=mid))
            process_email(msg, mid, f"2026-04-20T1{i}:00:00+00:00",
                          self.config, self.db, self.templates)

        warns = [c for c in mock_send.call_args_list
                 if c[0][3] == 'Too fast!']
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0][1]['tag'], 'pv')

    @patch('agent.send_email')
    @patch('agent.check_known_developer', return_value=1)
    def test_threaded_warn_tagged(self, mock_known, mock_send):
        msg = parse_email_str(_make_raw(subject="[PATCH] net: fix foo",
                                        message_id="<msg1@test>"))
        process_email(msg, "<msg1@test>", "2026-04-20T10:00:00+00:00",
                      self.config, self.db, self.templates)

        msg = parse_email_str(_make_raw(subject="[PATCH v2] net: fix foo",
                                        message_id="<msg2@test>",
                                        in_reply_to="<msg1@test>"))
        process_email(msg, "<msg2@test>", "2026-04-22T10:00:00+00:00",
                      self.config, self.db, self.templates)

        warns = [c for c in mock_send.call_args_list
                 if c[0][3] == 'Not threaded!']
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0][1]['tag'], 'pv')


if __name__ == '__main__':
    unittest.main()
