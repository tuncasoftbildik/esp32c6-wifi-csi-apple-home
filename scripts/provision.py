#!/usr/bin/env python3
"""Headless Wi-Fi provisioning for an ESPectre (or any Improv Serial) ESP32 over USB.

No browser needed. Sends Wi-Fi credentials to the device via the Improv Serial
protocol and prints the device URL/IP once it joins the network.

Usage:
    python3 provision.py "YOUR_SSID" "YOUR_PASSWORD" [/dev/cu.usbmodemXXX]

Requires: pyserial  (pip install pyserial)

The port is auto-detected on macOS (/dev/cu.usbmodem*) if omitted.
Nothing is stored in this file; credentials are passed as arguments only.
"""
import sys
import time
import glob

try:
    import serial
except ImportError:
    sys.exit("pyserial gerekli: pip install pyserial")


def find_port(explicit=None):
    if explicit:
        return explicit
    ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return ports[0] if ports else "/dev/cu.usbmodem101"


def _packet(ptype, data):
    body = b"IMPROV" + bytes([1, ptype, len(data)]) + data
    return body + bytes([sum(body) & 0xFF])


def set_wifi(ssid, pw):
    ssid_b, pw_b = ssid.encode(), pw.encode()
    payload = bytes([len(ssid_b)]) + ssid_b + bytes([len(pw_b)]) + pw_b
    rpc = bytes([0x01, len(payload)]) + payload  # 0x01 = SET_WIFI_SETTINGS
    return _packet(0x03, rpc)                     # 0x03 = RPC command


STATES = {0x02: "READY", 0x03: "PROVISIONING", 0x04: "PROVISIONED"}
ERRORS = {0x00: "none", 0x01: "invalid RPC", 0x02: "unknown RPC",
          0x03: "unable to connect to Wi-Fi", 0x04: "not authorized"}


def parse(buf):
    out = []
    while True:
        i = buf.find(b"IMPROV")
        if i < 0 or len(buf) - i < 9:
            break
        length = buf[i + 8]
        end = i + 9 + length + 1
        if len(buf) < end:
            break
        out.append((buf[i + 7], buf[i + 9:i + 9 + length]))
        buf = buf[end:]
    return out, buf


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    ssid, pw = sys.argv[1], sys.argv[2]
    port = find_port(sys.argv[3] if len(sys.argv) > 3 else None)
    print(f"[i] port={port} ssid={ssid!r}")

    s = serial.Serial(port, 115200, timeout=0.5)
    time.sleep(0.3)
    s.reset_input_buffer()
    s.write(set_wifi(ssid, pw))
    s.flush()
    print("[i] SET_WIFI_SETTINGS sent, waiting up to 40s for the device to join...")

    buf = b""
    t = time.time()
    url = None
    while time.time() - t < 40:
        buf += s.read(256)
        pkts, buf = parse(buf)
        for ptype, data in pkts:
            if ptype == 0x01:
                print("    state:", STATES.get(data[0] if data else 0, "?"))
            elif ptype == 0x02:
                print("    error:", ERRORS.get(data[0] if data else 0, "?"))
            elif ptype == 0x04:  # RPC result: device URL(s)
                p = 2
                while p < len(data):
                    ln = data[p]
                    url = data[p + 1:p + 1 + ln].decode("utf-8", "replace")
                    p += 1 + ln
                    print("    device URL:", url)
        if url:
            break
    s.close()

    if url:
        print(f"\nOK  provisioned -> {url}")
        return 0
    print("\nNo confirmation. Common causes: Wi-Fi is 5 GHz only (ESP32-C6 is 2.4 GHz),"
          " wrong password, or the AP was asleep. Retry once.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
