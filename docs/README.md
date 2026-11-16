# Documentation

How linux-surface-power works, and where to find what. The details are in the sub-folders; this page is the map.

## The idea in one picture

On Windows, the Surface System Management Framework (SMF) evaluates a configuration blob that the firmware ships. Small client drivers
feed it with sensor values and carry its results to the hardware. On Linux, one daemon takes the place of the core and the clients: it
reads the same blob, collects the same inputs from Linux interfaces, evaluates the blob the way the Windows core does, and writes the
results where Linux can.

```mermaid
flowchart TB
    BLOB[("SMF blob from the firmware<br/>one per device")]
    BLOB --> CORE
    BLOB --> ENG
    subgraph WIN["Windows"]
        direction LR
        WC["Surface clients<br/>sensors, battery, display,<br/>camera, dock, GPU"] --> CORE["SMF core"]
        CORE --> WOUT["IHV CPU client, thermal policy,<br/>SMF client (fans), GPU controller"]
    end
    subgraph LIN["Linux: surface-powerd"]
        direction LR
        IN["input adapters<br/>hwmon, power_supply, SSAM,<br/>DRM, USB, NVML"] --> ENG["engine<br/>same block semantics"]
        ENG --> SUP["supervisor"] --> OUT["output stage<br/>powercap, MSR"]
    end
```

## What the daemon does every cycle

```mermaid
flowchart TB
    L["loader<br/>decode · preprocess · parse · build graph"] --> E
    subgraph cycle["every second"]
        I["input adapters<br/>read sources, convert to blob units,<br/>mark invalid values"] --> E["engine<br/>runs the blob's instances and stages"]
        E --> S{"supervisor<br/>emergency rules,<br/>foreign changes"}
        S -->|control| O["output stage<br/>clamp → write → read back"]
        S -->|dry-run, emergency| N["nothing written<br/>(firmware values)"]
        E --> M["monitor<br/>journal · status.json · time series"]
    end
```

## States of the daemon

```mermaid
stateDiagram-v2
    [*] --> dry_run: default, untested device,<br/>or an input of a written limit is missing
    [*] --> control: --control on a device<br/>with every input available
    control --> emergency: skin ≥ 60 °C, CPU ≥ 105 °C<br/>or core inputs lost for 60 s
    emergency --> control: recovered
    control --> yielded: three foreign changes in 60 s
    control --> paused: suspend
    paused --> control: resume (engine reset)
    dry_run --> emergency: same rules, nothing written
    emergency --> dry_run: recovered
    note right of emergency: firmware values are written back,<br/>the engine keeps running
```

## Where each value comes from and goes

The blob reads *input channels* and writes *output channels*, both named `interface GUID / channel`. Every device page lists them.

```mermaid
flowchart LR
    subgraph inputs["Inputs"]
        T["temperatures<br/>2E8B14C2"]
        P["battery and power data<br/>28B7BD85"]
        S["slider, posture, lid,<br/>platform events"]
        D["display, GPU, standby,<br/>USB-C, client-computed"]
    end
    subgraph outputs["Outputs"]
        LIM["power limits 0B7B5350<br/>PL1 · PL2 · PL4 · psys PL1/PL2 · PL3 · dGPU"]
        TH["thermal throttle requests 9BBB8910<br/>per CPU cluster, GPU, modem"]
        FAN["fan and charging B620798F"]
        NOT["notifications, offsets, SoC values"]
    end
    inputs --> BL(["SMF blob"]) --> outputs
    LIM -->|Intel| RAPL["powercap / MSR<br/>written"]
    TH -.->|"candidate"| FREQ["cpufreq / GPU frequency caps"]
    FAN -.->|"not analysed"| EC["EC through SAM"]
```

- **Intel devices:** the power limits are written through powercap (PL1, PL2, psys) and optionally PL4 and PL3.
- **AMD and Snapdragon devices:** Linux has no write path for the power limits yet. On the Snapdragon models the blob regulates mainly
  through thermal throttle requests, for which cpufreq caps are the Linux candidate.
- Everything without a Linux path is computed and logged, so the values can be compared with Windows.

## Map

| Folder | What is in it |
|---|---|
| [`devices/`](devices/README.md) | one page per Surface: blob, inputs, outputs, block types, where Linux gets or puts each value (generated) |
| [`research/`](research/README.md) | findings: SB3 root cause, Windows measurements, SMF architecture, device survey, Linux interfaces, field tests, newer blobs |
| [`requirements/`](requirements/README.md) | requirements, acceptance criteria, architecture, ideas |
| [`specs/`](specs/README.md) | technical specifications the code implements: blob format, engine, controller, inputs, outputs, supervisor, monitoring, tools, deviations |

Evidence markers used throughout: [C] from the Windows driver code, [M] measured, [H] hypothesis, [D] our design decision.
