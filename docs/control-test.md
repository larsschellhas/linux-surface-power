# Control test: the daemon controls your power limits

A first supervised run of about 30 minutes, with the daemon writing PL1 and PL2 through our kernel module, the way Windows does.

**Who:** devices whose three module steps and first write passed in `surface-power-check` **and** that are in the module's device table (today
the Pro 10 for Business `…_2079` and the Pro 8 `…_1983`). The Pro 8 cannot run it yet: its kernel has no temperature sensor node, so the daemon
stays in dry-run. A Pro 9 can join once its module steps have passed and we have added it to the table.

**Please read first.** This is experimental software. Our safety simulation of the device profiles is not built yet. The daemon can set limits
**higher** than your firmware does. Save your work, stay at the machine and use the charger. A reboot brings back the firmware values.

All commands run in the checkout.

## 1. Prepare
```bash
git pull
```
Stop everything else that writes power limits, your own services included:
```bash
sudo systemctl stop thermald
```
Only if you use tuned (it comes back by itself unless masked):
```bash
sudo systemctl mask --runtime --now tuned tuned-ppd
```
```bash
sudo modprobe -a msr surface_aggregator_cdev
```

## 2. Load the module with write permission
```bash
rm -rf /tmp/srm && cp -r kernel/surface-rapl-mmio /tmp/srm && make -C /tmp/srm
```
Only if `cat /sys/module/module/parameters/sig_enforce` shows `Y`: sign it with your enrolled key (the check names its folder; this is the
usual one on Debian-based systems):
```bash
sudo /lib/modules/$(uname -r)/build/scripts/sign-file sha512 /var/lib/shim-signed/mok/MOK.priv /var/lib/shim-signed/mok/MOK.der /tmp/srm/surface_rapl_mmio.ko
```
```bash
sudo insmod /tmp/srm/surface_rapl_mmio.ko allow_write=1
```

## 3. Start the daemon
It runs as a temporary service: closing the terminal does not stop it, systemd puts the firmware values back if it dies, and nothing is left
after a reboot.
```bash
sudo systemd-run --unit=surface-powerd -p Type=notify -p NotifyAccess=main -p WatchdogSec=30 -p ExecStopPost="/usr/bin/env python3 -B -m surface_power.tools.reset" -E PYTHONPATH=$PWD/src -E PYTHONDONTWRITEBYTECODE=1 /usr/bin/env python3 -m surface_power.daemon --control
```
```bash
journalctl -fu surface-powerd
```
The first lines must say `mode control` and `PL path mmio`. If they say `dry-run` or `PL path msr`, stop (step 5) and post the log.

## 4. Use the machine for about 30 minutes
Some minutes idle, some under load, and **one suspend** of at least a minute. After the resume, with the daemon still running:
```bash
sudo python3 -B scripts/mmio-pl-read.py | tee after-resume.txt
```
```bash
cp /run/surface-power/status.json control-status.json
```
Stop early if fans or temperatures behave oddly, or the log shows `emergency` or `yielded`.

## 5. Stop
```bash
sudo systemctl stop surface-powerd
```
The log must end with `stopped; firmware values restored`.
```bash
journalctl -u surface-powerd -b -o cat > control-test.log
```
```bash
sudo rmmod surface_rapl_mmio
```
```bash
sudo python3 -B scripts/mmio-pl-read.py | tee after-stop.txt
```
Then start thermald again (`sudo systemctl start thermald`; with tuned: `sudo systemctl unmask --runtime tuned tuned-ppd` and
`sudo systemctl start tuned tuned-ppd`) and your own services.

**If something went wrong** and the log has no `firmware values restored`: reboot.

## 6. Post
In your device issue: `control-test.log`, `control-status.json`, `after-resume.txt`, `after-stop.txt`, and anything you noticed. Please look
through the files first; issues are public.
