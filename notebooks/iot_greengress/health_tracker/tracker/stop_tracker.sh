#!/usr/bin/env bash

screen -S heartrate -X at "#" stuff ^C
screen -S heartbeat -X at "#" stuff ^C
screen -S web -X at "#" stuff ^C
echo "all gg devices stopped"