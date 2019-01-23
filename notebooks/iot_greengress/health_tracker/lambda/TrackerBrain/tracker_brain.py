#
# Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

from __future__ import print_function
import json
import logging
import datetime
import greengrasssdk

log = logging.getLogger('brain')
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s|%(name)-8s|%(levelname)s: %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)
log.setLevel(logging.INFO)

GGC_SHADOW_NAME = "tracker_brain"

gg_client = greengrasssdk.client('iot-data')

gg_client.update_thing_shadow(
    thingName=GGC_SHADOW_NAME, payload=json.dumps({
        "state": {
            "reported": {
                "lambda_start": datetime.datetime.now().isoformat()
            }
        }
    }).encode()
)


def handle_heartrate(msg):
    hr_id = msg['data'][0]['sensor_id']
    value = msg['data'][0]['value']
    log.info("[handle_hr] hr id:'{0}' value:'{1}'".format(hr_id, value))
    gg_client.update_thing_shadow(
        thingName=GGC_SHADOW_NAME, payload=json.dumps({
            "state": {
                "desired": {
                    "heartrate": value
                }
            }
        }).encode()
    )

# Handler for processing lambda work items
def handler(event, context):
    log.debug("[handler] raw event:{0}".format(event))
    # Unwrap the message
    log.debug("[handler] context.function_name:{0}".format(
        context.function_name))
    log.debug("[handler] context.client_context:{0}".format(
        context.client_context))
    msg = json.loads(event)
    # topic = context.client_context.custom['subject']

    ggd_id = ''
    if 'ggd_id' in msg:
        ggd_id = msg['ggd_id']

    if ggd_id == "hr_ggd":
        handle_heartrate(msg)
    elif ggd_id == "bp_ggd":
        log.debug("[handler] message from the blood pressure device")
    else:
        log.error("[handler] unknown ggd_id:{0}".format(ggd_id))

