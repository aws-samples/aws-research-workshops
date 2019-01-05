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

from __future__ import print_function
import os
import fire
import json
import boto3
import logging
import zipfile
from os.path import basename
from datetime import datetime
from datetime import timedelta, tzinfo
from botocore.exceptions import ClientError
from retrying import retry

dir_path = os.path.dirname(os.path.realpath(__file__))

logging.basicConfig(format='%(asctime)s|%(name)-8s|%(levelname)s: %(message)s',
                    level=logging.INFO)

print("path: {0}".format(dir_path))
temp_deploy_zip = "deploy.zip"


def create(lambda_config, runtime='python2.7', role_name='NoServiceAccess',
           role_policy='policy.json', assume_role_policy_doc='trust.json'):

    with open(lambda_config, "r") as in_file:
        cfg = json.load(in_file)

    func_name = cfg['func_name']
    func_desc = cfg['func_desc']
    lambda_alias = cfg['lambda_alias']
    abs_lambda_dir = dir_path + '/' + cfg['lambda_dir']
    lambda_handler = cfg['lambda_handler']
    lambda_files = cfg['lambda_files']
    lambda_main = cfg['lambda_main']

    role_arn = _create_lambda_policies(assume_role_policy_doc,
                                       func_name=func_name,
                                       lambda_dir=abs_lambda_dir,
                                       role_name=role_name,
                                       role_policy=role_policy)

    refresh_lambda_zip(lambda_files, abs_lambda_dir)
    lambda_resp = _create_lambda(
        role_arn, func_name, func_desc, lambda_handler, lambda_main, runtime
    )
    _publish_lambda_version(func_arn=lambda_resp['FunctionArn'])
    alias_resp = _create_function_alias(
        func_alias=lambda_alias,
        func_name=func_name,
        func_version=lambda_resp['Version']
    )
    cfg['lambda_arn'] = alias_resp['AliasArn']
    with open(lambda_config, "w") as out_file:
        json.dump(
            cfg, out_file, indent=2,
            separators=(',', ': '), sort_keys=True
        )

    os.remove(temp_deploy_zip)


def _create_lambda_policies(assume_role_policy_doc, func_name, lambda_dir,
                            role_name, role_policy):
    iam = boto3.client('iam')
    role_arn = ''
    try:
        tf = lambda_dir + '/' + assume_role_policy_doc
        with open(tf) as trust_file:
            trust = json.dumps(json.load(trust_file))
            resp = iam.create_role(RoleName=role_name,
                                   # Path=dir_path+'/',
                                   AssumeRolePolicyDocument=trust)
            role_arn = resp['Role']['Arn']

        logging.info('created iam role:{0} with arn:{1}'.format(
            role_name, role_arn))
    except ClientError as ce:
        if ce.response['Error']['Code'] == 'EntityAlreadyExists':
            logging.warning(
                "Role '{0}' already exists. Using existing Role".format(
                    role_name))
            role = iam.get_role(RoleName=role_name)
            role_arn = role['Role']['Arn']
        else:
            logging.error("Unexpected Error: {0}".format(ce))
    try:
        pf = lambda_dir + '/' + role_policy
        with open(pf) as policy_file:
            policy = json.dumps(json.load(policy_file))
            resp = iam.put_role_policy(RoleName=role_name,
                                       PolicyName=func_name + '_policy',
                                       PolicyDocument=policy)
    except ClientError as ce:
        if ce.response['Error']['Code'] == 'EntityAlreadyExists':
            logging.warning("Policy '{0}' already exists.".format(role_name))
        else:
            logging.error("Unexpected Error: {0}".format(ce))

    return role_arn


@retry(wait_random_min=4000, wait_random_max=6000, stop_max_attempt_number=3)
def _create_lambda(arn, func_name, func_desc, lambda_handler, lambda_main,
                   runtime):
    func = dict()
    lamb = boto3.client('lambda')
    with open(temp_deploy_zip) as deploy:
        func['ZipFile'] = deploy.read()
    try:
        resp = lamb.create_function(
            FunctionName=func_name, Runtime=runtime, Publish=True,
            Description=func_desc,
            Role=arn, Code=func, Handler='{0}.{1}'.format(
                lambda_main, lambda_handler
            ))
        logging.info("Create Lambda Function resp:{0}".format(
            json.dumps(resp, indent=4, sort_keys=True))
        )
        return resp
    except ClientError as ce:
        if ce.response['Error']['Code'] == 'ValidationException':
            logging.warning("Validation Error {0} creating function '{1}'.".format(
                ce, func_name))
        else:
            logging.error("Unexpected Error: {0}".format(ce))


