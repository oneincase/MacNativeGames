#!/bin/sh
# Stability smoke test: boot the runtime N times for N seconds and count guest faults.
# usage: scripts/stability.sh [runs] [seconds] [extra env...]
set -u
WORK=${WORK:-/Volumes/bit2t/switch/totk/NIER_build}
RUNS=${1:-3}; SECS=${2:-13}; shift 2 2>/dev/null || true
fail=0
i=1
while [ $i -le $RUNS ]; do
  pkill -f "nier-native rewritten/v103 boot" 2>/dev/null; sleep 1
  log="$WORK/logs/stab-$i.log"
  env "$@" "$WORK/build/nier-native" rewritten/v103 boot >/dev/null 2>"$log" &
  pid=$!
  sleep "$SECS"
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
  n=$(grep -c 'fault=' "$log" 2>/dev/null || true)
  echo "run $i: faults=$n"
  [ "${n:-1}" = "0" ] || fail=1
  i=$((i+1))
done
pkill -f "nier-native rewritten/v103 boot" 2>/dev/null
[ $fail = 0 ] && echo "STABLE" || echo "UNSTABLE"
exit $fail
