#!/usr/bin/env bash

echo "[begin] copying GGC certs"
echo "sudo cp certs/tracker-core.pem /greengrass/certs"
sudo cp certs/tracker-core.pem /greengrass/certs
echo "sudo cp certs/tracker-core.prv /greengrass/certs"
sudo cp certs/tracker-core.prv /greengrass/certs
echo "sudo cp certs/root-ca.pem /greengrass/certs"
sudo cp certs/root-ca.pem /greengrass/certs
echo "[end] copying GGC certs"
