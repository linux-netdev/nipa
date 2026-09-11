Config syntax
~~~~~~~~~~~~~

This document describes the fields of the config file and their meaning.

poller
======

Section configuring patch ingest.

recheck_period
--------------

During normal operation poller fetches only the new patches - patches which
were sent since the previous check (minus 10 minutes to account for email lag).

To catch patches which got stuck in the email systems for longer, or got sent with
a date in the past poller will periodically scan patchwork looking back further into
the past.

``recheck_period`` defines the period of the long scans in hours.

recheck_lookback
----------------

Defines the length of the long history scan, see ``recheck_period``.

tree_update_period
------------------

Tests run in per-worker work trees, which are reset before every test.
The main tree is only reset when the poller has to guess which tree a series
targets, so it can go stale for a long time. Since misbehaving Makefiles
sometimes reach into the main tree instead of the work tree, the poller
refreshes the main trees periodically.

``tree_update_period`` defines the period of those refreshes in minutes
(default: 30).
