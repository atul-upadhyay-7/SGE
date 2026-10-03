"""
resistance.py
-------------
Internal (DC) resistance from a current step: R = -dV / dI.

Method, and why it looks like this
----------------------------------
The direct current resistance (DCR) method divides the voltage
change by the current change across a step in load. It is the
cheapest estimator that runs on an ESP32 with an INA219/INA226,
and the literature compares it favourably to heavier recursive
and Kalman-filter estimators for pack-scale use (Comparative
Analysis of Lithium-Ion Battery Resistance Estimation
Techniques for BMS, Energies 2018, 11(6), 1490). Two cautions
from that work and from the measurement-timescale study
(PMC5758786) shape the code:

  * The answer depends on how long after the step you look. A
    resistance read 10 ms after a step is mostly the ohmic
    part; read 10 s later it includes diffusion. So steps are
    only accepted when the two samples are close in time
    (max_dt_s), and the report says which window was used.
  * A step needs a real current change. Dividing noise by noise
    gives a wild number, so steps below min_step_a are ignored.

Sign convention: current is positive when the cell delivers
power (discharge). When the load rises, the terminal voltage
falls, so R = (V_before - V_after) / (I_after - I_before) is
positive. The NASA files store discharge current as negative;
pass discharge_positive=False for those and the sign is flipped.

The result is the median over every accepted step in the trace,
so one noisy sample pair cannot move it, together with the
count of steps used. A trace with no usable step returns
None, never a made-up value.
"""

import numpy as np

DEFAULT_MIN_STEP_A = 0.2
DEFAULT_MAX_DT_S = 5.0


def estimate_dcr(
    voltage,
    current,
    time,
    min_step_a=DEFAULT_MIN_STEP_A,
    max_dt_s=DEFAULT_MAX_DT_S,
    discharge_positive=True,
):
    """
    Return {"dcr_ohm", "steps", "min_step_a", "max_dt_s"} or None.
    """

    v = np.asarray(voltage, dtype=float)
    i = np.asarray(current, dtype=float)
    t = np.asarray(time, dtype=float)

    if not (len(v) == len(i) == len(t)) or len(v) < 2:

        return None

    if not discharge_positive:

        i = -i

    di = np.diff(i)
    dv = np.diff(v)
    dt = np.diff(t)

    ok = (
        np.isfinite(di)
        & np.isfinite(dv)
        & (np.abs(di) >= min_step_a)
        & (dt > 0)
        & (dt <= max_dt_s)
    )

    if not ok.any():

        return None

    # Both a load-on and a load-off step give a valid R. The
    # sign of dV and dI is opposite in each, so the ratio is
    # positive; anything negative is noise or a state change
    # (a relaxation tail) and is discarded.
    r = -dv[ok] / di[ok]
    r = r[r > 0]

    if len(r) == 0:

        return None

    return {
        "dcr_ohm": float(np.median(r)),
        "steps": int(len(r)),
        "min_step_a": float(min_step_a),
        "max_dt_s": float(max_dt_s),
    }
