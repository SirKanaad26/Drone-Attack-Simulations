## Running multiple drones

An example to run drones using PX4 and Gazebo. Run these commands in 4 separate terminals:

### Starting the Gazebo server
```
$ path_to_PX4/PX4-Autopilot/build/px4_sitl_default/rootfs/gz_env.sh
$ source build/px4_sitl_default/rootfs/gz_env.sh gz sim -s -r Tools/simulation/gz/worlds/default.sdf
```

### Start the Gazebo GUI
```
$ path_to_PX4/PX4-Autopilot/build/px4_sitl_default/rootfs/gz_env.sh
$ source build/px4_sitl_default/rootfs/gz_env.sh gz sim -g
```

### Launch PX4 instance 1 (drone at origin)
```
$ path_to_PX4/PX4-Autopilot/build/px4_sitl_default/rootfs/gz_env.sh
$ source build/px4_sitl_default/rootfs/gz_env.sh PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="0,0,0,0,0,0" PX4_GZ_MODEL=x500 ./build/px4_sitl_default/bin/px4 -i 1 -d build/px4_sitl_default/etc
```

### Launch PX4 instance 2 (drone 3m away)
```
$ path_to_PX4/PX4-Autopilot/build/px4_sitl_default/rootfs/gz_env.sh
$ source build/px4_sitl_default/rootfs/gz_env.sh PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="3,0,0,0,0,0" PX4_GZ_MODEL=x500 ./build/px4_sitl_default/bin/px4 -i 2 -d build/px4_sitl_default/etc
```