.. SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

=======
mailbot
=======

Mailbot performs actions based on commands hidden in emails
of authorized users.  Commands follow the::

  some-bot: command

pattern, currently the main use is updating patch state in patchwork.
mailbot commands are typically placed in the footer of an email,
but it's not a requirement and bot will find the commands anywhere
in the body.
The footer delimiter is two dashes followed by a space: '-- '.

pw-bot
======

``pw-bot`` groups patchwork commands. Currently only commands
to change patch state are supported. Please see the ``pw_act_map``
dictionary in ``mailbot.py`` for an up-to-date list of supported
states and their shortcuts / aliases.

Patchwork bot changes the state of the **entire series** whenever
a command is sent in discussion of any of the patches or the cover
letter.

Example::

  pw-bot: cr

or::

  pw-bot: changes-requested

will set the series state to "Changes Requested".

Authorization
-------------

Access to patchwork commands is based on authorship and maintainership.
That is to say that the author can always rescind their own series,
and maintainers (according to the MAINTAINERS file) can change the state
of the series at will. See also:

https://www.kernel.org/doc/html/next/process/maintainer-netdev.html#updating-patch-status

Other automation
================

``mailbot`` also performs other email-based automation not based
on explicit commands.

error bot automatic update
--------------------------

Whenever patch series gets a response from an error-reporting bot
(e.g. kbuild bot) the series will get marked as 'Changes Requested'.

TODO
====

 - auto-mark iwl-next patches as Awaiting Upstream
 - support marking PRs (they have no series), incl. PR + series posts
