// esp32_pack_logger.ino
// 3S battery pack logger: ESP32 + INA219 (pack current) + 2x ADS1115
// (cell taps through dividers, 3 NTC thermistors) -> MQTT.
//
// STATUS: NOT compiled on a board and NOT tested on hardware. The
// math in pack_math.h is checked on a PC (host_test/). Everything
// else (wiring, I2C addresses, divider values) is a starting point
// to verify with a multimeter before connecting real cells.
//
// Publishes, once per second per cell, to bms/CELL<n>/telemetry:
//   {"timestamp": <s>, "voltage": <V>, "current": <A>, "temperature": <C>}
// Current is NEGATIVE while the pack discharges (NASA convention the
// SOH model was trained on). In a series pack every cell carries the
// same current, so the one INA219 reading is sent for all three.
//
// Libraries (Arduino Library Manager): Adafruit INA219,
// Adafruit ADS1X15, PubSubClient, ArduinoJson (v6/v7).

#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_ADS1X15.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include "pack_math.h"
#include "config.h"

Adafruit_INA219 ina(0x40);
Adafruit_ADS1115 adsV;   // 0x48: cell taps on A0..A2
Adafruit_ADS1115 adsT;   // 0x49: NTC on A0..A2 (ADDR pin to VDD)
WiFiClient net;
PubSubClient mqtt(net);

static const float TAP_RATIO[3] = {TAP1_RATIO, TAP2_RATIO, TAP3_RATIO};

static float read_volts(Adafruit_ADS1115 &a, int ch) {
  // Average 8 conversions: cheap noise reduction.
  long sum = 0;
  for (int i = 0; i < 8; i++) sum += a.readADC_SingleEnded(ch);
  return a.computeVolts((int16_t)(sum / 8));
}

static void wifi_connect() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(250);
  configTime(0, 0, "pool.ntp.org");   // wall-clock timestamps
  while (time(nullptr) < 1700000000) delay(200);   // wait for NTP
}

static void mqtt_connect() {
  while (!mqtt.connected()) {
    if (!mqtt.connect(MQTT_CLIENT_ID)) delay(1000);
  }
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  if (!ina.begin()) { Serial.println("INA219 not found"); while (1) delay(1000); }
  ina.setCalibration_32V_2A();   // 0.1 ohm shunt on the stock breakout
  adsV.setGain(GAIN_ONE);        // +-4.096 V full scale
  adsT.setGain(GAIN_ONE);
  if (!adsV.begin(0x48) || !adsT.begin(0x49)) {
    Serial.println("ADS1115 not found"); while (1) delay(1000);
  }
  wifi_connect();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(256);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) wifi_connect();
  mqtt_connect();
  mqtt.loop();

  float taps[3], cells[3];
  for (int i = 0; i < 3; i++) taps[i] = read_volts(adsV, i);
  cell_voltages(taps, TAP_RATIO, 3, cells);

  // INA219 current_mA is positive for discharge when VIN+ is the
  // battery side of the shunt. Negate for the model.
  float amps = model_current(ina.getCurrent_mA() / 1000.0f);

  time_t now = time(nullptr);
  for (int i = 0; i < 3; i++) {
    float t = ntc_celsius(read_volts(adsT, i), NTC_VCC, NTC_R_FIXED,
                          NTC_R0, 25.0f, NTC_BETA);
    if (isnan(t)) continue;       // open/shorted sensor: send nothing

    StaticJsonDocument<192> doc;
    doc["timestamp"] = (double)now;
    doc["voltage"] = cells[i];
    doc["current"] = amps;
    doc["temperature"] = t;
    char body[192];
    size_t n = serializeJson(doc, body);
    char topic[32];
    snprintf(topic, sizeof(topic), "bms/CELL%d/telemetry", i + 1);
    mqtt.publish(topic, (const uint8_t *)body, n, false);
  }
  delay(1000);
}
