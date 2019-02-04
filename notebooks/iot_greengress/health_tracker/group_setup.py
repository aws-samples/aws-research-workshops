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

import fire
import json
import logging

from gg_group_setup import GroupConfigFile
from gg_group_setup import GroupCommands
from gg_group_setup import GroupType


logging.basicConfig(format='%(asctime)s|%(name)-8s|%(levelname)s: %(message)s',
                    level=logging.INFO)


class TrackerGroupType(GroupType):
    """
    Sub-class containing the definitions and subscriptions for the Tracker Group.
    """
    CORE_TYPE = 'tracker'

    def __init__(self, config=None, region='us-west-2', heartrate_name='heartrate_ggd',
                 web_name='web_ggd', heartbeat_name='heartbeat_ggd',
                 tracker_brain_shadow='tracker_brain'):
        super(TrackerGroupType, self).__init__(
            type_name=TrackerGroupType.CORE_TYPE, config=config, region=region
        )
        self.heartrate_ggd_name = heartrate_name
        self.web_ggd_name = web_name
        self.heartbeat_ggd_name = heartbeat_name
        self.tracker_brain_shadow = tracker_brain_shadow

    def get_core_definition(self, config):
        """
        Get the Tracker Group Type's core definition

        :param config: gg_group_setup.GroupConfigFile used with the Group Type
        :return: the core definition used to provision the group
        """
        cfg = config
        definition = [{
            "ThingArn": cfg['core']['thing_arn'],
            "CertificateArn": cfg['core']['cert_arn'],
            "Id": "{0}_00".format(self.type_name),  # arbitrary unique Id string
            "SyncShadow": True
        }]
        logging.debug('[tracker.get_core_definition] definition:{0}'.format(
            definition)
        )
        return definition

    def get_device_definition(self, config):
        """
        Get the Tracker Group Type's device definition

        :param config: gg_group_setup.GroupConfigFile used with the Group Type
        :return: the device definition used to provision the group
        """
        cfg = config
        definition = [
            {
                "Id": "{0}_12".format(self.type_name),
                "ThingArn": cfg['devices'][self.heartrate_ggd_name]['thing_arn'],
                "CertificateArn": cfg['devices'][self.heartrate_ggd_name][
                    'cert_arn'],
                "SyncShadow": cfg['devices'][self.heartrate_ggd_name]['cloud_sync']
            },
            {
                "Id": "{0}_15".format(self.type_name),
                "ThingArn":
                    cfg['devices'][self.heartbeat_ggd_name]['thing_arn'],
                "CertificateArn":
                    cfg['devices'][self.heartbeat_ggd_name]['cert_arn'],
                "SyncShadow":
                    cfg['devices'][self.heartbeat_ggd_name]['cloud_sync']
            },
            {
                "Id": "{0}_16".format(self.type_name),
                "ThingArn": cfg['devices'][self.web_ggd_name]['thing_arn'],
                "CertificateArn": cfg['devices'][self.web_ggd_name]['cert_arn'],
                "SyncShadow": cfg['devices'][self.web_ggd_name]['cloud_sync']
            },
            {
                "Id": "{0}_17".format(self.type_name),
                "ThingArn":
                    cfg['devices'][self.tracker_brain_shadow]['thing_arn'],
                "CertificateArn":
                    cfg['devices'][self.tracker_brain_shadow]['cert_arn'],
                "SyncShadow":
                    cfg['devices'][self.tracker_brain_shadow]['cloud_sync']
            }
        ]
        logging.debug('[tracker.get_device_definition] definition:{0}'.format(
            definition)
        )
        return definition

    def get_subscription_definition(self, config):
        """
        Get the Tracker Group Type's subscription definition

        :param config: gg_group_setup.GroupConfigFile used with the Group Type
        :return: the subscription definition used to provision the group
        """
        cfg = config
        d = cfg['devices']
        l = cfg['lambda_functions']
        s = cfg['subscriptions']

        definition = [
            {  # from TrackerErrorDetector to TrackerBrain Lambda
                "Id": "12",
                "Source": l['TrackerErrorDetector']['arn'],
                "Subject": s['errors'],
                "Target": l['TrackerBrain']['arn']
            },
            {  # from TrackerErrorDetector to AWS cloud
                "Id": "13",
                "Source": l['TrackerErrorDetector']['arn'],
                "Subject": s['errors'],
                "Target": "cloud"
            },
            {  # from Tracker web device to Greengrass Core local shadow
                "Id": "16",
                "Source": d[self.web_ggd_name]['thing_arn'],
                "Subject": "$aws/things/"+ self.tracker_brain_shadow + "/shadow/get",
                "Target": "GGShadowService"
            },
            {  # from Greengrass Core local shadow to Tracker web device
                "Id": "17",
                "Source": "GGShadowService",
                "Subject": "$aws/things/"+ self.tracker_brain_shadow + "/shadow/get/#",
                "Target": d[self.web_ggd_name]['thing_arn']
            },
            {  # from Tracker heartbeat device to AWS cloud
                "Id": "97",
                "Source": d[self.heartbeat_ggd_name]['thing_arn'],
                "Subject": "heart/beat",
                "Target": "cloud"
            },
            {  # from Tracker heart rate device to TrackerBrain Lambda
                "Id": "98",
                "Source": d[self.heartrate_ggd_name]['thing_arn'],
                "Subject": "hr",
                "Target": l['TrackerBrain']['arn']
            }
        ]
        logging.debug(
            '[tracker.get_subscription_definition] definition:{0}'.format(
                definition)
        )
        return definition


class TrackerGroupCommands(GroupCommands):

    def __init__(self):
        super(TrackerGroupCommands, self).__init__(group_types={
            TrackerGroupType.CORE_TYPE: TrackerGroupType
        })

    @staticmethod
    def associate_lambda(group_config, lambda_config):
        """
        Associate the Lambda described in the `lambda_config` with the
        Greengrass Group described by the `group_config`

        :param group_config: `gg_group_setup.GroupConfigFile` to store the group
        :param lambda_config: the configuration describing the Lambda to
            associate with the Greengrass Group

        :return:
        """
        with open(lambda_config, "r") as f:
            cfg = json.load(f)

        config = GroupConfigFile(config_file=group_config)

        lambdas = config['lambda_functions']
        lambdas[cfg['func_name']] = {
            'arn': cfg['lambda_arn'],
            'arn_qualifier': cfg['lambda_alias']
        }

        config['lambda_functions'] = lambdas


if __name__ == '__main__':
    """
    Instantiate a subclass of the `gg_group_setup.GroupCommands` object that 
    uses the two sub-classed GroupType classes. 
    
    The sub-class of GroupCommands will then use the sub-classed GroupTypes to 
    expose the `create`, `deploy`, `clean-all`, `clean-file`, etc. commands.
    
    Note: executing `clean-file` will result in stranded provisioned artifacts 
    in the AWS Greengrass service. These will artifacts will need manual 
    removal.
    """
    fire.Fire(TrackerGroupCommands())
