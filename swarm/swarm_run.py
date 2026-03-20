#!/usr/bin/env python3
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

from pymavlink import mavutil

PX4_DEFAULT = os.path.expanduser("~/Documents/Drones/PX4-Autopilot/build/px4_sitl_default/bin/px4")
ROMFS_DEFAULT = os.path.expanduser("~/Documents/Drones/PX4-Autopilot/ROMFS/px4fmu_common")

# Your branch's observed port scheme (from your logs):
# telemetry listen ports (PX4 sends to these): 14540 + instance
TEL_BASE = 14540
# command ports (PX4 listens on these): 14580 + instance
CMD_BASE = 14580


def launch_px4_instances(px4_bin: str, romfs: str, n: int, work_root: str) -> List[subprocess.Popen]:
    procs: List[subprocess.Popen] = []
    work_root_p = Path(work_root)
    work_root_p.mkdir(parents=True, exist_ok=True)

    for inst in range(n):
        wd = work_root_p / f"px4_{inst}"
        if wd.exists():
            shutil.rmtree(wd)
        wd.mkdir(parents=True, exist_ok=True)

        # IMPORTANT: we do NOT set PX4_SYS_AUTOSTART here because on your machine
        # that tends to force jMAVSim and block waiting for TCP. We want PX4 to come up
        # and give us a pxh prompt / MAVLink endpoints without waiting on a GUI sim.
        env = os.environ.copy()
        env["PX4_SIM_INSTANCE"] = str(inst)

        cmd = [px4_bin, romfs, "-i", str(inst)]
        print(f"🚀 Launching PX4 instance {inst} in {wd}")
        p = subprocess.Popen(
            cmd,
            cwd=str(wd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        procs.append(p)

    return procs


def wait_heartbeat_on_port(port: int, timeout_s: float = 20.0) -> mavutil.mavfile:
    m = mavutil.mavlink_connection(f"udp:127.0.0.1:{port}")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if hb:
            return m
    raise TimeoutError(f"No heartbeat on udp:127.0.0.1:{port} within {timeout_s}s")


def arm_and_takeoff(sysid: int, cmd_port: int, takeoff_alt_m: float) -> None:
    cmd = mavutil.mavlink_connection(f"udpout:127.0.0.1:{cmd_port}")

    # Force arm (works even if health checks complain in minimal sim)
    cmd.mav.command_long_send(
        sysid, 1,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1, 21196, 0, 0, 0, 0, 0  # param2=21196 is PX4 "force arm" magic
    )
    print(f"🟢 ARM sent (force) to sysid={sysid} via {cmd_port}")
    time.sleep(0.4)

    # Takeoff
    cmd.mav.command_long_send(
        sysid, 1,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0,
        0, 0, 0, 0,
        0, 0, takeoff_alt_m
    )
    print(f"🛫 TAKEOFF {takeoff_alt_m}m sent to sysid={sysid} via {cmd_port}")


def land(sysid: int, cmd_port: int) -> None:
    cmd = mavutil.mavlink_connection(f"udpout:127.0.0.1:{cmd_port}")
    cmd.mav.command_long_send(
        sysid, 1,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0,
        0, 0, 0, 0, 0, 0, 0
    )
    print(f"🟥 LAND sent to sysid={sysid} via {cmd_port}")


def read_altitude(m: mavutil.mavfile, timeout_s: float = 5.0) -> float:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        msg = m.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
        if msg:
            return msg.relative_alt / 1000.0
    return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--px4", type=str, default=PX4_DEFAULT)
    ap.add_argument("--romfs", type=str, default=ROMFS_DEFAULT)
    ap.add_argument("--work-root", type=str, default="/tmp/px4_swarm_py")
    ap.add_argument("--takeoff-alt", type=float, default=8.0)
    ap.add_argument("--attack-trigger-alt", type=float, default=6.0)
    ap.add_argument("--attack-src-sysid", type=int, default=2, help="when this drone exceeds trigger alt, attack fires")
    ap.add_argument("--attack-targets", type=str, default="1,3", help="comma sysids to LAND")
    args = ap.parse_args()

    procs = launch_px4_instances(args.px4, args.romfs, args.n, args.work_root)

    # Stream a bit of PX4 logs to help you see if something is blocking
    def dump_some_output(seconds=6):
        end = time.time() + seconds
        while time.time() < end:
            for i, p in enumerate(procs):
                if p.stdout is None:
                    continue
                line = p.stdout.readline()
                if line:
                    print(f"[px4-{i}] {line.rstrip()}")
            time.sleep(0.05)

    dump_some_output(6)

    # Connect telemetry for each instance
    telemetry: Dict[int, mavutil.mavfile] = {}
    for inst in range(args.n):
        tel_port = TEL_BASE + inst
        print(f"📡 Waiting heartbeat on telemetry port {tel_port} ...")
        m = wait_heartbeat_on_port(tel_port, timeout_s=30)
        sysid = m.target_system or (inst + 1)
        # PX4 SITL usually uses sysid = inst+1, but we'll read it from heartbeat:
        hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if hb:
            sysid = hb.get_srcSystem()
        telemetry[sysid] = m
        print(f"✅ Telemetry OK: sysid={sysid} on {tel_port}")

    # Arm + takeoff all drones
    for inst in range(args.n):
        sysid = inst + 1
        cmd_port = CMD_BASE + inst
        arm_and_takeoff(sysid=sysid, cmd_port=cmd_port, takeoff_alt_m=args.takeoff_alt)

    # Simple watcher loop: when src drone exceeds trigger alt, land targets
    attack_targets = [int(x.strip()) for x in args.attack_targets.split(",") if x.strip()]
    src_sysid = args.attack_src_sysid

    print(f"🕵️ Watching sysid={src_sysid} altitude; trigger > {args.attack_trigger_alt}m => LAND {attack_targets}")
    while True:
        m = telemetry.get(src_sysid)
        if not m:
            print(f"⚠️ No telemetry handle for src sysid {src_sysid}")
            break
        alt = read_altitude(m, timeout_s=2.0)
        if alt == alt:  # not NaN
            print(f"ALT sysid={src_sysid}: {alt:.2f}m")
            if alt > args.attack_trigger_alt:
                print("🚨 Trigger hit — executing fleet LAND")
                for t in attack_targets:
                    cmd_port = CMD_BASE + (t - 1)
                    land(sysid=t, cmd_port=cmd_port)
                break
        time.sleep(0.5)

    print("✅ Done. Leave PX4 running or Ctrl+C to exit.")

    # Keep alive until user interrupts
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("🧹 Shutting down PX4 processes...")
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        time.sleep(1)
        for p in procs:
            if p.poll() is None:
                p.kill()


if __name__ == "__main__":
    main()
