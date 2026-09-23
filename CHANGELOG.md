# Changelog

## 0.1.0

First versioned release of the setup guide and helper scripts. This version
identifies this repository, not the ESPectre firmware.

### Improvements

- Restrict scan and doctor AP candidates to the connected SSID with usable BSSID and signal data.
- Report Healthy (exit 0), Problems found (exit 1), or Insufficient measurements (exit 2). Low CSI coverage is a problem; missing or invalid measurements cannot produce a healthy result.
- Wait for calibration to finish and the detector to become ready, with an explicit failure if completion is not confirmed within 60 seconds.
- Document the updated commands and add 13 device-independent regression tests.

### Validation

- All 13 regression tests passed.
- No physical-device or Apple Home integration test was performed for this release.
