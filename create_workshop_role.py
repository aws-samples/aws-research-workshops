import boto3
import botocore.session
import botocore.exceptions
import json
import logging
import os
import time

iam = boto3.client('iam')

def create_service_role(iam, policy_name, assume_role_policy_document, inline_policy_name=None, policy_str=None):
    """Creates a new role if there is not already a role by that name"""
    if role_exists(iam, policy_name):
        logging.info('Role "%s" already exists. Assuming correct values.', policy_name)
        return get_role_arn(iam, policy_name)
    else:
        response = iam.create_role(RoleName=policy_name, Path="/service-role/", AssumeRolePolicyDocument=assume_role_policy_document)
        
        if policy_str is not None:
            iam.put_role_policy(RoleName=policy_name, PolicyName=inline_policy_name, PolicyDocument=policy_str)
        logging.info('response for creating role = "%s"', response)
        return response['Role']['Arn']

def role_exists(iam, role_name):
    """Checks if the role exists already"""
    try:
        iam.get_role(RoleName=role_name)
    except botocore.exceptions.ClientError:
        return False
    return True

def get_role_arn(iam, role_name):
    """Gets the ARN of role"""
    response = iam.get_role(RoleName=role_name)
    return response['Role']['Arn']

role_doc = {
        "Version": "2012-10-17", 
        "Statement": [
            {"Sid": "", 
             "Effect": "Allow", 
             "Principal": {
                 "Service": [
                     "sagemaker.amazonaws.com",
                     "robomaker.amazonaws.com",
                     "glue.amazonaws.com"
                 ]
             }, 
             "Action": "sts:AssumeRole"
        }]
    }

inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": [
                    "*",
                    "*"
                ],
                "Resource": [
                    "*"
                ],
                "Effect": "Allow"
            }
        ]
    }

role_arn = create_service_role(iam, 'ResearchWorkshops-AmazonSageMaker-ExecutionRole', json.dumps(role_doc), 'Inline-Research-Workshops-Policy', json.dumps(inline_policy))
print(role_arn)
