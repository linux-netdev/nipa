# Netdev CI test instructions

This repository (NIPA) implements the netdev CI. Two distinct kinds of tests
are relevant when working here:

1. **Netdev CI checks** — the checks NIPA runs against *kernel* patches
   (builds, checkpatch, selftests, KUnit, etc.), which can be reproduced
   locally against a `$linux` tree (see the rest of this document).
2. **NIPA's own test suite** — unit tests and lint checks for this
   repository's Python code (see below).

## Netdev CI checks (kernel patches)

Use these instructions to reproduce the tests run by the netdev CI (NIPA)
before submitting kernel patches whenever possible. The CI runs:

- LLM reviews for each patch (Sashiko and Clashiko).
- Static checks for each patch, including builds, documentation checks,
  `checkpatch.pl`, and Python, shell, and YAML checks.
- Runtime tests in VMs, including kselftests and KUnit.

## LLM reviews

Sashiko can be run locally with different models. Follow the
[Sashiko installation instructions](https://github.com/sashiko-dev/sashiko/#installation):
configure the model, then run:

```console
sashiko init
sashiko review
```

Clashiko uses Sashiko with multiple models for cross-reviews. It is available
at <https://netdev-ai.bots.linux.dev/sashiko>.


## Static tests

Static tests are implemented by scripts in the
[tests directory](https://github.com/linux-netdev/nipa/tree/main/tests).
Run them against a patch series with `ingest_mdir.py`:

1. Export the patches, for example with `b4 send -o (...)` or
   `git format-patch -o (...)`.
2. Check out a branch based on `net-next` (or `net`) in the Linux tree.
3. Run the ingestion script from this repository.

Example:

```console
## Export patches
$ cd $linux                          # your kernel source dir
$ b4 prep --set-prefixes 'net-next'  # or net
$ b4 send -o /tmp/my-series

## Create a branch on top of net-next (or net)
$ git switch -c test <base-branch>  # e.g. net-next or net

## Execute the tests; this can take a while
$ cd $nipa                           # the source dir of this repo
$ ./docker/build/run.sh --pull ./ingest_mdir.py \
        --mdir /tmp/my-series --tree $linux --result-dir out
```

Images can be built locally with `./docker/build.sh`. When using `./docker/build/run.sh`, `--pull` downloads the
latest image. Docker is shown above, but Podman can be used instead.

Also, tests can be disabled with `./ingest_mdir.py --disable-test (...)`
depending on the modified code, e.g. no need to build the kernel when only
modifying the selftests. Check `./ingest_mdir.py --list-tests`.

## KUnit

See the [KUnit instructions](https://docs.kernel.org/dev-tools/kunit/start.html).
Run all KUnit tests on x86_64 directly in the Linux tree from the selftests
container:

```console
$ cd $linux
$ $nipa/docker/selftests/run.sh --pull \
        ./tools/testing/kunit/kunit.py run --alltests --arch=x86_64
```

## Net selftests

The CI runs selftests in the latest Fedora environment inside lightweight VMs using
[`virtme-ng`](https://github.com/arighi/virtme-ng). The recommended local
workflow is to run `vng`, `make`, and `qemu` commands through the
`nipa-selftests` container:

```console
$ cd $linux
$ alias run="${nipa}/docker/selftests/run.sh"
$ docker pull ghcr.io/linux-netdev/nipa-selftests:latest

$ TARGETS=(net net/af_unix net/forwarding net/hsr net/mptcp net/netfilter \
    net/openvswitch net/ovpn net/packetdrill net/ppp net/rds net/tcp_ao nci)
$ for target in "${TARGETS[@]}"; do
    rm -f .config
    run vng --build --force --config "tools/testing/selftests/${target}/config"  # add '--config kernel/configs/debug.config' to validate with a debug kernel
    run make headers
    run make -C tools/testing/selftests TARGETS="${target}"
    run sudo vng --run . --user root -a mitigations=off --rw \
        --cpus 4 --network loop -- \
        make -C tools/testing/selftests TARGETS="${target}" run_tests
        # the output is TAP parsable to find failed and skipped tests
  done
```

Adapt `TARGETS` to the modified code. Driver-related targets can also be tested:

```text
drivers/net drivers/net/bonding drivers/net/hw drivers/net/netconsole
drivers/net/netdevsim drivers/net/team drivers/net/virtio_net
```

Check for:

- failed tests: `not ok (...)`
- skipped ones: `ok (...) # SKIP`
- any type of error in the tests
- or reported by the kernel: `Call Trace:`, timeout, unexpected stop
- with and without a debug kernel config, adding this to the `vng --build`
  command: `--config kernel/configs/debug.config`
- with a debug kernel config, check for leaks: scan
  (`echo scan > /sys/kernel/debug/kmemleak`), wait 5 seconds, scan again, check
  for leaks (`cat /sys/kernel/debug/kmemleak`)

For additional details and troubleshooting tips, see the
[netdev selftests CI guide](https://github.com/linux-netdev/nipa/wiki/How-to-run-netdev-selftests-CI-style).

## NIPA's own tests

Run before committing changes to this repository's Python code.

Unit tests (`unittest`-based, no `pytest` dependency required):

```console
$ python3 -m unittest discover -s contest/hw/tests -p "test_*.py"
$ python3 -m unittest discover -s ml-agent/tests -p "test_*.py"
```

Syntax check used by [.github/workflows/lint.yml](.github/workflows/lint.yml)
(runs on every changed `.py` file):

```console
$ python3 -m py_compile $(git ls-files '*.py')
```

This repository also follows the style defined in
[.style.yapf](.style.yapf) (not currently enforced in CI, but recommended
before submitting changes):

```console
$ pip install yapf
$ yapf --diff --style .style.yapf <changed_file>.py
```
