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

# update shadow when Lambda instantiated to ensure GET returns info to devices
gg_client.update_thing_shadow(
    thingName=GGC_SHADOW_NAME, payload=json.dumps({
        "state": {
            "reported": {
                "lambda_start": datetime.datetime.now().isoformat()
            }
        }
    }).encode()
)


def handle_button(msg):
    button_id = msg['data'][0]['sensor_id']
    value = msg['data'][0]['value']
    log.info("[handle_button] button id:'{0}' value:'{1}'".format(
        button_id, value))
    if button_id == 'green-button' and value == 'on':
        log.info(
            "[handle_button] button id:'{0}' start_cmd".format(button_id))
        gg_client.update_thing_shadow(
            thingName=GGC_SHADOW_NAME, payload=json.dumps({
                "state": {
                    "desired": {
                        "convey_cmd": "run"
                    }
                }
            }).encode()
        )
    elif button_id == 'red-button' and value == 'on':
        log.info(
            "[handle_button] button id:'{0}' stop_cmd".format(button_id))
        gg_client.update_thing_shadow(
            thingName=GGC_SHADOW_NAME, payload=json.dumps({
                "state": {
                    "desired": {
                        "convey_cmd": "stop"
                    }
                }
            }).encode()
        )
    elif button_id == 'white-button' and value == 'on':
        log.info(
            "[handle_button] button id:'{0}' convey_reverse=True".format(
                button_id
            ))
        gg_client.update_thing_shadow(
            thingName=GGC_SHADOW_NAME, payload=json.dumps({
                "state": {
                    "desired": {
                        "convey_reverse": 1
                    }
                }
            }).encode()
        )
    elif button_id == 'white-button' and value == 'off':
        log.info(
            "[handle_button] button id:'{0}' convey_reverse=False".format(
                button_id
            ))
        gg_client.update_thing_shadow(
            thingName=GGC_SHADOW_NAME, payload=json.dumps({
                "state": {
                    "desired": {
                        "convey_reverse": 0
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

    if ggd_id == "button_ggd":
        handle_button(msg)
    elif ggd_id == "bp_ggd":
        log.debug("[handler] message from the blood pressure device")
    elif ggd_id == "hr_ggd":
        log.debug("[handler] message from the heart rate device")        
    else:
        log.error("[handler] unknown ggd_id:{0}".format(ggd_id))

