### Stealth Attack

#### Steps:
1. In the `/PX4-Autopilot/` run `make px4_sitl jmavsim`
2. `cd collector`
3. Go to QGroundControl -> Application Settings -> Fly View -> Enable Virtual Joystick
4. Two joysticks will be visible on QGCS.
5. Get the drone to fly above a certain height (say 5m).
6. Run `python3 attacker_stealth.py --cmd-port 14580 --action LAND --trigger-alt ${HEIGHT}`
7. The HEIGHT variable is the height at which the attack needs to be observed.
8. Observe the drone height decreasing immediately.



source /Users/kanaaddeshpande/Documents/Drones/PX4-Autopilot/build/px4_sitl_default/rootfs/gz_env.sh
source build/px4_sitl_default/rootfs/gz_env.sh gz sim -s -r Tools/simulation/gz/worlds/default.sdf

source build/px4_sitl_default/rootfs/gz_env.sh gz sim -g

source build/px4_sitl_default/rootfs/gz_env.sh PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="0,0,0,0,0,0" PX4_GZ_MODEL=x500 ./build/px4_sitl_default/bin/px4 -i 1 -d build/px4_sitl_default/etc

source build/px4_sitl_default/rootfs/gz_env.sh PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="3,0,0,0,0,0" PX4_GZ_MODEL=x500 ./build/px4_sitl_default/bin/px4 -i 2 -d build/px4_sitl_default/etc
