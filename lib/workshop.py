#!/usr/bin/python

#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

import logging
import os
import time
import boto3
import argparse
import botocore.session
import botocore.exceptions
import uuid
import sys
import tarfile
import json
from botocore.exceptions import ClientError
from six.moves import urllib

from dateutil import parser


def create_and_configure_vpc(tag='research-workshop'): 
    """Create VPC"""
    ec2 = boto3.resource('ec2')
    ec2_client = boto3.client('ec2')
    session = boto3.session.Session()
    region = session.region_name
    vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
    vpc.modify_attribute(EnableDnsSupport={'Value':True})
    vpc.modify_attribute(EnableDnsHostnames={'Value':True})
    tag = vpc.create_tags(
    Tags=[
        {
            'Key': 'Name',
            'Value': tag
        },
    ])

    subnet = vpc.create_subnet(CidrBlock='10.0.0.0/24', AvailabilityZone=region + 'a')
    subnet.meta.client.modify_subnet_attribute(
        SubnetId=subnet.id, 
        MapPublicIpOnLaunch={"Value": True}
    )

    subnet2 = vpc.create_subnet(CidrBlock='10.0.1.0/24', AvailabilityZone=region + 'b')
    subnet2.meta.client.modify_subnet_attribute(SubnetId=subnet2.id, MapPublicIpOnLaunch={"Value": True})

    igw = ec2.create_internet_gateway()
    igw.attach_to_vpc(VpcId=vpc.id)

    public_route_table = list(vpc.route_tables.all())[0]
    # add a default route, for Public Subnet, pointing to Internet Gateway 
    ec2_client.create_route(RouteTableId=public_route_table.id,DestinationCidrBlock='0.0.0.0/0',GatewayId=igw.id)
    public_route_table.associate_with_subnet(SubnetId=subnet.id)
    public_route_table.associate_with_subnet(SubnetId=subnet2.id)

    return vpc, subnet, subnet2

def vpc_cleanup(vpcid):
    """Cleanup VPC"""
    print('Removing VPC ({}) from AWS'.format(vpcid))
    ec2 = boto3.resource('ec2')
    ec2_client = ec2.meta.client
    vpc = ec2.Vpc(vpcid)

  # detach default dhcp_options if associated with the vpc
    dhcp_options_default = ec2.DhcpOptions('default')
    if dhcp_options_default:
        dhcp_options_default.associate_with_vpc(
            VpcId=vpc.id
        )
    # detach and delete all gateways associated with the vpc
    for gw in vpc.internet_gateways.all():
        vpc.detach_internet_gateway(InternetGatewayId=gw.id)
        gw.delete()
    # delete all route table associations
    for rt in vpc.route_tables.all():
        if not rt.associations:
            rt.delete()
        else:
            for rta in rt.associations:
                if not rta.main:
                    rta.delete()
    # delete any instances
    for subnet in vpc.subnets.all():
        for instance in subnet.instances.all():
            instance.terminate()
    # delete our endpoints
    for ep in ec2_client.describe_vpc_endpoints(
            Filters=[{
                'Name': 'vpc-id',
                'Values': [vpcid]
            }])['VpcEndpoints']:
        ec2_client.delete_vpc_endpoints(VpcEndpointIds=[ep['VpcEndpointId']])
    # delete our security groups
    for sg in vpc.security_groups.all():
        if sg.group_name != 'default':
            sg.delete()
    # delete any vpc peering connections
    for vpcpeer in ec2_client.describe_vpc_peering_connections(
            Filters=[{
                'Name': 'requester-vpc-info.vpc-id',
                'Values': [vpcid]
            }])['VpcPeeringConnections']:
        ec2.VpcPeeringConnection(vpcpeer['VpcPeeringConnectionId']).delete()
    # delete non-default network acls
    for netacl in vpc.network_acls.all():
        if not netacl.is_default:
            netacl.delete()
    # delete network interfaces
    for subnet in vpc.subnets.all():
        for interface in subnet.network_interfaces.all():
            interface.delete()
        subnet.delete()
    # finally, delete the vpc
    ec2_client.delete_vpc(VpcId=vpcid)
    
def get_latest_amazon_linux():
    """Search EC2 Images for Amazon Linux"""
    ec2_client = boto3.client('ec2')
    
    filters = [ {
        'Name': 'name',
        'Values': ['amzn-ami-hvm-*']
    },{
        'Name': 'description',
        'Values': ['Amazon Linux AMI*']
    },{
        'Name': 'architecture',
        'Values': ['x86_64']
    },{
        'Name': 'owner-alias',
        'Values': ['amazon']
    },{
        'Name': 'owner-id',
        'Values': ['137112412989']
    },{
        'Name': 'state',
        'Values': ['available']
    },{
        'Name': 'root-device-type',
        'Values': ['ebs']
    },{
        'Name': 'virtualization-type',
        'Values': ['hvm']
    },{
        'Name': 'hypervisor',
        'Values': ['xen']
    },{
        'Name': 'image-type',
        'Values': ['machine']
    } ]
 
    response = ec2_client.describe_images(Owners=['amazon'], Filters=filters)
    source_image = newest_image(response['Images'])
    return source_image['ImageId']    
    
