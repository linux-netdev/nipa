==========================
Local nipa run with Docker
==========================

Nipa already has support to run the tests locally (via
`ingest_mdir.py`), but this is a bit complicated to set up, therefore
we have an oven-ready solution in this directory that runs everything
for you in Docker.

=====
Usage
=====

Generate your patch (or patches) with `git format-patch`, do not
forget to use the correct `--to` and `--cc` flags, as these will be
already tested in the `cc_maintainers` test.

You should also use the correct `--subject-prefix` (`PATCH net` or
`PATCH net-next`), but please pay extra care, as currently the
`tree_selection` check is not working with docker, because of the
limitation of `ingest_mdir.py`. (XXX, we should really fix this.)

Make sure that in your kernel git tree, you have a branch called
`nipa-local`, the automation will use this as the `--tree-branch`
argument for `ingest_mdir.py`, so this tip has to point to the git
commit where your supplied patches should be applied.

Copy `config.dist` to `config` and change the variables.

Run with `./run.sh`.

Output is on standard out, detailed logs are saved in `./nipa-run`.
