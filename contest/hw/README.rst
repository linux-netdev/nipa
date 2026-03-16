===============
NIPA contest HW
===============

This documentation covers the NIPA HW testing infra (which is deployed on
machines owned by netdev foundation).

This diagram shows the layout of the services within netdev foundation::

  -----------------    ---------------    ------------------
  |  control node |    | build node  |    |    Machine 1   |
  |   _________   |    |   ________  |    |  ___________   |
  |  / machine \  |    |  / hwksft \ |    | / hw-worker \  |
  |  \_control_/  |    |  \__NIC0__/ |    | \___________/  |
  |       ||      |    |   ________  |    |                |
  |   ____\/__    |    |  / hwksft \ |    | ======  ====== |
  |  /   DB   \   |    |  \__NIC1__/ |    | |NIC0|  |NIC1| |
  |  \________/   |    |   ________  |    |_||==||__||==||_|
  |_______________|    |  / hwksft \ |       \__/   /
                       |  \__NIC2__/ |             /
                       |_____________|        ____/
                                             /
                                           -||==||-----------
                                           | |NIC2|         |
                                           | ======         |
                                           |     Machine 2  |
                                           |  ___________   |
                                           | / hw-worker \  |
                                           | \___________/  |
                                           |________________|

Database
========

The HW system uses the following tables in the database.

machine_info
------------

Metadata about machines for NIC testing.

 - ID, auto int, pkey
 - name, varchar 128
 - mgmt ipaddr

machine_info_sec
----------------

Separate table with sensitive information about machines.

 - BMC ipaddr [sec]
 - BMC pass [sec]

nic_info
--------

Metadata about NICs in the test machines.

 - ID, auto int, pkey
 - machine ID, fkey (machine_info)
 - vendor, varchar 128
 - model, varchar 128
 - peer ID, fkey int (nic_info)
 - ifname, varchar 128
 - ip4addr, varchar 128
 - ip6addr, varchar 128

SOL
---

Serial logs storage.

 - ID, auto int, pkey
 - machine ID
 - ts, timestamp, usec
 - line, varchar 200
 - eol, bool

Lines longer than 200 will be broken up, eol is true on last chunk.

reservations
------------

Machine reservations.

 - ID, auto int, pkey
 - ts_start, timestamp, sec
 - ts_end, timestamp, sec
 - status, enum: ACTIVE, CLOSED, TIMEDOUT
 - metadata, text, flexible JSON-formatted metadata

reservation_machines
--------------------

Join table mapping reservations to machines. Needed to support atomic
multi-machine reservations (e.g. NIC + peer on different machines).

 - reservation_id, fkey (reservations)
 - machine_id, fkey (machine_info)

Machine control
===============

Service responsible for reserving machines and controlling them over BMC.
It uses ipmitool for most commands, and UDP socket to receive Serial-over-LAN.
The machine info is read from the ``machine_info`` table.

Each API endpoint takes a ``caller`` parameter to attribute calls to other
services for debug.

Service prints logs to stdout (available via journalctl).

APIs
----

get_machine_info
~~~~~~~~~~~~~~~~

Shows public info about the machine (from ``machine_info``,
_not_ ``machine_info_sec``).

This endpoint also adds to the output information about reservation.
Either who (``caller``) currently has the machine reserved, and when
that reservation started. Or if not reserved who was the last one
to reserve and when the reservation ended.

get_nic_info
~~~~~~~~~~~~

Shows info from ``nic_info``.

get_sol_logs
~~~~~~~~~~~~

Query logs. The endpoint reconstructs the full log lines using the ``eol``
field. The chunking is therefore not visible to the querier.

The querier is expected to pass in ``start_id`` to fetch logs from a specific
starting point. If no ``start_id`` is specified first / last set of lines
will be fetched, depending on ``sort``.

Each query returns ``last_id`` which lets querier ask for next lines.
Output does not contain IDs per line because if line is constructed from
multiple DB rows there would be multiple IDs per line.

in:
 - caller
 - machine_id
 - start_id, ID of the log line querier already seen
 - limit, number of log lines to fetch
 - sort, optional sort order (desc or asc), default asc
out:
 - machine_id
 - last_id
 - array of:
   - ts
   - line

power_cycle
~~~~~~~~~~~

Power cycle the machine using BMC.

This endpoint doesn't currently have any security checks.
Callers are trusted to never power cycle machines they don't have reserved.

in:
 - caller
 - machine_id
out:
 - code, int, 0 on success or non-zero on failure
 - status, string, "success" or error information

reserve
~~~~~~~

Reserve a group of machines. The reservation is atomic (all machines or none).
``timeout`` parameter sets the min refresh rate expected via the
``reservation_refresh`` endpoint.

``reservation_id`` will be present in the output only if reservation succeeded.
Otherwise caller should try again later. (Service does not maintain a queue
of outstanding waiters).

in:
 - caller
 - array of:
   - machine_id
out:
 - timeout
 - reservation_id

reservation_refresh
~~~~~~~~~~~~~~~~~~~

Refresh reservation. Each reservation automatically times out if not refreshed.