def newest_image(list_of_images):
    """Get Newest Amazon Linux Image from list"""
    latest = None

    for image in list_of_images:
        if not latest:
            latest = image
            continue

        if parser.parse(image['CreationDate']) > parser.parse(latest['CreationDate']):
            latest = image

    return latest

def create_role(iam, policy_name, assume_role_policy_document, inline_policy_name=None, policy_str=None, managed_policy=None):
    """Creates a new role if there is not already a role by that name"""
    if role_exists(iam, policy_name):
        logging.info('Role "%s" already exists. Assuming correct values.', policy_name)
        return get_role_arn(iam, policy_name)
    else:
        response = iam.create_role(RoleName=policy_name,
                                   AssumeRolePolicyDocument=assume_role_policy_document)
        
        if policy_str is not None:
            iam.put_role_policy(RoleName=policy_name,
                            PolicyName=inline_policy_name, PolicyDocument=policy_str)
        
        if managed_policy is not None:
            iam.attach_role_policy(RoleName=policy_name, PolicyArn=managed_policy)
            
        logging.info('response for creating role = "%s"', response)
        return response['Role']['Arn']

def role_exists(iam, role_name):
    """Checks if the role exists already"""
    try:
        iam.get_role(RoleName=role_name)
    except ClientError:
        return False
    return True

def get_role_arn(iam, role_name):
    """Gets the ARN of role"""
    response = iam.get_role(RoleName=role_name)
    return response['Role']['Arn']

def create_bucket_name(bucket_prefix):
    # The generated bucket name must be between 3 and 63 chars long
    return ''.join([bucket_prefix, str(uuid.uuid4())])

def create_bucket(region, session, bucket_prefix, with_uuid=True):
    if with_uuid:
        bucket = create_bucket_name(bucket_prefix)
    else:
        bucket = bucket_prefix

    if region != 'us-east-1':
        session.resource('s3').create_bucket(Bucket=bucket, CreateBucketConfiguration={'LocationConstraint': region})
    else:
        session.resource('s3').create_bucket(Bucket=bucket)
    return bucket

def delete_bucket_completely(bucket_name):
    """Remove all objects from S3 bucket and delete"""
    client = boto3.client('s3')

    try:
        response = client.list_objects_v2(
            Bucket=bucket_name,
        )
    except ClientError as e:
        if e.response['Error']['Code'] == "NoSuchBucket":
            print("Bucket has already been deleted")
            return
    except: 
        raise 

    while response['KeyCount'] > 0:
        print('Deleting %d objects from bucket %s' % (len(response['Contents']),bucket_name))
        response = client.delete_objects(
            Bucket=bucket_name,
            Delete={
                'Objects':[{'Key':obj['Key']} for obj in response['Contents']]
            }
        )
        response = client.list_objects_v2(
            Bucket=bucket_name,
        )

    print('Now deleting bucket %s' % bucket_name)
    response = client.delete_bucket(
        Bucket=bucket_name
    )
    
def delete_bucket_with_version(bucket_name):
    bucket = boto3.resource('s3').Bucket(bucket_name)
    try: 
        for version in bucket.object_versions.all():
            version.delete() 

        bucket.delete()
    except ClientError as e:
        if e.response['Error']['Code'] == "NoSuchBucket":
            print("Bucket has already been deleted")
    except:
        raise
    
def create_db(glue_client, account_id, database_name, description):
    """Create the specified Glue database if it does not exist"""
    try:
        glue_client.get_database(
            CatalogId=account_id,
            Name=database_name
        )
    except glue_client.exceptions.EntityNotFoundException:
        print("Creating database: %s" % database_name)
        glue_client.create_database(
            CatalogId=account_id,
            DatabaseInput={
                'Name': database_name,
                'Description': description
            }
        )

def create_keypair(region, session, key_name, save_path):
    new_keypair = session.resource('ec2').create_key_pair(KeyName=key_name)
    with open(save_path, 'w') as file:
        file.write(new_keypair.key_material)
    
    print(new_keypair.key_fingerprint)
    
