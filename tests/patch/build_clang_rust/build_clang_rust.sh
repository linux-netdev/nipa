#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

cc=clang
output_dir=build_clang_rust/
ncpu=$(grep -c processor /proc/cpuinfo)
build_flags="-Oline -j $ncpu W=1"
rc=0

prep_config() {
    make LLVM=1 O=$output_dir allmodconfig $build_flags

    # Disable -Werror so we get to see all the errors
    ./scripts/config --file $output_dir/.config -d werror

    # KVM has its own WERROR control, and it currently does generate errors!
    ./scripts/config --file $output_dir/.config -d kvm_werror

    # Unclear if this is related to Rust but we seem to get key generation
    # issues with SHA1 on Fedora 41. Switch to SHA256.
    ./scripts/config --file $output_dir/.config -d module_sig_sha1
    ./scripts/config --file $output_dir/.config -e module_sig_sha256
    ./scripts/config --file $output_dir/.config --set-str module_sig_hash sha256

    # allmodconfig is not sufficient to get Rust support enabled. So
    # flip some options.

    # Module versioning does not work because Rust symbols are too long
    # In order to disable that, RANDSTRUCT_FULL needs disabling
    ./scripts/config --file $output_dir/.config -d randstruct_full
    ./scripts/config --file $output_dir/.config -e randstruct_none
    ./scripts/config --file $output_dir/.config -d modversions
    # Rust also seems currently incompatible with CFI (Rust 1.83)
    ./scripts/config --file $output_dir/.config -d cfi_clang

    # Now Rust can be enabled
    ./scripts/config --file $output_dir/.config -e rust

    # Rust currently requires all dependencies are built in, so make
    # phylib built in.
    ./scripts/config --file $output_dir/.config -e phylib

    # And enable the Rust binding on phylib
    ./scripts/config --file $output_dir/.config -e rust_phylib_abstractions

    # Lastly, enable the Rust PHY driver for the AX88796B
    ./scripts/config --file $output_dir/.config -e ax88796b_rust_phy

    # Setting options above enabled some new options. Set them to their
    # defaults
    make LLVM=1 O=$output_dir olddefconfig $build_flags

    # And verify rust is now actually enabled in the configuration.
    config_rust=$(./scripts/config --file $output_dir/.config --state CONFIG_RUST)

    if [ $config_rust != "y" ]; then
	echo "CONFIG_RUST not set in generated config" >& $DESC_FD
	exit 1
    fi
}

if [ ${DESC_FD}x == x ]; then
    DESC_FD=/dev/stderr
fi

files=$(git show --pretty="" --name-only  -- *.rs)
if [ -z "$files" ]; then
    echo "No Rust files in patch. Skipping build" >& $DESC_FD
    exit 0
fi

# Check we have a Rust toolchain the kernel is happy with. It changes
# from release to release.
if ! make LLVM=1 rustavailable; then
    exit 1
fi

echo "Using $build_flags redirect to $tmpfile_o and $tmpfile_n"
echo "LLVM=1 cc=$cc"
$cc --version | head -n1
rustc --version

HEAD=$(git rev-parse HEAD)

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~

echo "Baseline building the tree"

prep_config
make LLVM=1 O=$output_dir $build_flags

tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)

git checkout -q HEAD~

echo "Building the tree before the patch"

prep_config
make LLVM=1 O=$output_dir $build_flags 2> >(tee $tmpfile_o >&2)
incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)

echo "Building the tree with the patch"

git checkout -q $HEAD

prep_config
make LLVM=1 O=$output_dir $build_flags -j $ncpu 2> >(tee $tmpfile_n >&2) || rc=1

current=$(grep -i -c "\(warn\|error\)" $tmpfile_n)

echo "Errors and warnings before: $incumbent this patch: $current" >&$DESC_FD

if [ $current -gt $incumbent ]; then
  echo "New errors added" 1>&2
  diff -U 0 $tmpfile_o $tmpfile_n 1>&2

  echo "Per-file breakdown" 1>&2
  tmpfile_fo=$(mktemp)
  tmpfile_fn=$(mktemp)

  grep -i "\(warn\|error\)" $tmpfile_o | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fo
  grep -i "\(warn\|error\)" $tmpfile_n | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fn

  diff -U 0 $tmpfile_fo $tmpfile_fn 1>&2
  rm $tmpfile_fo $tmpfile_fn

  rc=1
fi

rm $tmpfile_o $tmpfile_n

exit $rc