If refresh fails the caller must abort and stop touching the machine. Someone
else may own it.

in:
 - caller
 - reservation_id

reservation_close
~~~~~~~~~~~~~~~~~

Release machines in a reservation immediately.

in:
 - caller
 - reservation_id

Config
------

 - reservation timeout, seconds

CLI
---

The ``nipa-mctrl`` CLI (``/usr/local/bin/nipa-mctrl`` on ctrl) provides
command-line access to the machine_control API::

  nipa-mctrl machines            # list machines and health state
  nipa-mctrl nics                # list NICs
  nipa-mctrl sol --machine-id 1  # view SOL logs
  nipa-mctrl reserve --machine-ids 1,2  # reserve machines
  nipa-mctrl close --reservation-id 5   # release a reservation
  nipa-mctrl power-cycle --machine-id 1 # power cycle via BMC

Add ``--json`` for machine-parseable output. Defaults to
``http://localhost:5050``; override with ``--url`` or ``MC_URL`` env var.

In-memory state
---------------

machine_state
~~~~~~~~~~~~~

If the machine is not reserved service SSHs to it every 5min and
checks uptime and kernel version. If SSH fails machine state progresses
HEALTHY -> MISS_ONE -> MISS_TWO. After three missed checks the machine
is power-cycled via BMC (POWER_CYCLE_ISSUED). If the machine is still
down after power cycle, the miss counter restarts (MISS_ONE) and the
cycle repeats.

If the machine is RESERVED the machine state is just tracking last
refresh time to potentially time out the reservation.

Only machines in HEALTHY state can be reserved. If someone tries
to reserve a machine not in HEALTHY state we respond with try again.

Refreshed every 5min, and immediately after reservation is released.

 - machine ID
 - state, enum: RESERVED, HEALTHY, MISS_ONE, MISS_TWO, POWER_CYCLE_ISSUED
 - last_reservation: CLOSED, TIMEOUT
 - reservation_last_refresh
 - uptime
 - kernel version

Operation
---------

This service has three main responsibilities:
 1. collecting SOL logs
 2. managing reservations
 3. controlling the machines

The service discovers all machines using the ``machine_info`` table at startup.

SOL collection
~~~~~~~~~~~~~~

At startup the service spawns a persistent ``ipmitool sol activate``
session for each machine (using BMC credentials from ``machine_info_sec``).
Each session runs in its own thread, reading stdout and inserting lines
into the ``sol`` table. If a session drops it is automatically
reconnected after a short delay. Stale sessions are deactivated before
each new connection attempt.

Managing reservations
~~~~~~~~~~~~~~~~~~~~~

At startup service scans the ``reservations`` table to see if there are
any open reservations. If there are it adds them to the in-memory state.
The entries added from the SQL table at startup have the "last refresh"
time of "now" to give active owner time to ping us before timing out
the reservation.

After initialization the service listens for new reservations and
also counts down the timeouts. Reservation timeout should be read from
the config, the reservation owner should be told refresh time is half
of the reservation timeout.

When reservation closes or times out machine should be rebooted (via SSH,
and BMC if SSH fails).

Controlling machines
~~~~~~~~~~~~~~~~~~~~

This is a bit of a meta endpoint. For now it only allows the callers
to power cycle the machine via BMC in case SSH connectivity is lost.

hwksft
======

Testing service. There are two instances of this service per NIC.
One for "normal" kernel build and one for a debug kernel build.

Unlike ``vmksft-p`` is more of a management / orchestration service.
The task of executing the tests is delegated to ``hw-worker``.

Config
------

 - NIC ID to test against
 - information about the branch stream to follow (like vmksft-p.py)
 - path to extra kernel config (incl. the driver for the NIC in question)
 - reservation retry time (seconds)
 - max kexec boot timeout (seconds)
 - max test time (seconds)
 - crash wait time (seconds), how long to wait after a crash is detected
   in SOL logs and no new SOL output before power cycling
 - SOL poll interval (seconds), how often to check SOL logs for crashes

Operation
---------

Upon detection of a new testing branch (each step may fail, of course):

1. Build the kernel and ksft
2. Resolve which machines we need to reserve for the NIC ID - machine in which
   the NIC resides and the machine in which peer NIC resides if the peer ID
   is not the same as NIC ID (loopback)
3. Keep trying to reserve the machines.
   Note that reservation refresh calls are placed explicitly rather than
   handled by a separate thread to avoid hung runners from keeping the machine.
4. Deploy the test artifacts (kernel and ksft bundle) under
   ``/srv/hw-worker/tests/$reservation_id``
   Test artefacts must include correct config file for NIPA HW test runner
   (NETIF, LOCAL_V4, LOCAL_V6, REMOTE_V4, REMOTE_V6, REMOTE_TYPE, etc).
5. kexec the machine into the newly deployed kernel
6. Wait for machine to come back, and ``nipa-hw-worker`` service to exit,
   while refreshing the reservation. During this wait hwksft monitors
   SOL logs for kernel crashes (see `Crash recovery`_).
