#!/usr/bin/env python

# Copyright 2017 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not
# use this file except in compliance with the License. A copy of the License is
# located at
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed on
# an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""
Greengrass Heartbeat device

This Greengrass device simply provides a stream of heartbeat messages. These
messages can be useful when debugging the overall IoT solution as they are the
simplest messages being sent, on the simplest path.

This device expects to be launched from a command line. To learn more about that
command line type: `python heartbeat.py --help`
"""

import os
import json
import time
import random
import socket
import argparse
import datetime
import logging

from AWSIoTPythonSDK.core.greengrass.discovery.providers import \
    DiscoveryInfoProvider
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient, DROP_OLDEST
import utils
from gg_group_setup import GroupConfigFile


dir_path = os.path.dirname(os.path.realpath(__file__))
heartbeat_topic = 'heart/beat'

log = logging.getLogger('heartbeat')
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s|%(name)-8s|%(levelname)s: %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)
log.setLevel(logging.INFO)


def core_connect(device_name, config_file, root_ca, certificate, private_key, group_ca_path):
    # read the config file
    cfg = GroupConfigFile(config_file)

    # determine heartbeat device's thing name and orient MQTT client to GG Core
    heartbeat_name = cfg['devices'][device_name]['thing_name']
    iot_endpoint = cfg['misc']['iot_endpoint']
    local_core = None

    # Discover Greengrass Core
    dip = DiscoveryInfoProvider()
    dip.configureEndpoint(iot_endpoint)
    dip.configureCredentials(
        caPath=root_ca, certPath=certificate, keyPath=private_key
    )
    dip.configureTimeout(10)  # 10 sec
    log.info("[hb] Discovery using CA: {0} cert: {1} prv_key: {2}".format(
        root_ca, certificate, private_key
    ))
    # Now discover the groups in which this device is a member.
    # The heartbeat should only be in one group
    discovered, discovery_info = utils.ggc_discovery(
        heartbeat_name, dip, retry_count=10, max_groups=1
    )

    ca_list = discovery_info.getAllCas()
    group_id, ca = ca_list[0]
    group_ca_file = utils.save_group_ca(ca, group_ca_path, group_id)

    if discovered is False:
        log.error(
            "[hb] Discovery failed for: {0} when connecting to "
            "service endpoint: {1}".format(
                heartbeat_name, iot_endpoint
            ))
        return
    log.info("[hb] Discovery success")

    mqttc = AWSIoTMQTTClient(heartbeat_name)

    # find this device Group's core
    for group in discovery_info.getAllGroups():
        utils.dump_core_info_list(group.coreConnectivityInfoList)
        local_core = group.getCoreConnectivityInfo(cfg['core']['thing_arn'])
        if local_core:
            log.info('[hb] Found the local core and Group CA.')
            break

    if not local_core:
        raise EnvironmentError("[hb] Couldn't find the local Core")

    # local Greengrass Core discovered, now connect to Core from this Device
    log.info("[hb] gca_file:{0} cert:{1}".format(group_ca_file, certificate))
    mqttc.configureCredentials(group_ca_file, private_key, certificate)
    mqttc.configureOfflinePublishQueueing(10, DROP_OLDEST)

    if not utils.mqtt_connect(mqtt_client=mqttc, core_info=local_core):
        raise EnvironmentError("[hb] Connection to GG Core MQTT failed.")

    return mqttc, heartbeat_name


def heartbeat(mqttc, heartbeat_name, topic):
    # MQTT client has connected to GG Core, start heartbeat messages
    try:
        start = datetime.datetime.now()
        hostname = socket.gethostname()
        while True:
            now = datetime.datetime.now()
            msg = {
                "version": "2017-07-05",  # YYYY-MM-DD
                "ggd_id": heartbeat_name,
                "hostname": hostname,
                "data": [
                    {
                        "sensor_id": "heartbeat",
                        "ts": now.isoformat(),
                        "duration": str(now - start)
                    }
                ]
            }
            print("[hb] publishing heartbeat msg: {0}".format(msg))
            mqttc.publish(topic, json.dumps(msg), 0)
            time.sleep(random.random() * 10)

    except KeyboardInterrupt:
        log.info("[hb] KeyboardInterrupt ... exiting heartbeat")
    mqttc.disconnect()
    time.sleep(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Greengrass device that generates heartbeat messages',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('device_name',
                        help="The heartbeat's GGD device_name.")
    parser.add_argument('config_file',
                        help="The config file.")
    parser.add_argument('root_ca',
                        help="Root CA File Path of Cloud Server Certificate.")
    parser.add_argument('certificate',
                        help="File Path of GGD Certificate.")
    parser.add_argument('private_key',
                        help="File Path of GGD Private Key.")
    parser.add_argument('group_ca_path',
                        help="The directory where the discovered Group CA will be saved.")
    parser.add_argument('--topic', default=heartbeat_topic,
                        help="Topic used to communicate heartbeat telemetry.")
    parser.add_argument('--frequency', default=3,
                        help="Frequency in seconds to send heartbeat messages.")

    args = parser.parse_args()

    mqtt_client, hb_name = core_connect(
        device_name=args.device_name,
        config_file=args.config_file, root_ca=args.root_ca,
        certificate=args.certificate, private_key=args.private_key,
        group_ca_path=args.group_ca_path
    )
    heartbeat(
        mqttc=mqtt_client, heartbeat_name=hb_name,
        topic=args.topic
    )
