# SPDX-License-Identifier: GPL-2.0
# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=missing-class-docstring,wrong-import-position
# pylint: disable=import-error

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ml_email import (
    extract_bracket_contents, extract_title, extract_version,
    is_submission, is_resubmission_candidate, is_reply,
    extract_pv_bot_tags, split_from, parse_email_str,
)


def _make_msg(subject="test", from_hdr="Test <t@t.com>",
              in_reply_to="", references="", body=""):
    parts = []
    parts.append(f"Subject: {subject}")
    parts.append(f"From: {from_hdr}")
    parts.append("Message-ID: <test@test.com>")
    parts.append("Date: Mon, 20 Apr 2026 10:00:00 +0000")
    if in_reply_to:
        parts.append(f"In-Reply-To: {in_reply_to}")
    if references:
        parts.append(f"References: {references}")
    parts.append("Content-Type: text/plain")
    parts.append("")
    parts.append(body)
    return parse_email_str("\n".join(parts))


class TestBracketContents(unittest.TestCase):
    def test_patch(self):
        self.assertEqual(
            extract_bracket_contents("[PATCH net-next v2 1/3] foo"),
            "PATCH net-next v2 1/3")

    def test_no_bracket(self):
        self.assertIsNone(extract_bracket_contents("no bracket"))

    def test_empty(self):
        self.assertIsNone(extract_bracket_contents(""))

    def test_none(self):
        self.assertIsNone(extract_bracket_contents(None))

    def test_unclosed(self):
        self.assertIsNone(extract_bracket_contents("[unclosed"))


class TestTitleExtraction(unittest.TestCase):
    def test_single_tag(self):
        self.assertEqual(
            extract_title("[PATCH] net: fix foo"),
            "net: fix foo")

    def test_multiple_tags(self):
        self.assertEqual(
            extract_title("[PATCH net-next v2 1/3] net: foo: add bar"),
            "net: foo: add bar")

    def test_nested_tags(self):
        self.assertEqual(
            extract_title("[PATCH] [RFC] some title"),
            "some title")

    def test_no_tags(self):
        self.assertEqual(extract_title("plain subject"), "plain subject")

    def test_empty(self):
        self.assertEqual(extract_title(""), "")

    def test_none(self):
        self.assertEqual(extract_title(None), "")


class TestVersionExtraction(unittest.TestCase):
    def test_v2(self):
        self.assertEqual(extract_version("PATCH net-next v2 1/3"), 2)

    def test_v10(self):
        self.assertEqual(extract_version("PATCH v10"), 10)

    def test_no_version(self):
        self.assertIsNone(extract_version("PATCH net-next 1/3"))

    def test_none(self):
        self.assertIsNone(extract_version(None))

    def test_not_version_prefix(self):
        # "veth" should not match
        self.assertIsNone(extract_version("PATCH net veth"))


class TestIsSubmission(unittest.TestCase):
    def test_patch_no_reply(self):
        msg = _make_msg(subject="[PATCH net] net: fix bug")
        self.assertTrue(is_submission(msg))

    def test_rfc_no_reply(self):
        msg = _make_msg(subject="[RFC PATCH net] net: new feature")
        self.assertTrue(is_submission(msg))

    def test_rfc_only(self):
        msg = _make_msg(subject="[RFC] net: new feature")
        self.assertTrue(is_submission(msg))

    def test_patch_with_reply(self):
        msg = _make_msg(subject="[PATCH net] net: fix bug",
                        in_reply_to="<prev@test.com>")
        self.assertFalse(is_submission(msg))

    def test_no_bracket(self):
        msg = _make_msg(subject="Re: some discussion")
        self.assertFalse(is_submission(msg))

    def test_bracket_no_patch(self):
        msg = _make_msg(subject="[ANN] announcement")
        self.assertFalse(is_submission(msg))

    def test_case_insensitive(self):
        msg = _make_msg(subject="[patch net] net: fix")
        self.assertTrue(is_submission(msg))