7. Copy back the outputs from ``/srv/hw-worker/results/$reservation_id/``
   into appropriate locations in local FS (again, mimicking the ``vmksft-p``
   layout if outputs and json files in separate directories).
   Tests that appear in ``.attempted`` but not in ``results.json`` are
   reported as failures with crash info.
8. Release the reservation.
9. Include the result entry in the manifest file and wait for next branch.

Crash recovery
--------------

When a test causes a kernel crash the machine may become unresponsive.
hwksft detects this and recovers automatically so remaining tests can
continue.

Detection: hwksft polls ``get_sol_logs`` from ``machine_control`` at
``sol_poll_interval`` (default 15s). If the SOL output contains crash
markers (``RIP:``, ``Call Trace:``, ``ref_tracker:``, or
``unreferenced object``) a crash is flagged.

After a crash is flagged there are two paths:

Self-reboot: if subsequent SOL output contains ``[    0.000000]`` (the
first line of a kernel boot log), the machine is already rebooting itself
(e.g. due to ``panic=N`` kernel parameter). In this case the power cycle
step is skipped — hwksft proceeds directly to waiting for SSH and
continuing the recovery sequence below.

Hung machine: if no new SOL output appears for ``crash_wait_time`` (default
120s) after the crash, the machine is assumed hung. hwksft power-cycles it
via the ``machine_control`` ``power_cycle`` API.

Recovery sequence (after self-reboot or power cycle):

1. Wait for SSH to become available (machine boots into default kernel).
2. kexec into the test kernel again.
3. hw-worker starts, checks ``.kernel-version`` against ``uname -r``.
   On the default kernel the version won't match — hw-worker exits.
   After kexec into the test kernel the version matches — hw-worker
   resumes testing. Tests listed in ``.attempted`` are skipped
   (they caused the crash) and recorded as failures.
4. hwksft continues the wait loop from step 6 above.

This cycle can repeat multiple times if different tests cause different
crashes. Each crash skips only the offending test.

State files on the test machine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following files under ``/srv/hw-worker/tests/$reservation_id/``
coordinate crash recovery between hwksft and hw-worker:

``.kernel-version``
  Written by hwksft at deploy time. Contains the output of
  ``make kernelversion``. hw-worker compares this against ``uname -r``
  to verify it booted the test kernel. Mismatched version means wrong
  kernel — exit without running tests.

``.attempted``
  JSON list of test names (``target/prog``) already attempted. Written
  with ``fsync`` **before** each test starts. On resume after a crash,
  tests in this list are skipped and reported as failures. This ensures
  a crashing test is never retried.

hw-worker
=========

NIPA's ``hw-worker`` is the actual service that executes the tests.
It's one-shot, on-boot service, so it should see that there are tests
that need to be run after kexec completes boot.

Operation
---------

1. Scan ``/srv/hw-worker/tests`` for outstanding tests. Only the newest one
   will be executed.
2. Read ``.kernel-version`` from the test directory and compare against
   ``uname -r``. If the running kernel doesn't match, this is a boot into
   the wrong kernel (e.g. default kernel after power cycle) — exit
   immediately.
3. Open ``/dev/kmsg`` and drain existing boot messages to
   ``results_dir/boot-dmesg``.
4. Run the tests. For each test:
    a. Check if test name is in ``.attempted`` — if so, skip (crash recovery).
    b. Write test name to ``.attempted`` + fsync before execution.
    c. Run via ``./run_kselftest.sh -t <target>:<test>`` (installed form).
    d. Capture stdout/stderr, save to ``results_dir/<idx>-<name>/``.
    e. Drain ``/dev/kmsg`` — if any dmesg output was produced during
       the test, save it to ``results_dir/<idx>-<name>/dmesg``.
    f. Save metadata to ``results_dir/<idx>-<name>/info`` (JSON).
5. Results are saved under ``/srv/hw-worker/results/$reservation_id/``.
   hw-worker does **not** determine pass/fail — that is done by hwksft
   when it copies back and parses the output files.
6. Service exits.

Output artifacts
----------------

hw-worker produces the following files under
``/srv/hw-worker/results/$reservation_id/``.  hwksft copies this tree
back and parses it to build the final result JSON.

::

  $reservation_id/
  ├── boot-dmesg                    # dmesg from boot until first test
  ├── 0-test_name/                  # per-test output directory
  │   ├── stdout                    # test stdout (KTAP/TAP output)
  │   ├── stderr                    # test stderr
  │   ├── info                      # JSON: {retcode, time, target, prog}
  │   └── dmesg                     # dmesg during this test (if any)
  ├── 1-another_test/
  │   ├── stdout
  │   ├── stderr
  │   ├── info
  │   └── dmesg
  └── ...

``info`` JSON fields:

``retcode``
  Exit code of ``run_kselftest.sh``.  0 = pass, 4 = skip, other = fail.

``time``
  Wall-clock seconds the test took (float).

``target``
  kselftest collection name (e.g. ``drivers/net/hw``).

``prog``
  Test program name within the collection (e.g. ``rss_drv.py``).
