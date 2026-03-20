# Swarm SITL Simulation & Attack Research

Multi-drone swarm simulation using ArduPilot SITL and PyMAVLink for security research. A leader drone follows a predefined KML mission path while follower drones autonomously mirror its movements. Includes attack scripts demonstrating MAVLink protocol vulnerabilities in fleet communications.

## Dependencies

- Python 3.8+
- ArduPilot SITL
- QGroundControl (QGC)
- PyMAVLink

## Quick Start

### 1. Launch SITL Instances

Run each in a separate terminal:

```bash
sim_vehicle.py -v ArduCopter --sysid 1 -I 0 --out udp:127.0.0.1:14550 --out udp:127.0.0.1:14551
sim_vehicle.py -v ArduCopter --sysid 2 -I 1 --out udp:127.0.0.1:14560 --out udp:127.0.0.1:14561
sim_vehicle.py -v ArduCopter --sysid 3 -I 2 --out udp:127.0.0.1:14570 --out udp:127.0.0.1:14571
sim_vehicle.py -v ArduCopter --sysid 4 -I 3 --out udp:127.0.0.1:14580 --out udp:127.0.0.1:14581
```

### 2. Convert KML Mission to Waypoints

Place your `.kml` file inside `swarm_sitl/kml/`, then:

```bash
cd swarm_sitl/scripts
python3 kml_to_wpl.py ../kml/input.kml ../missions/drone1.wpl 30
```

### 3. Run Swarm

Upload the generated `.wpl` file to Vehicle 1 in QGC Plan view, then:

```bash
python3 swarm_follow.py
```

Take off and start the mission for Vehicle 1 in QGC. Followers track automatically.

## Attack Scripts

| Script | Threat Model | Description |
|--------|-------------|-------------|
| `rogue_gcs_sim.py` | Compromised GCS | Injects LAND/RTL/DISARM/GOTO to leader and follower mid-mission |
| `mavproxy_bus_attack.py` | Malicious drone as rogue GCS | Spoofs sysid=255 on shared MAVProxy bus to inject commands into Drone3 |
| `compromised_drone_spoof.py` | Drone sysid spoofing | Compromised Drone2 forges GCS identity to attack Drone3 |
| `malicious_gcs.py` | Compromised GCS (basic) | Direct command injection to leader+follower pair |

### Running an Attack (example)

```bash
# Start 3 SITL instances (sysid 1-3), then:
python3 rogue_gcs_sim.py
# Follow prompts to inject attacks mid-mission
```

## Project Structure

```
├── swarm_follow.py              # Swarm leader-follower coordination
├── kml_to_wpl.py                # KML to QGC waypoint converter
├── rogue_gcs_sim.py             # Compromised GCS attack
├── mavproxy_bus_attack.py       # Shared bus sysid spoof attack
├── compromised_drone_spoof.py   # Drone-level sysid spoof attack
└── malicious_gcs.py             # Basic compromised GCS attack
```