class TestIsResubmissionCandidate(unittest.TestCase):
    def test_patch_v2_reply(self):
        msg = _make_msg(subject="[PATCH v2] net: fix bug",
                        in_reply_to="<prev@test.com>")
        self.assertTrue(is_resubmission_candidate(msg))

    def test_rfc_v2_reply(self):
        msg = _make_msg(subject="[RFC PATCH v2] net: feature",
                        in_reply_to="<prev@test.com>")
        self.assertFalse(is_resubmission_candidate(msg))

    def test_patch_v2_no_reply(self):
        msg = _make_msg(subject="[PATCH v2] net: fix bug")
        self.assertFalse(is_resubmission_candidate(msg))

    def test_patch_no_version_reply(self):
        msg = _make_msg(subject="[PATCH] net: fix bug",
                        in_reply_to="<prev@test.com>")
        self.assertFalse(is_resubmission_candidate(msg))

    def test_references_counts_as_reply(self):
        msg = _make_msg(subject="[PATCH v3] net: fix",
                        references="<prev@test.com>")
        self.assertTrue(is_resubmission_candidate(msg))

    def test_individual_patch_in_series_skipped(self):
        # Patch 3/3 of a v6 series, sent as reply to cover — must not
        # be treated as a resubmission of an earlier version.
        msg = _make_msg(subject="[PATCH net-next v6 3/3] net: foo",
                        in_reply_to="<cover@test.com>")
        self.assertFalse(is_resubmission_candidate(msg))

    def test_cover_letter_counts(self):
        msg = _make_msg(subject="[PATCH v2 0/3] net: foo",
                        in_reply_to="<prev@test.com>")
        self.assertTrue(is_resubmission_candidate(msg))


class TestIsReply(unittest.TestCase):
    def test_not_reply(self):
        msg = _make_msg()
        self.assertFalse(is_reply(msg))

    def test_in_reply_to(self):
        msg = _make_msg(in_reply_to="<x@y.com>")
        self.assertTrue(is_reply(msg))

    def test_references(self):
        msg = _make_msg(references="<x@y.com>")
        self.assertTrue(is_reply(msg))


class TestPvBotTags(unittest.TestCase):
    def test_single_tag(self):
        msg = _make_msg(body="some text\npv-bot: cc\nmore text")
        self.assertEqual(extract_pv_bot_tags(msg), ["cc"])

    def test_multiple_tags(self):
        msg = _make_msg(body="pv-bot: cc\npv-bot: nit")
        self.assertEqual(extract_pv_bot_tags(msg), ["cc", "nit"])

    def test_no_tags(self):
        msg = _make_msg(body="just normal email text")
        self.assertEqual(extract_pv_bot_tags(msg), [])

    def test_empty_body(self):
        msg = _make_msg(body="")
        self.assertEqual(extract_pv_bot_tags(msg), [])

    def test_pv_bot_empty_value(self):
        msg = _make_msg(body="pv-bot: ")
        self.assertEqual(extract_pv_bot_tags(msg), [])

    def test_pw_bot_not_matched(self):
        msg = _make_msg(body="pw-bot: cr")
        self.assertEqual(extract_pv_bot_tags(msg), [])


class TestSplitFrom(unittest.TestCase):
    def test_name_email(self):
        name, em = split_from("Alice Smith <alice@example.com>")
        self.assertEqual(name, "Alice Smith")
        self.assertEqual(em, "alice@example.com")

    def test_bare_email(self):
        name, em = split_from("bare@example.com")
        self.assertEqual(name, "")
        self.assertEqual(em, "bare@example.com")

    def test_angle_only(self):
        name, em = split_from("<bare@example.com>")
        self.assertEqual(name, "")
        self.assertEqual(em, "bare@example.com")

    def test_empty(self):
        name, em = split_from("")
        self.assertEqual(name, "")
        self.assertEqual(em, "")

    def test_none(self):
        name, em = split_from(None)
        self.assertEqual(name, "")
        self.assertEqual(em, "")


if __name__ == '__main__':
    unittest.main()