def create_simple_mysql_rds(region, session, db_name, subnet_ids, rds_secret_name):
    ENGINE_NAME = 'mysql'
    DB_INSTANCE_TYPE = 'db.m5.large'
    DB_NAME = db_name
    DB_USER_NAME = 'db_user'
    DB_USER_PASSWORD = 'db_pass_'+str(uuid.uuid4())[0:10]
    SUBNET_GROUP_NAME =  db_name + '-subnetgroup'
    rds_client = boto3.client('rds')
    ec2_client = boto3.client('ec2')
    
    # create a subnet group first
    try:
        subnet_group_response = rds_client.create_db_subnet_group(DBSubnetGroupName=SUBNET_GROUP_NAME, 
                                                              DBSubnetGroupDescription='subnet group for the simple rds', 
                                                              SubnetIds=subnet_ids)
    except ClientError as e:  
        if e.response['Error']['Code'] == "DBSubnetGroupAlreadyExists":
            print("SubnetGroup Already exist, ignore")
        else:
            print(e)
    
    try: 
        create_db_instance_response = rds_client.create_db_instance(DBInstanceIdentifier=DB_NAME,
                                                                DBInstanceClass=DB_INSTANCE_TYPE,
                                                                DBName=DB_NAME,
                                                                Engine=ENGINE_NAME,
                                                                AllocatedStorage=10,
                                                                MasterUsername=DB_USER_NAME,
                                                                MasterUserPassword=DB_USER_PASSWORD,
                                                                DBSubnetGroupName=SUBNET_GROUP_NAME,
                                                                PubliclyAccessible=True)
    except ClientError as e:
        if e.response['Error']['Code'] == "DBInstanceAlreadyExists":
            print("DB instance with the same id already exists. You can either use the same instance or pick a new id")
    except: 
        print("Failed to create the db")
        raise        
    else:
        print("Successfully create DB instance %s" % DB_NAME)
        create_rds_secret(region, session, rds_secret_name, DB_NAME,'', '3306', DB_USER_NAME, DB_USER_PASSWORD)
        
def create_rds_secret(region, session, secret_name, rds_id, host, port, username, password): 
    sm_client = session.client('secretsmanager', region_name=region)
    data = {"username": username, "password": password, "engine": 'mysql', "host": host, "port": port, 'dbInstanceIdentifier': rds_id}
    try:
        sm_client.create_secret(Name=secret_name, SecretString=json.dumps(data))
    except ClientError as e:
        if e.response['Error']['Code'] == "ResourceExistsException":
            print("secret exists, update instead")
            sm_client.update_secret(SecretId=secret_name, SecretString=json.dumps(data))
    except:
        raise

def update_rds_secret_with_hostname(region, session, secret_name, hostname):
    sm_client = session.client('secretsmanager', region_name=region)
    try:
        data = sm_client.get_secret_value(SecretId=secret_name)
        secret = json.loads(data['SecretString'])
        secret['host'] = hostname
        sm_client.update_secret(SecretId=secret_name, SecretString=json.dumps(secret))
    except:
        raise
    
def get_sgs_and_update_secret(region, session, rds_id, rds_secret_name):
    rds_client = session.client('rds', region)
    try:
        resp = rds_client.describe_db_instances(DBInstanceIdentifier=rds_id)
        hostname=resp['DBInstances'][0]['Endpoint']['Address']
        update_rds_secret_with_hostname(region,session, rds_secret_name, hostname)
        return resp['DBInstances'][0]['VpcSecurityGroups'] 
    except:
        raise

def update_security_group(sg_id, cidr, port):
    ec2 = boto3.resource('ec2')
    sg = ec2.SecurityGroup(sg_id)
    
    ip_permissions = list()
    p = {}
    rgs = {}
    p['IpProtocol'] = 'tcp'
    p['IpRanges'] = list ()
    rgs['CidrIp'] = cidr
    rgs['Description'] = 'Auto added by notebook'
    p['IpRanges'].append(rgs)
    p['Ipv6Ranges'] = list()
    p['PrefixListIds'] = list()
    p['ToPort'] = port
    p['FromPort'] = port
    p['UserIdGroupPairs'] = list()
    ip_permissions.append(p)

    try: 
        resp = sg.authorize_ingress(IpPermissions = ip_permissions)
        print(resp)
    except ClientError as e:
        if e.response['Error']['Code'] == "InvalidPermission.Duplicate":
            print("Ingress rule already exists, ignore")
        else:
            print("Something else faile ... ")
            print(e)
            
def detele_rds_instance(region, session, rds_id):
    rds_client = session.client('rds', region)
    try:
        resp = rds_client.delete_db_instance(DBInstanceIdentifier=rds_id, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
        print(resp)
    except ClientError as e:
        if e.response['Error']['Code'] == "InvalidDBInstanceState":
            print("DB might have been deleted already")
    except:
        raise

def delete_secrets_with_force(region, session, secret_names): 
    sm_client = session.client('secretsmanager', region)
    for s in secret_names:
        try:
            resp = sm_client.delete_secret(SecretId=s, ForceDeleteWithoutRecovery=True)
        except:
            raise
            