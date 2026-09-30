"""
streaming
---------
Live per-second SOH inference.

The model predicts once per completed cycle; this package
holds that value and serves it on every read. See
soh_service.py for the timing argument and cycle_tracker.py
for boundary detection.

Modules
-------
cycle_tracker  per-second samples to per-cycle feature rows
soh_service    latched prediction, loaded once, read often
replay         drive the live path from .mat files, no device
mqtt_client    MQTT transport, added after the core is tested
"""

__all__ = [
    "cycle_tracker",
    "soh_service",
    "replay",
    "mqtt_client"
]
