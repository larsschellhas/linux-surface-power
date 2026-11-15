#!/bin/sh
# Summary of the long dry-run (acceptance test AC-L4) from systemd accounting and the journal. Runs as any user
# who may read the system journal (e.g. group adm/systemd-journal).
# AC-L4: no crash, no invalid core input except as documented, CPU < 0.5 %, memory < 50 MB.
U=surface-powerd-dryrun.service
show() { systemctl show -p "$1" --value "$U"; }

since=$(show ActiveEnterTimestamp)
mono=$(show ActiveEnterTimestampMonotonic)
now=$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)
cpu_ns=$(show CPUUsageNSec)
echo "== $U: $(show ActiveState)/$(show SubState) since $since, restarts $(show NRestarts)"
if [ -n "$mono" ] && [ "$mono" -gt 0 ] && [ "$cpu_ns" != "[not set]" ]; then
    awk -v c="$cpu_ns" -v s="$((now - mono / 1000000))" 'BEGIN { printf "   CPU %.0f s in %.1f h = %.3f %% of one core\n", c / 1e9, s / 3600, 100 * c / 1e9 / s }'
fi
mem=$(show MemoryCurrent); peak=$(show MemoryPeak)
awk -v m="$mem" -v p="$peak" 'BEGIN { printf "   memory now %.1f MB, peak %.1f MB\n", m / 1048576, p / 1048576 }'

echo "== journal since start, by priority (0-3 = error and worse, 4 = warning):"
journalctl -u "$U" --since "$since" -o json --output-fields=PRIORITY 2>/dev/null \
    | sed -n 's/.*"PRIORITY":"\([0-9]\)".*/\1/p' | sort | uniq -c
echo "== state changes, warnings and errors (last 30):"
journalctl -u "$U" --since "$since" -p warning --no-pager -o short-iso 2>/dev/null | tail -30
echo "== state changes (last 30):"
journalctl -u "$U" --since "$since" --no-pager -o short-iso 2>/dev/null | grep -E "state |resume|sleep" | tail -30
