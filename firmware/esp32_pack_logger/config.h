// config.h - edit before flashing. Do not commit real credentials.
#pragma once
#define WIFI_SSID "your-wifi"
#define WIFI_PASS "your-password"
#define MQTT_HOST "192.168.1.10"     // the machine running Mosquitto
#define MQTT_PORT 1883
#define MQTT_CLIENT_ID "esp32-pack1"

// Divider on each tap: ratio = (R_top + R_bottom) / R_bottom.
// 3S pack: tap1 4.2 V, tap2 8.4 V, tap3 12.6 V. A ratio of 4 keeps the
// 12.6 V tap at 3.15 V, inside the ADS1115 +-4.096 V range when it is
// powered from 3.3 V (never exceed VDD + 0.3 V on an input pin).
// Use 0.1 % resistors and calibrate against a multimeter.
#define TAP1_RATIO 4.0f
#define TAP2_RATIO 4.0f
#define TAP3_RATIO 4.0f

// NTC divider: 3V3 - R_FIXED - node - NTC - GND
#define NTC_VCC 3.3f
#define NTC_R_FIXED 10000.0f
#define NTC_R0 10000.0f
#define NTC_BETA 3950.0f
