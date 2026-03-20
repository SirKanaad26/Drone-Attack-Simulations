import time
import json
from pymavlink import mavutil

last_mode = None

PORT = 14540  # onboard port from your PX4 log
m = mavutil.mavlink_connection(f"udp:127.0.0.1:{PORT}")

m.wait_heartbeat()
print("✅ Connected (heartbeat)")

latest = {
    "pos": None,      # GLOBAL_POSITION_INT
    "att": None,      # ATTITUDE
    "batt": None,     # BATTERY_STATUS
    "hb": None,       # HEARTBEAT
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


# Read continuously, but only emit 2 times/second
emit_every_s = 0.5
next_emit = time.time() + emit_every_s

while True:
    msg = m.recv_match(blocking=True, timeout=1)
    if not msg:
        continue

    t = msg.get_type()

    if t == "GLOBAL_POSITION_INT":
        latest["pos"] = {
            "lat": msg.lat / 1e7,
            "lon": msg.lon / 1e7,
            "alt_m": msg.relative_alt / 1000.0,
            "vx_mps": msg.vx / 100.0,
            "vy_mps": msg.vy / 100.0,
            "vz_mps": msg.vz / 100.0,
            "hdg_deg": (msg.hdg / 100.0) if msg.hdg != 65535 else None,
        }

    elif t == "ATTITUDE":
        latest["att"] = {
            "roll": float(msg.roll),
            "pitch": float(msg.pitch),
            "yaw": float(msg.yaw),
            "rollspeed": float(msg.rollspeed),
            "pitchspeed": float(msg.pitchspeed),
            "yawspeed": float(msg.yawspeed),
        }

    elif t == "BATTERY_STATUS":
        latest["batt"] = {
            "remaining_pct": int(msg.battery_remaining),
        }

    elif t == "HEARTBEAT":
        # Filter to PX4 autopilot heartbeat only
        if msg.get_srcSystem() != 1 or msg.get_srcComponent() != 1:
                continue

        mode = mavutil.mode_string_v10(msg)
        if mode != last_mode:
                print("MODE =>", mode)
                last_mode = mode

        latest["hb"] = {
                "mode": mode,
                "base_mode": int(msg.base_mode),
                "custom_mode": int(msg.custom_mode),
                "system_status": int(msg.system_status),
        }

    # Emit a compact, normalized record at fixed rate
    now = time.time()
    if now >= next_emit and latest["pos"] and latest["att"]:
        record = {
            "ts": now_iso(),
            "drone_id": "drone_1",
            **latest["pos"],
            **latest["att"],
            **(latest["batt"] or {}),
            "hb": latest["hb"],
        }
        print(json.dumps(record))
        next_emit = now + emit_every_s
