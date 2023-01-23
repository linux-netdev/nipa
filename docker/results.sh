#!/bin/bash

set -euo pipefail

find nipa-run/results -name retcode | xargs grep . | awk 'BEGIN { FS=":" } { print $2 "\t" $1 }'