def _publish_lambda_version(func_arn):
    pass


def _create_function_alias(func_alias, func_name, func_version):
    lamb = boto3.client('lambda')

    try:
        resp = lamb.create_alias(
            Name=func_alias,
            FunctionName=func_name,
            FunctionVersion=func_version
        )
        logging.info("Create Lambda Alias resp:{0}".format(
            json.dumps(resp, indent=4, sort_keys=True))
        )
        return resp
    except ClientError as ce:
        if ce.response['Error']['Code'] == 'ValidationException':
            logging.warning("Validation Error {0} creating alias '{1}'.".format(
                ce, func_alias))
        else:
            logging.error("Unexpected Error: {0}".format(ce))


def update(config_file, runtime='python2.7', role_name='NoServiceAccess',
           role_policy='policy.json', assume_role_policy_doc='trust.json'):
    lamb = boto3.client('lambda')

    now = datetime.now(tz=FixedOffset(0))
    with open(config_file, "r") as f:
        cfg = json.load(f)

    func_name = cfg['func_name']
    func_desc = cfg['func_desc']
    lambda_alias = cfg['lambda_alias']

    resp = lamb.get_function(
        FunctionName=func_name,
        Qualifier=lambda_alias
    )
    logging.debug("Get function resp:{0}".format(
        json.dumps(resp, indent=4, sort_keys=True))
    )
    lambda_dir = dir_path + '/' + cfg['lambda_dir']
    lambda_files = cfg['lambda_files']

    refresh_lambda_zip(lambda_files, lambda_dir)

    with open(temp_deploy_zip) as zip_file:
        func_version = _update_lambda_function(zip_file, func_name)
        _update_lambda_alias(lambda_alias, func_name, func_version)
        logging.info("Updated function {0} with new code as of {1}".format(
            func_name, now))


def _update_lambda_function(zip_file, func_name):
    lamb = boto3.client('lambda')
    try:
        resp = lamb.update_function_code(
            FunctionName=func_name,
            ZipFile=zip_file.read(),
            Publish=True
        )
        return resp['Version']
    except ClientError as ce:
        if ce.response['Error']['Code'] == 'ValidationException':
            logging.warning(
                "Validation Error {0} updating function '{1}'.".format(
                    ce, func_name))
        else:
            logging.error("Unexpected Error: {0}".format(ce))


def _update_lambda_alias(func_alias, func_name, func_version):
    lamb = boto3.client('lambda')
    try:
        resp = lamb.update_alias(
            Name=func_alias,
            FunctionName=func_name,
            FunctionVersion=func_version
        )
        return resp['AliasArn']
    except ClientError as ce:
        if ce.response['Error']['Code'] == 'ValidationException':
            logging.warning(
                "Validation Error {0} updating alias '{1}'.".format(
                    ce, func_name))
        else:
            logging.error("Unexpected Error: {0}".format(ce))


def refresh_lambda_zip(lambda_files, lambda_dir):
    with zipfile.ZipFile(temp_deploy_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in lambda_files:
            zf.write(lambda_dir + '/' + f, basename(f))


def string_as_datetime(time_str):
    """Expects timestamps inline with '2017-06-05T22:45:24.423+0000'"""
    # split the utc offset part
    naive_time_str, offset_str = time_str[:-5], time_str[-5:]
    # parse the naive date/time part
    naive_dt = datetime.strptime(naive_time_str, '%Y-%m-%dT%H:%M:%S.%f')
    # parse the utc offset
    offset = int(offset_str[-4:-2]) * 60 + int(offset_str[-2:])
    if offset_str[0] == "-":
        offset = -offset
    dt = naive_dt.replace(tzinfo=FixedOffset(offset))
    return dt


class FixedOffset(tzinfo):
    """Fixed offset in minutes: `time = utc_time + utc_offset`."""
    def __init__(self, offset):
        self.__offset = timedelta(minutes=offset)
        hours, minutes = divmod(offset, 60)
        self.__name = '<%+03d%02d>%+d' % (hours, minutes, -hours)

    def utcoffset(self, dt=None):
        return self.__offset

    def tzname(self, dt=None):
        return self.__name

    def dst(self, dt=None):
        return timedelta(0)

    def __repr__(self):
        return 'FixedOffset(%d)' % (self.utcoffset().total_seconds() / 60)


if __name__ == "__main__":

    fire.Fire({
        'create': create,
        'update': update
    })
