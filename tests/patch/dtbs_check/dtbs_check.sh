#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright 2025-2026 NXP

HEAD=$(git rev-parse HEAD)
nproc=$(grep -c processor /proc/cpuinfo)
build_flags="-j $nproc"
rc=0

architectures=(
    "arc" \
    "arm" \
    "arm64" \
    "csky" \
    "loongarch" \
    "microblaze" \
    "mips" \
    "nios2" \
    "openrisc" \
    "powerpc" \
    "riscv" \
    "sh" \
    "xtensa" \
)

pr() {
    echo " ====== $* ======" | tee -a /dev/stderr
}

# "make dtbs" will fail on many archs during the syncconfig stage, where it
# tries to probe the cross-compiler version. We don't actually need any
# cross-compilation feature, the host gcc could in principle handle everything
# as long as we filter out arch-specific flags.
setup_shims() {
    local shims_dir="$1"

    mkdir -p "$shims_dir"

    cat > "$shims_dir/gcc" << 'EOF'
#!/bin/bash
args=()
skip_next=false
for arg in "$@"; do
  if [[ "$skip_next" == "true" ]]; then
    skip_next=false
    continue
  fi
  case "$arg" in
    -mdiv|-mno-stack-size|-mhard-float|-msoft-float|-mcpu=*|-march=*|-mtune=*|\
    -mmedium-calls|-mlock|-mswape|-munaligned-access|-mno-sdata|-mbig-endian|\
    -mabi=*|-mcmodel=*|-G|-mno-abicalls|-EB|0)
      ;;
    *)
      args+=("$arg")
      ;;
  esac
done
exec /usr/bin/gcc "${args[@]}"
EOF

    chmod +x "$shims_dir/gcc"

    # Special case for xtensa
    ln -s $(which gcc) "$shims_dir/xtensa_fsf-gcc"
    ln -s "$(which ld)" "$shims_dir/xtensa_fsf-ld"
}

prep_config() {
    local arch=$1
    local output_dir=$2
    local shims_dir=$3
    PATH="$shims_dir:$PATH" make ARCH=$arch O=$output_dir allmodconfig $build_flags
}

build() {
    local arch=$1
    local output_dir=$2
    local shims_dir=$3
    PATH="$shims_dir:$PATH" make -s ARCH=$arch O=$output_dir $build_flags DT_CHECKER_FLAGS=-m CHECK_DT_BINDING=y dtbs 2>&1
}

clean_build() {
    local output_dir=$1
    rm -rf "$output_dir"
}

schema_modified=false
if git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | \
    grep -q -E "^Documentation/devicetree/bindings/"
then
    schema_modified=true
fi

touched_archs=()
for arch in "${architectures[@]}"; do
    if git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | \
        grep -q -E "^arch/${arch}/boot/dts/"
    then
        touched_archs+=("$arch")
    fi
done

test_archs=()
if [ "$schema_modified" = true ]; then
    test_archs=("${architectures[@]}")
    echo "DT schema files modified, testing all architectures" >&"$DESC_FD"
elif [ ${#touched_archs[@]} -gt 0 ]; then
    test_archs=("${touched_archs[@]}")
    echo "DTS files touched for architectures: ${test_archs[*]}" >&"$DESC_FD"
else
    echo "No DT schema or DTS files touched, skip" >&"$DESC_FD"
    exit 0
fi

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~
echo "Now at:"
git log -1 --pretty='%h ("%s")' HEAD

# Set up compiler shims
shims_dir="$PWD/shims"
setup_shims "$shims_dir"

for arch in "${test_archs[@]}"; do
    output_dir="build_dtbs_check_${arch}/"

    tmpfile_o_raw=$(mktemp)
    tmpfile_n_raw=$(mktemp)
    tmp_new_issues=$(mktemp)

    pr "Checking $arch before the patch"
    git checkout -q HEAD~

    # Prepare config and run the check on the parent commit
    clean_build "$output_dir"
    prep_config "$arch" "$output_dir" "$shims_dir"
    (build "$arch" "$output_dir" "$shims_dir" | tee -a "$tmpfile_o_raw") || true

    # Sort the output
    sort "$tmpfile_o_raw" > "${tmpfile_o_raw}.sorted"
    mv "${tmpfile_o_raw}.sorted" "$tmpfile_o_raw"
    incumbent_total=$(wc -l < "$tmpfile_o_raw")

    pr "Checking $arch with the patch"
    git checkout -q "$HEAD"

    # Prepare config and run the check on the new commit
    clean_build "$output_dir"
    prep_config "$arch" "$output_dir" "$shims_dir"
    (build "$arch" "$output_dir" "$shims_dir" | tee -a "$tmpfile_n_raw") || true

    # Sort the output
    sort "$tmpfile_n_raw" > "${tmpfile_n_raw}.sorted"
    mv "${tmpfile_n_raw}.sorted" "$tmpfile_n_raw"
    current_total=$(wc -l < "$tmpfile_n_raw")

    # Compare the lists to find new and fixed issues
    # Use comm to find fixed issues (lines only in the old log, column 1).
    fixed_issues_count=$(comm -23 "$tmpfile_o_raw" "$tmpfile_n_raw" | wc -l)

    # Use comm to find new issues (lines only in the new log, column 2)
    # and save them for later display.
    comm -13 "$tmpfile_o_raw" "$tmpfile_n_raw" > "$tmp_new_issues"
    new_issues_count=$(wc -l < "$tmp_new_issues")

    echo "[$arch] Issues before: $incumbent_total, after: $current_total" \
        "(Fixed: $fixed_issues_count, New: $new_issues_count)" >&"$DESC_FD"

    if [ "$new_issues_count" -gt 0 ]; then
        echo "[$arch] New issues added:" 1>&2
        # Print the new issues we saved
        cat "$tmp_new_issues" 1>&2
        rc=1
    elif [ "$fixed_issues_count" -gt 0 ]; then
        echo "[$arch] Patch fixed $fixed_issues_count issue(s)." >&2
        # No new issues, and some were fixed. This is a success.
    fi

    rm "$tmpfile_o_raw" "$tmpfile_n_raw" "$tmp_new_issues"
done

# Clean up shims directory
rm -rf "$shims_dir"

exit $rc
