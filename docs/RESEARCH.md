# Design notes and sources

Each design choice in the pack monitor, with the source it rests on and how it
was checked. "Verified" means run in this repository; "not verified" is said
plainly.

## Internal resistance: DC resistance from a load step

`R = -dV / dI` across a current step (`src/pack/resistance.py`). Chosen because it
is cheap enough for an ESP32 with an INA219/INA226 and is a standard BMS method.
The result depends on how long after the step it is read (ohmic vs diffusion
parts), so only steps between close samples are used and the window is recorded.
Sources: MDPI Energies 11(6):1490 (comparison of resistance estimation methods,
https://www.mdpi.com/1996-1073/11/6/1490); PMC5758786 (resistance depends on
measurement timescale, https://pmc.ncbi.nlm.nih.gov/articles/PMC5758786/).

On the NASA files the sample spacing is about 17 s, so the value (about 0.15 ohm)
includes some diffusion and is not a millisecond ohmic resistance. The monitor
only uses it relative to each cell's own baseline. Verified: unit tests, and the
live run below, where an injected resistance fault moves the estimate from about
0.15 to 0.35-0.4 ohm and raises `RESISTANCE_SPIKE`.

## Cell imbalance

Voltage spread between cells at the same instant, with levels at 30 / 50 / 100 mV
(watch / warn / critical), a persistent weak-cell flag and an SOH mismatch flag.
Thresholds are engineering defaults and should be tuned to the chemistry and BMS.
Source on imbalance and balancing: https://www.bestpcbs.com/blog/2026/07/how-much-can-voltage-vary-in-a-bms/ ;
TI cell-balancing material was found but not read.

The live path compares voltage a fixed 900 s into each discharge. End-of-discharge
voltage is not comparable across the NASA cells because the bench stopped each
cell at its own cutoff (2.7 V, 2.2 V, 2.5 V). Verified: on a clean replay the
spread is 49-71 mV; before this change it was 200+ mV and the pack showed STOP
without any fault.

## Thermal runaway indicators

Warn at 55 C, critical at 70 C, and a rate flag at dT/dt above 1 C/s. The 70 C
level and the rate criterion follow https://www.mdpi.com/1996-1073/19/3/858
(multi-level alarm). Related: https://pmc.ncbi.nlm.nih.gov/articles/PMC10795034/.
The NASA data never gets near these temperatures, so thermal alarms are only
exercised with synthetic, labelled fault injection. Not verified on a real cell.

## Anomaly layer

Isolation Forest on per-cell cycle features, trained on healthy cycles of the
other packs (leave-one-pack-out). It is a second opinion, not the primary alarm.
Measured on synthetic faults it finds resistance spikes but is weaker on thermal
(about 58%) and cell sag (about 21%); the rule layer carries the alarms.

## Cycles to retirement

Retirement is 80% of initial capacity (the PdM brief's threshold; the tracker
sheet says 70%, which is inconsistent and should be corrected one way or the
other). RUL uses reference-trajectory matching, evaluated leave-one-cell-out:
mean MAE 21.0 vs 32.7 discharges for a fleet-lifetime baseline. It loses on B0006
(48.4 vs 42.0), the fast-fading cell. Straight-line extrapolation of the recent
fade was far worse (MAE above 100) and is not used. With four cells this is a
demonstration, not a validated life model.

## SOH model and leakage

Causal features only: each cycle's features use that cycle and earlier ones
(`src/causal.py`), with the same code in training and in the streaming service,
and the raw `cycle` index removed (it was a train/serve skew). Leave-one-cell-out
MAE: 3.47% with nested hyperparameter search, 3.14% with fixed hyperparameters;
B0006 is the weak cell (about 7%). The NASA cells run at a nearly constant load
(1.5-2 A), so the model cannot truly learn load sensitivity from them.
Dataset: https://data.nasa.gov/dataset/li-ion-battery-aging-datasets

## Hardware inputs (firmware)

INA219 for pack current; ADS1115 for cell taps and thermistors; NTC beta
equation. The ESP32 firmware is **not compiled on a board and not tested on
hardware**; only its conversion maths is checked on a PC. See `firmware/README.md`.

## End-to-end live run (verified in a sandbox, no Docker)

Mosquitto 2, `run_pack.py` (3 SOH services), `bridge.py`, `grafana_api.py`,
Grafana 11.2 with the simpod JSON datasource, and `publisher.py` replaying NASA
B0005 / B0007 / B0018 as CELL1-3 with `--fault CELL2:resistance_spike:40
--fault CELL3:thermal:65`. Result: alerts appeared in `live_pack_alerts` and on
the "Battery Pack Live" dashboard, which was screenshotted headless and the
rendered pixels inspected (`docs/pack_live.png`).

Known limits seen in that run: publish pace of 0.3 s per discharge dropped
messages (about one third of cycles were not seen), so use 0.7 s or slower for
the replay; one B0018 cycle produced an isolated low SOH, which the monitor now
smooths with a median of three; the SOH model reads an injected voltage sag as
capacity loss, so synthetic resistance faults also pull that cell's SOH down.

## Offline fault evaluation: what it can and cannot say

`src/pack/evaluate.py` injects synthetic faults into every NASA pack of three
cells (24 per kind) and records detection. Clean-pack false alarms for the
fault-specific alerts: RESISTANCE_SPIKE 0 and THERMAL 0 over 564 discharges.
IMBALANCE, WEAK_CELL and SOH_MISMATCH fire on most clean discharges in this
offline table, because it uses end-of-discharge voltage (different cutoffs per
cell) and the four NASA cells genuinely differ in SOH. Do not read those three
as detection rates; the live path uses same-instant voltage (see above) and a
clean live replay shows 49-71 mV spread. The offline table was not rebuilt on
same-instant voltage.
