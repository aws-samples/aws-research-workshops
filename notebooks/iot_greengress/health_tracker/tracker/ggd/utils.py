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

import os
import socket
import logging
import traceback

from boto3.session import Session
from AWSIoTPythonSDK.core.protocol.connection.cores import \
    ProgressiveBackOffCore
from AWSIoTPythonSDK.exception.AWSIoTExceptions import \
    DiscoveryInvalidRequestException, DiscoveryFailure
from AWSIoTPythonSDK.exception import operationTimeoutException
from AWSIoTPythonSDK.core.greengrass.discovery.providers import \
    DiscoveryInfoProvider
from AWSIoTPythonSDK.MQTTLib import DROP_OLDEST, AWSIoTMQTTShadowClient
from gg_group_setup import GroupConfigFile


def get_aws_session(region, profile_name=None):
    if profile_name is None:
        logging.debug("loading AWS IoT client using 'default' AWS CLI profile")
        ses = Session(region_name=region)
    else:
        logging.debug(
            "loading AWS IoT client using '{0}' AWS CLI profile".format(
                profile_name))
        ses = Session(region_name=region, profile_name=profile_name)

    return ses


def mqtt_connect(mqtt_client, core_info):
    connected = False

    # try connecting to all connectivity info objects in the list
    for connectivity_info in core_info.connectivityInfoList:
        core_host = connectivity_info.host
        core_port = connectivity_info.port
        logging.info("Connecting to Core at {0}:{1}".format(
            core_host, core_port))
        mqtt_client.configureEndpoint(core_host, core_port)
        try:
            mqtt_client.connect()
            connected = True
            break
        except socket.error as se:
            print("SE:{0}".format(se))
        except operationTimeoutException as te:
            print("operationTimeoutException:{0}".format(te.message))
            traceback.print_tb(te, limit=25)
        except Exception as e:
            print("Exception caught:{0}".format(e.message))

    return connected


def local_shadow_connect(device_name, config_file, root_ca, certificate,
                         private_key, group_ca_dir):
    cfg = GroupConfigFile(config_file)
    ggd_name = cfg['devices'][device_name]['thing_name']
    iot_endpoint = cfg['misc']['iot_endpoint']

    dip = DiscoveryInfoProvider()
    dip.configureEndpoint(iot_endpoint)
    dip.configureCredentials(
        caPath=root_ca, certPath=certificate, keyPath=private_key
    )
    dip.configureTimeout(10)  # 10 sec
    logging.info(
        "[shadow_connect] Discovery using CA:{0} cert:{1} prv_key:{2}".format(
            root_ca, certificate, private_key
    ))
    gg_core, discovery_info = discover_configured_core(
        config_file=config_file, dip=dip, device_name=ggd_name,
    )
    if not gg_core:
        raise EnvironmentError("[core_connect] Couldn't find the Core")

    ca_list = discovery_info.getAllCas()
    core_list = discovery_info.getAllCores()
    group_id, ca = ca_list[0]
    core_info = core_list[0]
    logging.info("Discovered Greengrass Core:{0} from Group:{1}".format(
        core_info.coreThingArn, group_id)
    )
    group_ca_file = save_group_ca(ca, group_ca_dir, group_id)

    # local Greengrass Core discovered
    # get a shadow client to receive commands
    mqttsc = AWSIoTMQTTShadowClient(ggd_name)

    # now connect to Core from this Device
    logging.info("[core_connect] gca_file:{0} cert:{1}".format(
        group_ca_file, certificate))
    mqttsc.configureCredentials(group_ca_file, private_key, certificate)

    mqttc = mqttsc.getMQTTConnection()
    mqttc.configureOfflinePublishQueueing(10, DROP_OLDEST)
    if not mqtt_connect(mqttsc, gg_core):
        raise EnvironmentError("connection to Tracker Shadow failed.")

    # create and register the shadow handler on delta topics for commands
    # with a persistent connection to the Tracker shadow
    tracker_shadow = mqttsc.createShadowHandlerWithName(
        cfg['misc']['tracker_shadow_name'], True)

    return mqttc, mqttsc, tracker_shadow, ggd_name


def discover_configured_core(device_name, dip, config_file):
    cfg = GroupConfigFile(config_file)
    gg_core = None
    # Discover Greengrass Core

    discovered, discovery_info = ggc_discovery(
        device_name, dip, retry_count=10
    )
    logging.info("[discover_cores] Device: {0} discovery success".format(
        device_name)
    )

    # find the configured Group's core
    for group in discovery_info.getAllGroups():
        dump_core_info_list(group.coreConnectivityInfoList)
        gg_core = group.getCoreConnectivityInfo(cfg['core']['thing_arn'])

        if gg_core:
            logging.info('Found the configured core and Group CA.')
            break

    return gg_core, discovery_info


def ggc_discovery(thing_name, discovery_info_provider, retry_count=10,
                  max_groups=1):
    back_off_core = ProgressiveBackOffCore()
    discovered = False
    discovery_info = None

    while retry_count != 0:
        try:
            discovery_info = discovery_info_provider.discover(thing_name)
            group_list = discovery_info.getAllGroups()

            if len(group_list) > max_groups:
                raise DiscoveryFailure("Discovered more groups than expected")

            discovered = True
            break
        except DiscoveryFailure as df:
            logging.error(
                "Discovery failed! Error:{0} type:{1} message:{2}".format(
                    df, str(type(df)), df.message)
            )
            back_off = True
        except DiscoveryInvalidRequestException as e:
            logging.error("Invalid discovery request! Error:{0}".format(e))
            logging.error("Stopping discovery...")
            break
        except BaseException as e:
            logging.error(
                "Error in discovery:{0} type:{1} message:{2} thing_name:{3} "
                "dip:{4}".format(
                    e, str(type(e)), e.message, thing_name,
                    discovery_info_provider)
            )
            back_off = True

        if back_off:
            retry_count -= 1
            logging.info("{0} retries left\n".format(retry_count))
            logging.debug("Backing off...\n")
            back_off_core.backOff()

    return discovered, discovery_info


def save_group_ca(group_ca, group_ca_path, group_id):
    logging.info("[save_group_ca] saving file...")
    group_ca_file = group_ca_path + '/' + group_id + "_CA.crt"
    if not os.path.exists(group_ca_path):
        os.makedirs(group_ca_path)
    with open(group_ca_file, "w") as crt:
        crt.write(group_ca)
    logging.info('[save_group_ca] Saved CA file:{0}'.format(group_ca_file))

    return group_ca_file


def dump_core_info_list(core_connectivity_info_list):

    for cil in core_connectivity_info_list:
        print("  Core {0} has connectivity list".format(cil.coreThingArn, ))
        for ci in cil.connectivityInfoList:
            print("    Connection info: {0} {1} {2} {3}".format(
                ci.id, ci.host, ci.port, ci.metadata))


def get_conn_info(core_connectivity_info_list, match):
    """
    Get core connectivity info objects from the list. Matching any the `match`
    argument.

    :param core_connectivity_info_list: the connectivity info object list
    :param match: the value to match against either the Core Connectivity Info
        `id`, `host`, `port`, or `metadata` values
    :return: the list of zero or more matching connectivity info objects
    """
    conn_info = list()

    if not match:
        return conn_info

    for cil in core_connectivity_info_list:
        for ci in cil.connectivityInfoList:
            if match == ci.id or match == ci.host or match == ci.port or \
                            match == ci.metadata:
                conn_info.append(ci)

    return conn_info
