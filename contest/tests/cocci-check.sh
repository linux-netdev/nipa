#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

# rev-parse wants the branch with remote path
full_branch=$(git branch -a --list "*$BRANCH" | tail -1)
branch_rev=$(git rev-parse $full_branch)
range="$BASE..$branch_rev"

SPFLAGS="--use-patch-diff $range"

out_o=$RESULTS_DIR/old
out_n=$RESULTS_DIR/new
out_of=$RESULTS_DIR/old-filtered
out_nf=$RESULTS_DIR/new-filtered
rc=0

clean_up_output() {
    local file=$1

    # coccicheck produces this warning for every spatch run
    sed -i '/^grep: warning: + at start of expression$/d' $file

    # remove the command lines
    sed -i '/^\/usr\/local\/bin\/spatch -D report /d' $file

    # if files are removed or added cocci will fail in pre- or post- run
    sed -i '/^EXN: .*No such file or directory/d' $file
    sed -i '/^EXN: Coccinelle_modules.Common.Timeout /d' $file
    sed -i '/An error occurred when attempting /d' $file
}

# Figure out the number of physical cores, save 8 or half for other stuff
THREADS_PER_CORE=$(LANG=C lscpu | grep "Thread(s) per core: " | tr -cd "[:digit:]")
NPROC=$(getconf _NPROCESSORS_ONLN)
JOBS=$((NPROC / THREADS_PER_CORE))

if [ $JOBS -gt 16 ]; then
    JOBS=$((JOBS - 8))
else
    JOBS=$((JOBS / 2))
fi

echo " === Start ==="
echo "Base: $BASE"
echo "Branch: $BRANCH ($branch_rev)"
echo "Jobs: $JOBS"
echo

echo " === Waiting for loadavg to die down ==="
while true; do
    # Sleep first to make sure others get a chance to start
    sleep 120

    load=$(cat /proc/loadavg | sed -e 's/\([0-9.]\) .*/\1/;s/\.//;s/^0*//')
    [ $load -lt 800 ] && break
done

echo "Starting at $(date)"
echo

IGNORED=( scripts/coccinelle/misc/minmax.cocci )
for ign_file in ${IGNORED[@]}; do
    echo "Ignoring " $ign_file
    mv $ign_file $ign_file.ignore
done

echo " === Checking the base tree ==="
git checkout -q $BASE
make coccicheck MODE=report J=$JOBS SPFLAGS="$SPFLAGS" > $out_o || exit 1

echo " === Building the new tree ==="
git checkout -q $BRANCH
make coccicheck MODE=report J=$JOBS SPFLAGS="$SPFLAGS" > $out_n || exit 1

dirty=( $(grep -c . $out_o) $(grep -i -c "warn" $out_o) $(grep -i -c "error" $out_o)
	$(grep -c . $out_n) $(grep -i -c "warn" $out_n) $(grep -i -c "error" $out_n)
      )

cp $out_o $out_of
cp $out_n $out_nf

clean_up_output $out_of
clean_up_output $out_nf

incumbent=( $(grep -c . $out_of)
	    $(grep -i -c "warn" $out_of)
	    $(grep -i -c "error" $out_of) )
current=( $(grep -c . $out_nf)
	  $(grep -i -c "warn" $out_nf)
	  $(grep -i -c "error" $out_nf) )

if [ ${current[2]} -gt ${incumbent[2]} ]; then
  echo "New errors added" 1>&2
  diff -U 0 $out_of $out_nf 1>&2
  rc=1
elif [ ${current[1]} -gt ${incumbent[1]} ]; then
  echo "New warnings added" 1>&2
  diff -U 0 $out_of $out_nf 1>&2
  rc=1
elif [ ${current[0]} -gt ${incumbent[0]} ]; then
  echo "New output added" 1>&2
  diff -U 0 $out_of $out_nf 1>&2
  rc=5
fi

if [ $rc -ne 0 ]; then
  echo "Per-file breakdown" 1>&2
  tmpfile_fo=$(mktemp)
  tmpfile_fn=$(mktemp)

  grep -i "^$PWD" $out_of | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fo
  grep -i "^$PWD" $out_nf | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fn

  diff -U 0 $tmpfile_fo $tmpfile_fn 1>&2
  rm $tmpfile_fo $tmpfile_fn
fi

for ign_file in ${IGNORED[@]}; do
    echo "Un-ignoring " $ign_file
    mv $ign_file.ignore $ign_file
done

echo
echo " === Summary === "
echo "Incumbent: ${incumbent[@]}"
echo "Current:   ${current[@]}"
echo "Dirty counts: ${dirty[@]}"
echo "Result: $rc"

exit $rc
