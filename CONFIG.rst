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
