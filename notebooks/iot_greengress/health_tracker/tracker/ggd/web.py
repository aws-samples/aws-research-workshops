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

import os
import json
import time
import argparse
import datetime as dt
import logging
import cachetools

from datetime import timedelta
from threading import Lock
from flask import Flask, request, render_template, Response, send_from_directory
from werkzeug.utils import secure_filename
from flask_cors import CORS, cross_origin

import utils

dir_path = os.path.dirname(os.path.realpath(__file__))

app = Flask(
    __name__, static_folder="flask/static", template_folder='flask/templates'
)
CORS(app)

UPLOAD_FOLDER = 'flask/uploads'
ALLOWED_EXTENSIONS = set('png')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

log = app.logger
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s|%(name)-8s|%(levelname)s: %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)
log.setLevel(logging.INFO)

mqttc = None
tracker_shadow = None
shady_vals = {}
topic_cache = cachetools.LRUCache(maxsize=50)
msg_cache = cachetools.LRUCache(maxsize=100)
second = timedelta(seconds=1)
last_hz = 0
incr_lock = Lock()
current_hz = 0
current_hz_time = dt.datetime.utcnow()
rollover_lock = Lock()

tracker_topics = [
    "/tracker/telemetry",
    "/tracker/errors"
]


def shadow_mgr(payload, status, token):
    if payload == "REQUEST TIME OUT":
        log.info(
            "[shadow_mgr] shadow 'REQUEST TIME OUT' tk:{0}".format(
                token))
        return
    global shady_vals
    shady_vals = json.loads(payload)
    log.debug("[shadow_mgr] shadow payload:{0} token:{1}".format(
        json.dumps(shady_vals, sort_keys=True), token))


def count_telemetry(data):
    i = 0
    for d in data:
        if 'ts' in d:
            i += 1

    global current_hz
    with incr_lock:
        current_hz += i

    log.debug('[count_telemetry] incrementing count by:{0}'.format(1))


def history(message):
    if 'ggd_id' in message and 'data' in message:
        key = message['ggd_id'] + '_' + message['data'][0]['ts']
        msg_cache[key] = message


def topic_update(client, userdata, message):
    log.debug('[topic_update] received topic:{0} ts:{1}'.format(
        message.topic, dt.datetime.utcnow()))
    topic_cache[message.topic] = message.payload

    msg = json.loads(message.payload)

    if 'data' in msg:
        global last_hz
        global current_hz
        global current_hz_time
        count_telemetry(msg['data'])
        elapsed = dt.datetime.utcnow() - current_hz_time
        if elapsed > second:  # if a second has passed rollover Hz
            with rollover_lock:
                last_hz = current_hz
                current_hz_time = dt.datetime.utcnow()
                current_hz = 0

    history(msg)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1] in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    freq = 8

    heartbeat = {
        "duration": "0:06:58.589397",
        "sensor_id": "heartbeat",
        "ts": "2017-05-10T07:32:51.631351",
        "age": "0:00:10.000000",
        "version": "2016-11-01",
        "ggd_id": "sh-pi3b-ggc_GGD_heartbeat",
        "hostname": "sh-pi3b"
    }

    logs = [{
        "payload": "some message here 1",
        "ts": "2017-05-02T05:30:51.631351"
    }, {
        "payload": "some message here 2",
        "ts": "2017-05-02T04:30:51.631352"
    }, {
        "payload": "some message here 3",
        "ts": "2017-05-02T03:30:51.631353"
    }, {
        "payload": "some message here 4",
        "ts": "2017-05-02T02:30:51.631354"
    }]

    images = {}

    return render_template(
        'index.html',
        freq=freq,
        heartbeat=heartbeat,
        logs=logs,
        images=images
    )


@app.route('/ui')
def root():
    return app.send_static_file('index.html')


@app.route('/hello/')
@app.route('/hello/<name>')
def hello(name=None):
    return render_template('hello.html', name=name)


