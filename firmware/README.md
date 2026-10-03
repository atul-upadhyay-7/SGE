# ESP32 pack logger (firmware)

**Status: not compiled on a board and not tested on hardware.** What *is*
checked: the conversion maths in `esp32_pack_logger/pack_math.h` (NTC beta
equation, tap-to-cell voltages, current sign) passes the PC test in
`esp32_pack_logger/host_test/` (`g++ -std=c++11 host_test/test_math.cpp`).
The MQTT message format is the same one `src/pack/publisher.py` sends,
and that path is exercised end to end in the sandbox (see `docs/RESEARCH.md`).

## What it does

One ESP32 reads a 3S pack and publishes one JSON message per cell per
second to `bms/CELL1..3/telemetry`:

    {"timestamp": 1790000000, "voltage": 3.91, "current": -2.01, "temperature": 31.4}

Current is **negative while discharging**, the convention the SOH model was
trained on. The INA219 reports discharge as positive, so the firmware
negates it (`model_current`). A series pack carries one current, so all
three cells get the same value.

## Parts and wiring (3S1P)

| Part | Use | Bus / pin |
|---|---|---|
| INA219 breakout (0.1 ohm shunt) | pack current, in series with the load | I2C 0x40 |
| ADS1115 #1 | cell taps through 4:1 dividers, A0 = tap 1 (4.2 V), A1 = tap 2, A2 = tap 3 | I2C 0x48 |
| ADS1115 #2 | 3 NTC 10k thermistors (beta 3950), each in a divider with a 10k fixed resistor | I2C 0x49 (ADDR to VDD) |
| ESP32 dev board | Wi-Fi + MQTT | SDA 21, SCL 22 |

Common ground for pack, ADS1115 and ESP32. Check every divider output with a
multimeter before connecting the ADS1115: an input must never exceed VDD + 0.3 V.
Cell voltage `i` is `tap_i * ratio_i - tap_(i-1) * ratio_(i-1)`.

## Why an external ADC and not the ESP32's own

The ESP32 SAR ADC is non-linear and noisy, and its readings vary chip to chip,
which matters when the signal of interest is a few tens of millivolts of cell
imbalance. The ADS1115 is a 16-bit ADC with a programmable gain stage. This is
engineering judgement, not a measurement made here: calibrate whichever ADC you
use against a multimeter. Divider resistor tolerance adds error to the
subtraction (use 0.1 % parts).

## Build

Arduino IDE, board "ESP32 Dev Module". Libraries: Adafruit INA219, Adafruit
ADS1X15, PubSubClient, ArduinoJson. Edit `config.h` (Wi-Fi, broker address,
divider ratios) and flash. Set the same broker address on the PC side
(`docker compose up`, see the main README).

## Safety

Lithium cells can start fires. Test first on a bench with a current-limited
supply and a fuse. This firmware only reads; it does not control charging or
protect the pack. It is not a BMS protection circuit.
