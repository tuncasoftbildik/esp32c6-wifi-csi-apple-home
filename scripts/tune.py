#!/usr/bin/env python3
"""Tune an ESPectre CSI sensor over its local Direct HTTP API.

The ESPectre device exposes a local HTTP API on port 62587. This helper wraps
the calls you need to get reliable detection in a real room, based on the
gotchas documented in the README (weak mesh node, ICMP-dropping routers,
noisy calibration).

Your computer must be on the SAME normal (non-guest) network as the device.

Usage:
    python3 tune.py --host 192.168.1.42 status
    python3 tune.py --host 192.168.1.42 scan
    python3 tune.py --host 192.168.1.42 pin AA:BB:CC:DD:EE:FF
    python3 tune.py --host 192.168.1.42 traffic dns        # ping | dns | dns_tcp
    python3 tune.py --host 192.168.1.42 calibrate          # EMPTY the room first
    python3 tune.py --host 192.168.1.42 watch              # live motion score
    python3 tune.py --host 192.168.1.42 doctor             # one-shot health check + fixes

Stdlib only. Find the device IP from your Home app, router, or mDNS
(`dns-sd -B _espectre._tcp` on macOS; the host name is espectre-<id>.local).
"""
import argparse
import json
import sys
import time
import urllib.request

PORT = 62587
ORIGIN = "https://espectre.dev"  # published firmware allows this origin


def call(host, path, method="GET", body=None, timeout=10):
    url = f"http://{host}:{PORT}/espectre/v1{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Origin", ORIGIN)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def diag(host, fields):
    import urllib.parse
    q = urllib.parse.quote(json.dumps(fields))
    return call(host, f"/diagnostics?fields={q}")


def cmd_status(host):
    s = call(host, "/sensing")
    w = call(host, "/wifi")
    d = diag(host, ["csi_occupancy", "csi_accepted_pps", "wifi_rssi_dbm"])
    print(f"Wi-Fi   : {w.get('ssid')}  bssid={w.get('bssid')}  rssi={w.get('rssi_dbm')} dBm  ch={w.get('channel')}")
    print(f"CSI     : occupancy={d.get('csi_occupancy')}  accepted_pps={d.get('csi_accepted_pps')}")
    print(f"Detector: ready={s.get('ready')}  calibrating={s.get('calibrating')}  "
          f"detector={s.get('detector')}  threshold={round(s.get('threshold', 0), 4)}")
    print(f"Traffic : generator={s.get('traffic_generator_mode')}  mode={s.get('csi_traffic_mode')}")
    r = w.get("rssi_dbm")
    if isinstance(r, (int, float)) and r < -68:
        print("  ! Signal is weak (< -68 dBm). Run `scan` and `pin` the strongest AP,")
        print("    or move the device / add an AP closer. CSI needs a strong link.")


def cmd_scan(host):
    call(host, "/wifi/scans", method="POST")
    for _ in range(8):
        time.sleep(3)
        d = call(host, "/wifi/access-points")
        if not isinstance(d, dict) or d.get("scanning"):
            print("  scanning...")
            continue
        aps = sorted(d.get("access_points", []),
                     key=lambda a: a.get("rssi_dbm", -999), reverse=True)
        print(f"=== {len(aps)} access points (strongest first) ===")
        for a in aps:
            r = a.get("rssi_dbm")
            tag = "STRONG" if r >= -60 else ("ok" if r >= -68 else "weak")
            print(f"  {r:>4} dBm  [{tag:6}]  ch{a.get('channel')}  "
                  f"{a.get('bssid')}  {a.get('ssid') or '(hidden)'}")
        print("\nPin the strongest with:  tune.py --host <ip> pin <BSSID>")
        return
    print("  scan did not complete")


def cmd_pin(host, bssid):
    print(call(host, "/wifi/bssid", method="PUT", body={"bssid": bssid}).get("message", ""))
    time.sleep(12)
    w = call(host, "/wifi")
    print(f"now: bssid={w.get('bssid')}  rssi={w.get('rssi_dbm')} dBm")


def cmd_traffic(host, mode):
    call(host, "/sensing", method="PATCH", body={"traffic_generator_mode": mode})
    time.sleep(8)
    d = diag(host, ["csi_occupancy", "csi_accepted_pps"])
    print(f"traffic={mode}  occupancy={d.get('csi_occupancy')}  accepted_pps={d.get('csi_accepted_pps')}")
    print("tip: `ping` is best on a strong link; if occupancy stays 0, the router may")
    print("     drop ICMP -> try `dns`. Re-run `calibrate` after changing traffic mode.")


def cmd_calibrate(host):
    print(">>> EMPTY the room and stay out for ~30 s. Calibration learns the quiet baseline;")
    print(">>> any movement during it inflates the threshold and detection stops working.")
    print("starting in 10 s...")
    time.sleep(10)
    call(host, "/sensing/calibrations", method="POST")
    for i in range(8):
        time.sleep(5)
        s = call(host, "/sensing")
        print(f"  [{(i+1)*5}s] calibrating={s.get('calibrating')} ready={s.get('ready')} "
              f"threshold={round(s.get('threshold', 0), 4)}")
    print("done. A low, stable threshold (~0.03-0.1) with ready=True is good.")


def cmd_watch(host):
    print("live motion score (Ctrl-C to stop). Walk near the device:")
    req = urllib.request.Request(f"http://{host}:{PORT}/espectre/v1/events")
    req.add_header("Origin", ORIGIN)
    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r:
            line = line.decode().strip()
            if line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                    s = d.get("score", 0)
                    bar = "#" * min(40, int(s * 40))
                    print(f"  {d.get('state',''):7} {s:7.3f} {bar}")
                except json.JSONDecodeError:
                    pass