@app.route('/shadow/get')
def get_shadow():
    token = tracker_shadow.shadowGet(shadow_mgr, 5)
    log.debug("[get_shadow] shadowGet() tk:{0}".format(token))
    return 'Sent request to get TrackerBrain shadow'


@app.route('/shadow/read')
def read_shadow():
    return shady_vals


@app.route('/upload', methods=['POST'])
def upload():
    log.info('[upload] request')
    if request.method == 'POST':
        log.info('[upload] POST request')
        if 'file' not in request.files:
            log.error('[upload] Upload attempt with no file')
            return Response('No file uploaded', status=500)

        f = request.files['file']
        if f.filename == '':
            log.error('[upload] Upload attempt with no filename')
            return Response('No filename uploaded', status=500)

        if f and allowed_file(f.filename):
            absolute_file = os.path.abspath(UPLOAD_FOLDER + f.filename)
            log.info('[upload] absolute_filename:{0}'.format(absolute_file))
            filename = secure_filename(absolute_file)
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            return Response('Uploaded file successfully', status=200)
    return


@app.route('/dashboard')
def dashboard():
    topic_dict = dict()
    
    return render_template('topic.html', topic_dict=topic_dict)


@app.route('/msg/frequency')
@app.route('/msg/frequency/all')
def frequency():
    js = json.dumps({"frequency": last_hz}, sort_keys=False)
    return Response(js, status=200, mimetype='application/json')

# TODO add specific station Hz metrics


@app.route('/msg/history')
@app.route('/msg/history/<count>')
def message_history(count=None):
    response = dict()
    keys = msg_cache.keys()
    log.debug('[message_history] history length:{0}'.format(len(keys)))
    for k in keys:
        response[k] = msg_cache[k]

    response['length'] = len(keys)
    js = json.dumps(response, sort_keys=True)
    log.debug('[message_history] response:{0}'.format(js))
    return Response(js, status=200, mimetype='application/json')


@app.route('/msg/topic/<path:topic>')
def latest_message(topic):
    top = '/' + topic
    log.debug('[latest_message] get topic:{0}'.format(top))
    if top in topic_cache:
        msg = topic_cache[top]
        return Response(msg, status=200, mimetype='application/json')
    else:
        return Response("Couldn't find topic:{0}".format(top),
                        status=200,
                        mimetype='application/json')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Web Greengrass Device (GGD)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('device_name',
                        help="The Web GGD device_name in the config file.")
    parser.add_argument('config_file',
                        help="The config file.")
    parser.add_argument('root_ca',
                        help="Root CA File Path of Cloud Server Certificate.")
    parser.add_argument('certificate',
                        help="File Path of Web GGD Certificate.")
    parser.add_argument('private_key',
                        help="File Path of Web GGD Private Key.")
    parser.add_argument('group_ca_dir',
                        help="The directory where the discovered Group CA will be saved.")
    parser.add_argument('--debug', default=False, action='store_true',
                        help="Activate debug output.")
    pa = parser.parse_args()

    try:
        if pa.debug:
            log.setLevel(logging.DEBUG)

        mqttc, shadow_client, mshadow, ggd_name = \
            utils.local_shadow_connect(
                device_name=pa.device_name,
                config_file=pa.config_file,
                root_ca=pa.root_ca, certificate=pa.certificate,
                private_key=pa.private_key, group_ca_dir=pa.group_ca_dir
        )

        token = mshadow.shadowGet(shadow_mgr, 5)
        logging.debug('[__main__] shadowGet() tk:{0}'.format(token))

        for t in tracker_topics:
            mqttc.subscribe(t, 1, topic_update)
            log.info('[__main__] subscribed to topic:{0}'.format(t))

        app.run(
            host="0.0.0.0",
            port=5000, use_reloader=False,
            debug=True
        )
    except KeyboardInterrupt:
        log.info("[__main__] KeyboardInterrupt ... shutting down")

    if mqttc:
        mqttc.disconnect()
    time.sleep(1)
