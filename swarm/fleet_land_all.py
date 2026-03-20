import time
from pymavlink import mavutil

# Typical command ports for 5 drones
cmd_ports = [14580, 14581, 14582, 14583, 14584]

def land_on_port(port, target_sys):
    cmd = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}")
    cmd.mav.command_long_send(
        target_sys, 1,  # compid 1 is autopilot
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0,
        0,0,0,0,0,0,0
    )

# Option A: targeted (send to sysid matching drone index)
for i, port in enumerate(cmd_ports, start=1):
    land_on_port(port, target_sys=i)
    print(f"Sent LAND to sysid={i} via cmd-port {port}")
    time.sleep(0.05)

print("Done. Verify modes/altitudes in QGC or via telemetry.")
