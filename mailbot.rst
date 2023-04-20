.. SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

=======
mailbot
=======

Mailbot performs actions based on commands hidden in emails
of authorized users.  Commands follow the::

  some-bot: command

pattern, currently patchwork and documentation quoting support
is planned. It's recommended (but not required) to place the
commands in the footer of an email. The footer delimiter is
two dashes followed by a space: '-- '.

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

will set the series state to "Changes Requested".

doc-bot
=======

**Work-in-progress** (pending getting an email account for the bot :().

``doc-bot`` groups documentation commands. Bot replies to the email
with the command quoting a specified section of kernel documentation.
It is intended to be used to quote process documentation at people
who have not read it.

TODO
====

 - read authorized users directly from MAINTAINERS?
 - auto-mark iwl-next patches as Awaiting Upstream
