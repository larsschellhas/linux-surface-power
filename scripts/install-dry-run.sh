#!/bin/sh
# Installs the current checkout for a long dry-run (acceptance test AC-L4) and starts it.
#   - a root-owned copy in a venv under /opt/surface-power (the service never runs code from a user's home);
#   - surface-powerd-dryrun.service (dry-run, thermald keeps running) and the sleep hook;
#   - loads surface_aggregator_cdev at boot (EC power data, interim until a kernel client exists);
#   - data: status in /run/surface-power, time series in /var/lib/surface-power.
# Usage (as root):  sh scripts/install-dry-run.sh            install or update, then (re)start
#                   sh scripts/install-dry-run.sh --remove   stop and remove everything this script installed
set -eu
PREFIX=/opt/surface-power
UNIT=/etc/systemd/system/surface-powerd-dryrun.service
HOOK=/usr/lib/systemd/system-sleep/surface-power
# Interim (NFR-11): the EC power data come through the SSAM debug device until there is a kernel client.
MODLOAD=/etc/modules-load.d/surface-power-cdev.conf
REPO=$(cd "$(dirname "$0")/.." && pwd)

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

if [ "${1:-}" = "--remove" ]; then
    systemctl disable --now surface-powerd-dryrun.service 2>/dev/null || true
    rm -f "$UNIT" "$HOOK" "$MODLOAD"
    systemctl daemon-reload
    rm -rf "$PREFIX"
    echo "removed (the time series in /var/lib/surface-power is kept)"
    exit 0
fi

# Fresh venv on every install, so that no stale files remain. System site packages give setuptools for an offline build.
rm -rf "$PREFIX"
python3 -m venv --system-site-packages "$PREFIX"
"$PREFIX/bin/pip" install --quiet --no-build-isolation --no-deps --no-cache-dir "$REPO"
# pip leaves a build directory in the checkout; remove it so the checkout stays clean.
rm -rf "$REPO/build" "$REPO"/src/*.egg-info

sed "s|/usr/bin/surface-powerd|$PREFIX/bin/surface-powerd|" "$REPO/packaging/systemd/surface-powerd-dryrun.service" > "$UNIT"
install -m 0755 "$REPO/packaging/system-sleep/surface-power" "$HOOK"
echo surface_aggregator_cdev > "$MODLOAD"
modprobe surface_aggregator_cdev
systemctl daemon-reload
systemctl enable surface-powerd-dryrun.service
systemctl restart surface-powerd-dryrun.service
sleep 5
systemctl --no-pager --lines=8 status surface-powerd-dryrun.service || true
echo "installed from $(git -C "$REPO" describe --always --dirty 2>/dev/null || echo "$REPO")"
