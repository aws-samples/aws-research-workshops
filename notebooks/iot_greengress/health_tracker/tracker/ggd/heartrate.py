#!/usr/bin/env python

"""
GGD Heart Rate
This GGD will send heart rate messages.
"""
import os
import json
import time
import socket
import argparse
import datetime
import logging

from random import *
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient, DROP_OLDEST
from AWSIoTPythonSDK.core.greengrass.discovery.providers import DiscoveryInfoProvider
import utils
from gg_group_setup import GroupConfigFile

dir_path = os.path.dirname(os.path.realpath(__file__))

log = logging.getLogger('heartrate')
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s|%(name)-8s|%(levelname)s: %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)
log.setLevel(logging.INFO)

GGD_HR_TOPIC = "heartrate"

hostname = socket.gethostname()
mqttc = None
ggd_name = None

def heartrate(sensor_id):
    # MQTT client has connected to GG Core, start heartbeat messages
    try:
        start = datetime.datetime.now()
        hostname = socket.gethostname()
        while True:
            now = datetime.datetime.now()

            val = randint(60, 100)

            msg = {
                "version": "2017-07-05",  # YYYY-MM-DD
                "ggd_id": ggd_name,
                "hostname": hostname,
                "data": [
                    {
                        "sensor_id": sensor_id,
                        "ts": now.isoformat(),
                        "value": val
                    }
                ]
            }
            print("[hb] publishing heartrate msg: {0}".format(msg))
            mqttc.publish(GGD_HR_TOPIC, json.dumps(msg), 0)
            time.sleep(random() * 10)

    except KeyboardInterrupt:
        log.info("[hb] KeyboardInterrupt ... exiting heartrate")
    mqttc.disconnect()
    time.sleep(2)

def core_connect(device_name, config_file, root_ca, certificate, private_key, group_ca_path):
    global ggd_name, mqttc
    cfg = GroupConfigFile(config_file)
    ggd_name = cfg['devices'][device_name]['thing_name']
    iot_endpoint = cfg['misc']['iot_endpoint']

    dip = DiscoveryInfoProvider()
    dip.configureEndpoint(iot_endpoint)
    dip.configureCredentials(
        caPath=root_ca, certPath=certificate, keyPath=private_key
    )
    dip.configureTimeout(10)  # 10 sec
    logging.info("[heartrate] Discovery using CA:{0} cert:{1} prv_key:{2}".format(
        root_ca, certificate, private_key
    ))

    gg_core, discovery_info = utils.discover_configured_core(
        device_name=device_name, dip=dip, config_file=config_file,
    )
    if not gg_core:
        raise EnvironmentError("[heartrate] Couldn't find the Core")

    ca_list = discovery_info.getAllCas()
    group_id, ca = ca_list[0]
    group_ca_file = utils.save_group_ca(ca, group_ca_path, group_id)

    mqttc = AWSIoTMQTTClient(ggd_name)
    # local Greengrass Core discovered, now connect to Core from this Device
    log.info("[heartrate] gca_file:{0} cert:{1}".format(
        group_ca_file, certificate))
    mqttc.configureCredentials(group_ca_file, private_key, certificate)
    mqttc.configureOfflinePublishQueueing(10, DROP_OLDEST)

    return mqttc, gg_core


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Tracker GGD and CLI heart rate',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('device_name',
                        help="The GGD device_name in the config file.")
    parser.add_argument('config_file',
                        help="The config file.")
    parser.add_argument('root_ca',
                        help="Root CA File Path of Cloud Server Certificate.")
    parser.add_argument('certificate',
                        help="File Path of GGD Certificate.")
    parser.add_argument('private_key',
                        help="File Path of GGD Private Key.")
    parser.add_argument('group_ca_path',
                        help="The directory path where the discovered Group CA will be saved.")

    pa = parser.parse_args()

    client, core = core_connect(
        device_name=pa.device_name,
        config_file=pa.config_file, root_ca=pa.root_ca,
        certificate=pa.certificate, private_key=pa.private_key,
        group_ca_path=pa.group_ca_path
    )

    if utils.mqtt_connect(mqtt_client=client, core_info=core):
        heartrate('user1')