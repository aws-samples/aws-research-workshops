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


from __future__ import print_function
import json
import greengrasssdk

gg_client = greengrasssdk.client('iot-data')


def check_obstruction(datum):
    present_speed = datum['present_speed']
    present_position = datum['present_position']
    present_load = datum['present_load']
    goal_position = datum['goal_position']
    moving = datum['moving']

    print("[check_obstruction] present speed:{0} position:{1} load:{2}".format(
        present_speed, present_position, present_load
    ))
    print("[check_obstruction] goal_position:{0} moving:{1}".format(
        goal_position, moving
    ))


# Handler for processing lambda work items
def handler(event, context):
    # Unwrap the message
    msg = json.loads(event)
    print("[error_detector] looking for errors")

    if 'data' in msg:
        for datum in msg['data']:
            check_obstruction(datum)
