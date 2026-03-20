import time
from pymavlink import mavutil

ports = [14540,14541,14542,14543,14544]
conns = []

for p in ports:
    try:
        c = mavutil.mavlink_connection(f"udp:127.0.0.1:{p}")
        conns.append((p, c))
    except OSError as e:
        print(f"Bind failed on {p}: {e}")

print("Listening for heartbeats on:", ports)

seen = set()
t_end = time.time() + 5
while time.time() < t_end:
    for p, c in conns:
        hb = c.recv_match(type="HEARTBEAT", blocking=False)
        if not hb:
            continue
        key = (p, hb.get_srcSystem(), hb.get_srcComponent())
        if key in seen:
            continue
        seen.add(key)
        print(f"HB on port {p}: sysid={hb.get_srcSystem()} compid={hb.get_srcComponent()} mode={mavutil.mode_string_v10(hb)}")

print("Done.")