def cmd_doctor(host):
    """One-shot health check: runs the whole manual diagnosis from the README
    (weak signal, wrong mesh node, ICMP-dropped CSI, noisy calibration) and
    prints an ordered list of fixes."""
    print("ESPectre CSI — health check\n")
    problems = []

    s = call(host, "/sensing")
    w = call(host, "/wifi")
    d = diag(host, ["csi_occupancy", "csi_accepted_pps", "traffic_tx_pps", "wifi_rssi_dbm"])
    rssi = w.get("rssi_dbm")
    occ = d.get("csi_occupancy")
    acc = d.get("csi_accepted_pps")
    tx = d.get("traffic_tx_pps")
    thr = s.get("threshold")
    ready = s.get("ready")
    calibrating = s.get("calibrating")
    gen = s.get("traffic_generator_mode")
    cur_bssid = w.get("bssid")

    # 1) Signal strength — CSI quality is dominated by RSSI.
    if isinstance(rssi, (int, float)):
        if rssi >= -60:
            print(f"  Wi-Fi ......... {rssi} dBm   OK (strong)")
        elif rssi >= -68:
            print(f"  Wi-Fi ......... {rssi} dBm   ~ borderline")
        else:
            print(f"  Wi-Fi ......... {rssi} dBm   WEAK (< -68). CSI needs a strong link.")
            problems.append("get a stronger link: `scan` then `pin` the best AP, or move closer / add an AP")
    else:
        print("  Wi-Fi ......... unknown")

    # 2) Mesh — is a much stronger AP available that we are NOT on?
    best = None
    try:
        call(host, "/wifi/scans", method="POST")
        for _ in range(6):
            time.sleep(3)
            sc = call(host, "/wifi/access-points")
            if isinstance(sc, dict) and not sc.get("scanning"):
                aps = sorted((sc.get("access_points") or []),
                             key=lambda a: a.get("rssi_dbm", -999), reverse=True)
                best = aps[0] if aps else None
                break
    except (urllib.error.URLError, OSError):
        pass
    if best and isinstance(best.get("rssi_dbm"), (int, float)) and isinstance(rssi, (int, float)):
        if best.get("bssid") != cur_bssid and best["rssi_dbm"] > rssi + 6:
            print(f"  Mesh .......... STRONGER AP available: {best['rssi_dbm']} dBm {best['bssid']} "
                  f"(you are on {rssi} dBm) — device latched onto a far node.")
            problems.append(f"pin the strong node: `pin {best['bssid']}`")
        else:
            print("  Mesh .......... on the strongest reachable AP   OK")

    # 3) CSI flow — 0 accepted while traffic is going out = router drops the probe.
    if isinstance(acc, (int, float)) and acc < 1:
        if isinstance(tx, (int, float)) and tx > 0:
            print(f"  CSI flow ...... accepted_pps={acc} (tx={tx})   BLOCKED — router likely drops the gateway probe.")
            problems.append("switch traffic to DNS: `traffic dns`")
        else:
            print(f"  CSI flow ...... accepted_pps={acc}, tx={tx}   no traffic")
            problems.append("check Wi-Fi association and traffic mode")
    elif acc is not None:
        print(f"  CSI flow ...... accepted_pps={acc}   OK (generator={gen})")

    # 4) Occupancy — fraction of the detector window with valid CSI.
    if isinstance(occ, (int, float)):
        if occ >= 0.7:
            print(f"  Occupancy ..... {occ:.0%}   OK")
        else:
            print(f"  Occupancy ..... {occ:.0%}   low (want > 70%)")

    # 5) Calibration / detector readiness.
    if calibrating:
        print("  Detector ...... calibrating now...")
    elif not ready:
        print("  Detector ...... NOT ready")
        problems.append("recalibrate in an EMPTY room: `calibrate`")
    elif isinstance(thr, (int, float)) and thr > 0.3:
        print(f"  Detector ...... ready but threshold={thr:.3f}   TOO HIGH (calibrated while the room was active)")
        problems.append("recalibrate with the room EMPTY: `calibrate`")
    else:
        print(f"  Detector ...... ready, threshold={round(thr or 0, 4)}   OK")

    print()
    if problems:
        print(f"  => {len(problems)} issue(s) found — fix in this order:")
        for i, p in enumerate(problems, 1):
            print(f"     {i}. {p}")
    else:
        print("  => Healthy. Run `watch` and walk near the device to confirm detection.")


def main():
    ap = argparse.ArgumentParser(description="Tune an ESPectre CSI sensor over Direct HTTP.")
    ap.add_argument("--host", required=True, help="device IP on the local network")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("scan")
    p = sub.add_parser("pin"); p.add_argument("bssid")
    p = sub.add_parser("traffic"); p.add_argument("mode", choices=["ping", "dns", "dns_tcp"])
    sub.add_parser("calibrate")
    sub.add_parser("watch")
    sub.add_parser("doctor")
    a = ap.parse_args()
    try:
        {"status": lambda: cmd_status(a.host),
         "scan": lambda: cmd_scan(a.host),
         "pin": lambda: cmd_pin(a.host, a.bssid),
         "traffic": lambda: cmd_traffic(a.host, a.mode),
         "calibrate": lambda: cmd_calibrate(a.host),
         "watch": lambda: cmd_watch(a.host),
         "doctor": lambda: cmd_doctor(a.host)}[a.cmd]()
    except KeyboardInterrupt:
        pass
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {a.host}:{PORT} -- same network? guest networks block this. ({e})")


if __name__ == "__main__":
    main()
