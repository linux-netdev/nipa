# Container images for Netdev tests

The idea here is to have some container images to get an environment closed to
the CI one, to easily run the same tests as the ones executed on NIPA.

Instead of installing a bunch of (undocumented) dependences, and trying to guess
which versions are used on NIPA, these containers can be used.

These images are supposed to be easy to use and maintain. They are automatically
built and pushed to the GitHub registry each time the related files are
modified: <https://github.com/orgs/linux-netdev/packages>.

The Dockerfile are also kept simple to let more people modifying them if needed:
at the end it is just a bunch of commands someone would execute to install
programs on their side on Fedora.

## Usage

To use them, that's easy, either:

- Execute `docker/build/run.sh` (static tests) or `docker/selftests/run.sh`
  (runtime tests) with `--pull`, then execute the same commands as before, but
  from the containers.

- Use the same scripts followed by the same commands as before to execute them
  directly from the containers.

- Wrap commands, e.g. to always execute `make` and `vng` from the selftests
  container, create symlinks named after them, pointing to
  `docker/selftests/wrapper.sh`, in a directory placed first in `$PATH`:

  ```console
  $ mkdir -p ~/nipa-bin
  $ ln -s "${nipa}/docker/selftests/wrapper.sh" ~/nipa-bin/make
  $ ln -s "${nipa}/docker/selftests/wrapper.sh" ~/nipa-bin/vng
  $ export PATH="${HOME}/nipa-bin:${PATH}"
  ```

- Use Docker or Podman directly, e.g.

  ```console
  $ mkdir -p .ccache
  $ docker run \
    -v .ccache:/home/nipa/.ccache:rw \
    -v "${PWD}:${PWD}:rw" \
    -w "${PWD}" \
    -u "${RUID:-$(id -u)}:${RGID:-$(id -g)}" \
    --group-add "$(grep "^kvm:" /etc/group | cut -d: -f3)" \
    --rm \
    -i -t \
    --privileged \
    --pull always \
    ghcr.io/linux-netdev/nipa-selftests:latest \
    ## the rest of the command here
  ```

For more details about which commands to execute next, please check the wiki:
[Running Netdev CI tests locally](https://github.com/linux-netdev/nipa/wiki/Running-Netdev-CI-tests-locally).

## Images

In this repository, there are different images:

- `base`: a common base for the following ones, on top of Fedora, latest version
- `build`: containing dependencies for the static tests, to run `ingest_mdir.py`
- `selftests`: containing dependencies for the runtime tests: KSelftests, KUnit

Note: `debian` is a deprecated image to run the static tests. The Netdev CI is
using a Fedora base, so it is recommended to do the same.

## Build locally

If you prefer to build container images locally, you can use the
`docker/build.sh` script.
