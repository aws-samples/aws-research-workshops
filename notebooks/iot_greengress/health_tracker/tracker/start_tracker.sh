#!/usr/bin/env bash

tracker_dir=$(pwd)

cd ggd
chmod 755 load_gg_profile.sh
./load_gg_profile.sh

cd ${tracker_dir}
cd ..
screen -S heartrate -h 200 -d -m python \
-m tracker.ggd.heartrate heartrate_ggd \
~/health_tracker/tracker/cfg.json \
~/health_tracker/tracker/ggd/certs/root-ca.pem \
~/health_tracker/tracker/ggd/certs/heartrate_ggd.pem \
~/health_tracker/tracker/ggd/certs/heartrate_ggd.prv \
~/health_tracker/tracker/ggd/certs 
screen -S heartbeat -h 200 -d -m python \
-m tracker.ggd.heartbeat heartbeat_ggd \
~/health_tracker/tracker/cfg.json \
~/health_tracker/tracker/ggd/certs/root-ca.pem \
~/health_tracker/tracker/ggd/certs/heartbeat_ggd.pem \
~/health_tracker/tracker/ggd/certs/heartbeat_ggd.prv \
~/health_tracker/tracker/ggd/certs --frequency 0.1
screen -S web -h 200 -d -m python \
-m tracker.ggd.web web_ggd \
~/health_tracker/tracker/cfg.json \
~/health_tracker/tracker/ggd/certs/root-ca.pem \
~/health_tracker/tracker/ggd/certs/web_ggd.pem \
~/health_tracker/tracker/ggd/certs/web_ggd.prv \
~/health_tracker/tracker/ggd/certs
screen -ls
cd ${tracker_dir}
