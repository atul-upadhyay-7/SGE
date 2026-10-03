#include <cstdio>
#include <cmath>
#include "../pack_math.h"
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
  // 10k NTC, beta 3950, 10k fixed: at 25 C the node is at half of vcc.
  float t = ntc_celsius(1.65f, 3.3f, 10000, 10000, 25, 3950);
  CHECK(fabsf(t - 25.0f) < 0.05f);
  // 50 C: R_ntc = 10k*exp(3950*(1/323.15-1/298.15)) = 3.6k (approx)
  float r50 = 10000.0f * expf(3950.0f * (1.0f/323.15f - 1.0f/298.15f));
  float v50 = 3.3f * r50 / (10000.0f + r50);
  CHECK(fabsf(ntc_celsius(v50, 3.3f, 10000, 10000, 25, 3950) - 50.0f) < 0.05f);
  // open / shorted sensor
  CHECK(std::isnan(ntc_celsius(3.3f, 3.3f, 10000, 10000, 25, 3950)));
  CHECK(std::isnan(ntc_celsius(0.0f, 3.3f, 10000, 10000, 25, 3950)));
  // 3S pack: cells 4.1, 4.0, 3.9 -> taps 4.1, 8.1, 12.0 through /4 dividers
  float taps[3] = {4.1f/4, 8.1f/4, 12.0f/4}, ratio[3] = {4, 4, 4}, c[3];
  cell_voltages(taps, ratio, 3, c);
  CHECK(fabsf(c[0]-4.1f) < 1e-4f && fabsf(c[1]-4.0f) < 1e-4f && fabsf(c[2]-3.9f) < 1e-4f);
  CHECK(model_current(2.0f) == -2.0f);
  printf(fails ? "FAILED\n" : "all host checks passed\n");
  return fails;
}
