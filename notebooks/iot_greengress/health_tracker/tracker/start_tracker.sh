#!/usr/bin/env bash

tracker_dir=$(pwd)

cd ${tracker_dir}
cd ..
screen -S heartrate -h 200 -d -m python \
-m tracker.ggd.heartrate heartrate_ggd \
/health/groups/tracker/cfg.json \
/health/groups/tracker/ggd/certs/root-ca.pem \
/health/groups/tracker/ggd/certs/heartrate_ggd.pem \
/health/groups/tracker/ggd/certs/heartrate_ggd.prv \
/health/groups/tracker/ggd/certs 
screen -S heartbeat -h 200 -d -m python \
-m tracker.ggd.heartbeat heartbeat_ggd \
/health/groups/tracker/cfg.json \
/health/groups/tracker/ggd/certs/root-ca.pem \
/health/groups/tracker/ggd/certs/heartbeat_ggd.pem \
/health/groups/tracker/ggd/certs/heartbeat_ggd.prv \
/health/groups/tracker/ggd/certs --frequency 0.1
screen -S web -h 200 -d -m python \
-m tracker.ggd.web web_ggd \
/health/groups/tracker/cfg.json \
/health/groups/tracker/ggd/certs/root-ca.pem \
/health/groups/tracker/ggd/certs/web_ggd.pem \
/health/groups/tracker/ggd/certs/web_ggd.prv \
/health/groups/tracker/ggd/certs
screen -ls
cd ${tracker_dir}
