#!/usr/bin/env python3
"""
Simulated compromised GCS — arms all drones, leader+follower command mirroring.
For security research and swarm resilience testing only.
"""
from pymavlink import mavutil
import time
import threading

LEADER_URI  = ("udp:127.0.0.1:14551", 1, "LEADER")
FOLLOWER_URI = ("udp:127.0.0.1:14561", 2, "FOLLOWER")

TAKEOFF_ALT = 10  # metres


# ── low-level helpers ────────────────────────────────────────────────────────

def connect(uri, name):
    print(f"[+] Connecting to {name} @ {uri}")
    c = mavutil.mavlink_connection(uri)
    c.wait_heartbeat()
    print(f"[+] Heartbeat from {name} (sysid={c.target_system})")
    return c


def set_mode(conn, sysid, mode_str):
    mode_id = conn.mode_mapping()[mode_str]
    conn.mav.set_mode_send(sysid, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id)


def arm(conn, sysid, name):
    print(f"[*] Arming {name}...")
    set_mode(conn, sysid, "GUIDED")
    time.sleep(1)
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 0, 0, 0, 0, 0, 0  # 1 = arm
    )
    # wait for armed heartbeat
    for _ in range(20):
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if msg and (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
            print(f"[+] {name} armed.")
            return True
        time.sleep(0.5)
    print(f"[-] {name} did not arm in time.")
    return False


def takeoff(conn, sysid, name, alt=TAKEOFF_ALT):
    print(f"[*] Taking off {name} to {alt}m...")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0, 0, 0, 0, 0, 0, 0, alt
    )


# ── commands sent to leader (and mirrored to follower) ───────────────────────

def cmd_land(conn, sysid, name):
    print(f"[!] LAND -> {name}")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0, 0, 0, 0, 0, 0, 0, 0
    )


def cmd_rtl(conn, sysid, name):
    print(f"[!] RTL -> {name}")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
        0, 0, 0, 0, 0, 0, 0, 0
    )


def cmd_disarm(conn, sysid, name):
    print(f"[!] DISARM -> {name}")
    conn.mav.command_long_send(
        sysid, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0  # 0=disarm, 21196=force
    )


def cmd_goto(conn, sysid, name, lat, lon, alt):
    print(f"[!] GOTO ({lat},{lon},{alt}m) -> {name}")
    conn.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
        0,          # time_boot_ms
        sysid, 0,   # target system/component
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,  # only position
        int(lat * 1e7), int(lon * 1e7), alt,
        0, 0, 0,    # velocity
        0, 0, 0,    # accel
        0, 0        # yaw, yaw_rate
    ))


COMMANDS = {
    "1": ("LAND",   cmd_land),
    "2": ("RTL",    cmd_rtl),
    "3": ("DISARM", cmd_disarm),
}


# ── position mirror thread (follower tracks leader's GPS) ────────────────────

def mirror_position(leader_conn, follower_conn, follower_sysid, stop_event):
    print("[*] Position mirror thread started.")
    while not stop_event.is_set():
        msg = leader_conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
        if not msg:
            continue
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt = msg.relative_alt / 1000.0  # mm -> m
        if alt < 0.5:
            continue
        cmd_goto(follower_conn, follower_sysid, "FOLLOWER", lat, lon, alt)
        time.sleep(0.2)


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    l_conn = connect(LEADER_URI[0],   LEADER_URI[2])
    f_conn = connect(FOLLOWER_URI[0], FOLLOWER_URI[2])

    l_sysid, l_name = LEADER_URI[1],   LEADER_URI[2]
    f_sysid, f_name = FOLLOWER_URI[1], FOLLOWER_URI[2]

    # arm & takeoff both
    for conn, sysid, name in [(l_conn, l_sysid, l_name), (f_conn, f_sysid, f_name)]:
        if arm(conn, sysid, name):
            time.sleep(0.5)
            takeoff(conn, sysid, name)

    print("[*] Waiting 5s for takeoff to stabilise...")
    time.sleep(5)

    # start follower position mirror in background
    stop_mirror = threading.Event()
    mirror_thread = threading.Thread(
        target=mirror_position,
        args=(l_conn, f_conn, f_sysid, stop_mirror),
        daemon=True
    )
    mirror_thread.start()

    print("\n--- Compromised GCS Active ---")
    print("Commands (sent to LEADER, mirrored to FOLLOWER automatically):")
    for k, (label, _) in COMMANDS.items():
        print(f"  {k} -> {label}")
    print("  q -> quit\n")

    while True:
        choice = input("Command: ").strip().lower()
        if choice == "q":
            stop_mirror.set()
            break
        if choice in COMMANDS:
            label, fn = COMMANDS[choice]
            fn(l_conn, l_sysid, l_name)
            time.sleep(0.1)
            fn(f_conn, f_sysid, f_name)
            print(f"[*] {label} sent to leader + follower.\n")
        else:
            print("Unknown command. Options: 1=LAND  2=RTL  3=DISARM  q=quit")
