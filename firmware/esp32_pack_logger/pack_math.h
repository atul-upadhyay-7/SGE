// pack_math.h - pure C++ helpers (no Arduino headers) so they can be
// checked on a PC with host_test/.  See ../README.md.
#pragma once
#include <math.h>

// NTC thermistor, beta equation, divider with the NTC on the LOW side:
//   3V3 --- R_FIXED --- node --- NTC --- GND
// v_node is the voltage at the node, vcc the divider supply.
//   R_ntc = R_FIXED * v_node / (vcc - v_node)
//   1/T   = 1/T0 + ln(R_ntc / R0) / BETA          (T in kelvin)
// Returns degrees C, or NAN when the reading is outside the divider
// range (open or shorted sensor).
inline float ntc_celsius(float v_node, float vcc, float r_fixed,
                         float r0, float t0_c, float beta) {
  if (!(v_node > 0.01f * vcc) || !(v_node < 0.99f * vcc)) return NAN;
  float r_ntc = r_fixed * v_node / (vcc - v_node);
  float t0_k = t0_c + 273.15f;
  float inv_t = 1.0f / t0_k + logf(r_ntc / r0) / beta;
  return 1.0f / inv_t - 273.15f;
}

// Cell voltages from taps measured through a divider.
// taps[i] is the voltage seen by the ADC for tap i (after the divider),
// ratio[i] = (R_top + R_bottom) / R_bottom for that divider.
// Cell i = tap_i*ratio_i - tap_{i-1}*ratio_{i-1}.
inline void cell_voltages(const float *taps, const float *ratio, int n,
                          float *cells) {
  float prev = 0.0f;
  for (int i = 0; i < n; i++) {
    float v = taps[i] * ratio[i];
    cells[i] = v - prev;
    prev = v;
  }
}

// The INA219 reports current positive when it flows from VIN+ to VIN-
// (discharge through the load, if the shunt is wired in the load path).
// The model and the NASA data use negative = discharging, so negate.
inline float model_current(float ina_current_a) { return -ina_current_a; }
