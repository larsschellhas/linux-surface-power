# Surface Book 3: detaching the base, what Windows and Linux do, and what the daemon sees

24.11.2026, from work package B13 (raw note `raw/arbeitsordner/b13-basis-sensoren-2026-11-24.md`, measurements `raw/arbeitsordner/b13-messung-2026-11/`). Applies to the
Surface Book 3 15" (reference device, firmware and drivers as on this machine). Nothing here is transferred to the 13" or to other devices without its own evidence.

## What reaches the base sensors in the blob
- `2E8B14C2/605` (KIP sensor 5) reaches only `110DF2A4/1` and `/2` (thermal actions O_HOT, O_CRT). `2E8B14C2/609` (KIP sensor 9) reaches the same two outputs and the
  dGPU limit `0B7B5350/201` (controller Id 4 and a min block). Neither reaches PL1, PL2, PL3, PL4 or psys [C, `engine.graph.outputs_reached_by` on
  `sb3-15in-39.0.1.4.csv`; the 13" blob `sb3-13in-39.0.1.7.csv` has the same structure].
- Invalid inputs act per block: controller Id 4 goes OFF with its `U_off`, the window comparators give 1 (no request), the min block ignores the missing node. The engine's
  output stayed at its table values during the incident of 19.11.2026 [C `controller.md` 4.2, `engine.md` 4.7; M series of 19.11.].

## What Windows does (SB3 driver package, DTX 3.85.139.0, HotPlug 3.204.20.0)
- There is no device-wide fallback in the SMF core. A client whose data request fails has all its channels taken offline together (`SmfCore_DataGet_Callback … ChannelsToCoreOffline`);
  a single failed or out-of-range sensor read sends no update, so the core keeps the last value [C, decompiled core and `SurfaceSmfBaseThermalClient.sys`].
- The base thermal client exists only while the base is attached: `SurfaceHotPlug.sys` creates the child device `{C65C8174-…}\SurfaceSmfKipThermalClient` ("Surface SMF Base
  Thermal Client") when the SAM thermal category attaches and removes it on detach, like the battery, HID and fingerprint children [C, `SurfaceHotPlug.sys` (decompiled, function in the raw note `b13-basis-sensoren-2026-11-24.md` 12); INF of the client].
  The `SurfaceHotPlug.sys` of the OEM package has the same sha256 (`25d8fab2…`) as the one decompiled here. That the DTX package is the one on the maintainer's SB3 is [H].
- Pressing the detach button (driver `SurfaceDtxDriver.dll`, service `SurfaceDtxService.exe`, app `SurfaceDTX.exe`) [C, decompiled; the order of the steps comes from the trace names and the call sites]:
  1. the EC sends the detach request; the app learns of it and, if the dGPU has no user processes, sends `Open` with a 5 s timeout; with processes it lists them ("Detach anyway"
     kills them with `Process.Kill`, a second button press cancels);
  2. the driver sends the service a command with the dGPU's hardware ID: command 3 = `CM_Query_And_Remove_SubTreeW` (silent removal of the device subtree), and waits up to 5 s,
     polling once per second, for the dGPU to be gone ("Dtx removed DGpu successfully" or "failed", the failure is only logged);
  3. only then it sends the SAM request for the latch (TC `0x11`, CID `9`);
  4. when the base is attached again the service brings the dGPU back (command 4: wait until the devnode exists, `CM_Setup_DevNode`, `CM_Reenumerate_DevNode`, up to 30 s on the driver side).
- `SurfaceHotPlug.sys` additionally reads two GPIOs (dGPU present, dGPU power) and passes them to the firmware with `_DSM` functions 4 and 5. The SB3 DSDT has the receiver: device `\_SB.SHPS`
  (`MSHW0153`), UUID `5515a847-ed55-4b27-8352-cd320e10360a`; function 5 with 0 runs `HGOF` (dGPU power off through GPIOs), with 1 `HGON`, then a device check on root port `RP13` [C, public dump
  `acpidumps/surface_book_3/dsdt.dsl`; the firmware of a tester may differ [H]]. Linux has the same in `surface_hotplug.ko` (in-tree, bound to `MSHW0153:00` here, strings for base presence,
  device power and device presence) [M]. Both react after the detach, neither frees the dGPU beforehand.

## What Linux does
- `surface_aggregator_hub` removes all children of the base hub when the base is disconnected and registers the children it knows from the registry 250 ms after reconnect
  [C, `surface_aggregator_hub.c` of the out-of-tree module, 12/2023]. The registry of linux-surface (`v7.3-surface`, from 23.11.2026; #190 and #195 merged 17.11.2026) has
  `ssam_node_tmp_sensors_sb3base`, `ssam_node_fan_speed_sb3base_1/_2` and the DPTF battery nodes [C, GitHub file].
- **Kernel 7.3.4-2-surface (registry module of 16.11.2026) does not have them** [M, `strings`]. On this machine the base sensors, base fans and EC power data came from three
  DKMS prototypes (`sb3-ssam-sensors`, `sb3-surface-hwmon-kip`, `sb3-surface-power-dptf`) that shadowed the in-tree modules; the first one never re-created the base nodes
  after a detach (its source says so) [M, `modinfo -n`, `dkms status`]. **Kernel 7.3.6-1-surface** has the nodes; with the prototypes removed the base thermal device was back **about 10 s** after
  re-attaching, without any module reload [M, 24.11.2026 18:05].
- The hwmon index changes: before the detach the base thermal sensor was `hwmon5`, afterwards `hwmon11`, and `hwmon5` belonged to a fan (no `temp9_input`). On 7.3.4-2 the index of the
  base thermal sensor, `hwmon2`, was taken by `BAT2`'s hwmon [M, both runs]. A daemon that resolves hwmon paths once at start reads the wrong device or none after a detach.
- The detach is governed by the EC; `surface_dtx` only relays events and commands. Without a user-space answer the EC opens an unlocked latch after a timeout; the latch can be
  locked to keep the dGPU from being hot-unplugged [C, `surface-dtx.txt` of the module, marked reverse engineered there].
- The maintainer's handler `/etc/surface-dtx/detach.sh` checks that no process holds the dGPU and that its `power_state` is `D3cold`, unmounts USB disks, then confirms. It does not unbind the
  NVIDIA driver or remove the PCI device; `attach.sh` is empty [M]. With this handler, on 7.3.4-2 and on 7.3.6-1, the detach was followed by `pciehp: Card not present`,
  `NVRM Xid 79` (GPU has fallen off the bus) and `Xid 154` (GPU reset required), and `nvidia` bound again after the re-attach (19.11. only reported "needs a reset", 24.11. twice
  `Loading driver`) [M]. Whether the GPU is usable afterwards was not tested; whether removing the PCI device before the latch opens avoids the Xid was not tested (variant B, prepared in
  `raw/arbeitsordner/b13-messung-2026-11/`).

## What the daemon does since B13 (25.11.2026, commit 22d4f62; tested on a fake sysfs and by a real detach)
- The base channels 601-699 are looked up again when their device is gone (`ENOENT`, `ENODEV`, `ENXIO`, another device or name under the cached index) or missing, at most
  every 5 s; only a `surface_thermal` device with SSAM category `03` and target `02` is taken. A base channel without its device is **absent**: invalid (block defaults),
  not counted for the emergency rule; a running emergency ends 10 s later. A read error of a present device still counts as lost (`supervisor.md` 4.1).
- The dGPU stand-in waits for an absent 609 at start and starts when it is back.
- Real detach, 25.11.2026: (7.3.6-1-surface, daemon in control, raw `b13-messung-2026-11/results/b13-check-20261125-0959.txt`): 605/609 absent at 10:00:17, back at 10:00:41 under `hwmon8`, 90 of 90 samples in control, no emergency [M]. The detach again gave `Xid 79` and `Xid 154` for the dGPU (not part of B13).

## What the daemon did before B13
- `HwmonTemperatures` resolves its paths once at start (`adapters/inputs.py`). After a detach 605/609 stay invalid, and after 60 s the supervisor's rule "controller inputs invalid for 60 s"
  puts the daemon into `emergency` (firmware values for every output, dGPU cap released; the thermal actions stay armed) [M: 19.11.2026 for 39 min in control; 24.11.2026 on 7.3.4-2
  and on 7.3.6-1 in dry-run, 60 s after the detach]. Inputs unavailable at start are excluded, inputs lost while running are not.
- `BAT2` is read through its fixed `power_supply` path every second and returns by itself; EC power inputs (`28B7BD85/…`) do not count for the rule [C, `daemon.py` `_core_invalid`].

## Traps found on the way
- DKMS packages under `updates/dkms/` shadow the in-tree modules of the same name. Check with `modinfo -n <module>`; after removing a package run `depmod -a <kernel>` and
  `update-initramfs -u -k <kernel>` (the prototype registry was inside the initramfs) and remove `/etc/modules-load.d/sb3-ssam-sensors.conf` [M].
- `apt update` printed only `OK … InRelease` while the local `Packages` list of `pkg.surfacelinux.com` was from 17.11.2026 and the server had 7.3.5-1 and 7.3.6-1: the `InRelease` was already
  current, so apt did not fetch the list again. Deleting the two files `/var/lib/apt/lists/pkg.surfacelinux.com_debian_dists_release_{InRelease,main_binary-amd64_Packages}` and running
  `apt update` fixed it [M].
- Kernel taint of this machine: `P` and `O` (NVIDIA, Ubuntu's `nvidia-dkms-595`, signed with the machine's key), `S` (the daemon writes MSR `0x615`), `U` (`enable_guc`). Removing the
  prototypes does not change it (4165 before and after) [M].

## Observations of 26.11.2026 (two detaches in normal use, 7.3.6-1-surface, daemon 551631c in control)
- Both detaches: handler exit 0, then USB disconnect, `Card not present`, `Xid 79`/`154`, and `sdtxd` logged `unexpected disconnect: latch is closed` [M journal]. No such line in the `surface-dtx-daemon` journal from 17.11. to before these two. Maintainer: on this machine the EC at times reports that the detach did not work although the display comes off with a light pull, without force [maintainer observation]. That this is what the two lines record is [H]; the cause in the EC or the latch is unknown.
- Both detaches without `emergency`; 605/609 came back under another hwmon index, once with the display attached the other way round (posture 5) [M].
- While the detach handler ran before the first detach, the EC value `available input power` (`28B7BD85/8`) read 0 for some seconds although on AC; the blob's psys limits fell to 23/34.5 W each time and returned [M series]. Link to the detach request only by time [H for the cause].
- After a re-attach without a reboot the dGPU stayed in `D0` and the handler refused the next detach ("GPU nicht ausgeschaltet: D0"), for about 3 min in the first case (the second case has one check 16 s after the attach, which shows nothing) until the reboot [M]. After a boot it reached `D3cold` within about a minute. The handler checks for processes holding the GPU first, so none held it at those checks, the daemon included [C script]. Cause not shown; the maintainer takes it for a current kernel bug [H]. **Found 26.11.2026 afternoon [M]:** after a re-attach the NVIDIA driver reports `Runtime D3 status: Not supported` in `/proc/driver/nvidia/gpus/<pci>/power` (normal: `Enabled (fine-grained)`), with `power/control` `auto`, `runtime_usage` 1 and no process holding the GPU; the daemon was not touching it. So the daemon is not the cause; why the driver drops runtime D3 is not shown [H]. Next time record before the reboot: `power_state`, `power/control`, `power/runtime_status`, `power/runtime_usage` of the GPU and `/proc/driver/nvidia/gpus/<pci>/power` (normal here: `D3cold`, `auto`, `suspended`, 0).

