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

# Developers' notes: 
# All the functions in this file are written for this specific workshop only. You can use them as references,
# but please don't use them for production. There are lots of "convention over configuration" short-cuts to simplify the 
# workshop.


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
        print("Successfully created DB instance %s" % DB_NAME)
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
            
def create_simple_compute_environment(proj_name): 
    computeEnvironmentName = f"CE-{proj_name}"
    
    iam_client = boto3.client('iam')
    ec2_client = boto3.client('ec2')
    batch_client = boto3.client('batch')
    
    # use the default VPC for simplicity
    vpc_filter = [{'Name':'isDefault', 'Values':['true']}]
    default_vpc = ec2_client.describe_vpcs(Filters=vpc_filter)
    vpc_id = default_vpc['Vpcs'][0]['VpcId']

    subnet_filter = [{'Name':'vpc-id', 'Values':[vpc_id]}]
    subnets = ec2_client.describe_subnets(Filters=subnet_filter)
    subnet1_id = subnets['Subnets'][0]['SubnetId']
    subnet2_id = subnets['Subnets'][1]['SubnetId']


    batch_instance_role_name = f"batch_instance_role_{proj_name}"
    batch_instance_policies = ["arn:aws:iam::aws:policy/CloudWatchFullAccess", "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role","arn:aws:iam::aws:policy/AmazonS3FullAccess"]
    create_service_role_with_policies(batch_instance_role_name, "ec2.amazonaws.com", batch_instance_policies)
    instance_profile_name =f"instance_profile_{proj_name}"
    try:
        iam_client.create_instance_profile(InstanceProfileName=instance_profile_name)
    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            print("Instance profile already attached, ignore")
        else:
            raise e
    try:
        iam_client.add_role_to_instance_profile(InstanceProfileName=instance_profile_name, RoleName=batch_instance_role_name)
    except ClientError as e:
        print(e)
        
    instanceRole = iam_client.get_instance_profile(InstanceProfileName=f"instance_profile_{proj_name}")['InstanceProfile']['Arn']
    
    batch_service_role_name = f"batch_service_role_{proj_name}"
    batch_service_policies = ["arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole", "arn:aws:iam::aws:policy/CloudWatchFullAccess"]
    serviceRole = create_service_role_with_policies(batch_service_role_name, "batch.amazonaws.com", batch_service_policies)

    batch_sg_name = f"batch_sg_{proj_name}"
    try:
        sg = ec2_client.create_security_group(
            Description='security group for Compute Environment',
            GroupName=batch_sg_name,
            VpcId=vpc_id
        )
        batch_sec_group_id=sg["GroupId"]
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
            print("SG already exists, ")
            resp = ec2_client.describe_security_groups(Filters=[dict(Name='group-name', Values=[batch_sg_name])])
            batch_sec_group_id = resp['SecurityGroups'][0]['GroupId']

    print('Batch security group id - ' + batch_sg_name)
    print(batch_sec_group_id)

    security_groups = [batch_sec_group_id]
    
    compute_resources = {
        'type': 'EC2',
        'allocationStrategy': 'BEST_FIT_PROGRESSIVE',
        'minvCpus': 4,
        'maxvCpus': 64,
        'desiredvCpus': 4,
        'instanceTypes': ['optimal'],
        'subnets': [subnet1_id,  subnet2_id],
        'securityGroupIds': security_groups,
        'instanceRole': instanceRole
    }
        
    response = batch_client.create_compute_environment(
        computeEnvironmentName=computeEnvironmentName,
        type='MANAGED',
        serviceRole=serviceRole,
        computeResources=compute_resources
    )

    while True:
        describe = batch_client.describe_compute_environments(computeEnvironments=[computeEnvironmentName])
        computeEnvironment = describe['computeEnvironments'][0]
        status = computeEnvironment['status']
        if status == 'VALID':
            print('\rSuccessfully created compute environment {}'.format(computeEnvironmentName))
            break
        elif status == 'INVALID':
            reason = computeEnvironment['statusReason']
            raise Exception('Failed to create compute environment: {}'.format(reason))
        print('\rCreating compute environment...')
        time.sleep(10)
            
    return response            
            
def delete_simple_compute_environment(proj_name):
    computeEnvironment = f"CE-{proj_name}"
    iam_client = boto3.client('iam')
    batch_client = boto3.client('batch')
    ec2_client = boto3.client('ec2')
        
    try:
        response = batch_client.update_compute_environment(
            computeEnvironment=computeEnvironment,
            state='DISABLED',
        )
    
        while True:
            response = batch_client.describe_compute_environments(
                computeEnvironments=[computeEnvironment])
            assert len(response['computeEnvironments']) == 1
            env = response['computeEnvironments'][0]
            state = env['state']
            status = env['status']
            if status == 'UPDATING':
                print("Environment %r is updating, waiting..." % (computeEnvironment,))

            elif state == 'DISABLED':
                break

            else:
                raise RuntimeError('Expected status=UPDATING or state=DISABLED, '
                                   'but status=%r and state=%r' % (status, state))

            # wait a little bit before checking again.
            time.sleep(15)

        ce_response = batch_client.delete_compute_environment(
            computeEnvironment=computeEnvironment
        )

        time.sleep(5)
        response = describe_compute_environments([computeEnvironment])

        while response['computeEnvironments'][0]['status'] == 'DELETING':
            time.sleep(5)
            response = describe_compute_environments([computeEnvironment])
            if len(response['computeEnvironments']) != 1:
                break
    except:
        print("CE may not exist, ignore")
        
    # only delete those if the CE is deleted
    response = describe_compute_environments([computeEnvironment])
    if len(response['computeEnvironments']) != 1:
        # clean up the other resouces created 
        batch_instance_role_name = f"batch_instance_role_{proj_name}"
        instance_profile_name =f"instance_profile_{proj_name}"
        batch_instance_policies = ["arn:aws:iam::aws:policy/CloudWatchFullAccess", "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role","arn:aws:iam::aws:policy/AmazonS3FullAccess"]

        try:
            iam_client.remove_role_from_instance_profile(InstanceProfileName=instance_profile_name, RoleName=batch_instance_role_name)
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                print("Ignore profile removal")

        delete_service_role_with_policies(batch_instance_role_name,  batch_instance_policies)
        iam_client.delete_instance_profile(InstanceProfileName=instance_profile_name)



        batch_service_role_name = f"batch_service_role_{proj_name}"
        batch_service_policies = ["arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole", "arn:aws:iam::aws:policy/CloudWatchFullAccess"]
        delete_service_role_with_policies(batch_service_role_name, batch_service_policies)

        batch_sg_name = f"batch_sg_{proj_name}"

        try: 
            ec2_client.delete_security_group(GroupName=batch_sg_name)
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroup.NotFound':
                print("SG doesn't exist, ignore")

            
    print("CE delete completed")


def describe_compute_environments(compute_envs):
    batch = boto3.client('batch')

    try:
        response = batch.describe_compute_environments(
            computeEnvironments=compute_envs,
        )
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise

    return response

def create_job_queue(computeEnvironmentName, priority):
    batch = boto3.client('batch')
    jobQueueName = computeEnvironmentName + '_queue'
    try:
        response = batch.create_job_queue(jobQueueName=jobQueueName,
                                      priority=priority,
                                      computeEnvironmentOrder=[
                                          {
                                              'order': priority,
                                              'computeEnvironment': computeEnvironmentName
                                          }
                                      ])
    except ClientError as e:
        if e.response['Error']['Message'] =='Object already exists':
            print("Job queue already exists, ignore")

    while True:
        describe = batch.describe_job_queues(jobQueues=[jobQueueName])
        jobQueue = describe['jobQueues'][0]
        status = jobQueue['status']
        if status == 'VALID':
            print('\rSuccessfully created job queue {}'.format(jobQueueName))
            return jobQueue['jobQueueName'], jobQueue['jobQueueArn']
        elif status == 'INVALID':
            reason = jobQueue['statusReason']
            raise Exception('Failed to create job queue: {}'.format(reason))
        print('\rCreating job queue... ')
        time.sleep(5)


def delete_job_queue(job_queue):
    batch = boto3.client('batch')
    job_queues = [job_queue]
    response = describe_job_queues(job_queues)
    
    try:        
        if response['jobQueues'][0]['state'] != 'DISABLED':
            try:
                batch.update_job_queue(
                    jobQueue=job_queue,
                    state='DISABLED'
                )
            except ClientError as e:
                print(e.response['Error']['Message'])
                raise

        terminate_jobs(job_queue)

        # Wait until job queue is DISABLED
        response = describe_job_queues(job_queues)
        while response['jobQueues'][0]['state'] != 'DISABLED':
            time.sleep(5)
            response = describe_job_queues(job_queues)

        time.sleep(10)
        if response['jobQueues'][0]['status'] != 'DELETING':
            try:
                batch.delete_job_queue(
                    jobQueue=job_queue,
                )
            except ClientError as e:
                print(e.response['Error']['Message'])
                raise

        response = describe_job_queues(job_queues)

        while response['jobQueues'][0]['status'] == 'DELETING':
            time.sleep(5)
            response = describe_job_queues(job_queues)

            if len(response['jobQueues']) != 1:
                break
    except:
        print("Job queue doesn't exist, skip")

def describe_job_queues(job_queues):
    batch = boto3.client('batch')
    try:
        response = batch.describe_job_queues(
            jobQueues=job_queues
        )
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise

    return response


def delete_job_definition(job_def):
    batch = boto3.client('batch')
    try:
        response = batch.deregister_job_definition(
            jobDefinition=job_def
        )
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise

    return response


def terminate_jobs(job_queue):
    batch = boto3.client('batch')
    response = list_jobs(job_queue)
    for job in response['jobSummaryList']:
        batch.terminate_job(
            jobId =job['jobId'],
            reason='Removing Batch Environment'
        )
    while response.get('nextToken', None) is not None:
        response = list_jobs(job_queue, response['nextToken'])
        for job in response['jobSummaryList']:
            batch.terminate_job(
                jobId =job['jobId'],
                reason='Removing Batch Environment'
            )


def list_jobs(job_queue, next_token=""):
    batch = boto3.client('batch')
    try:
        if next_token:
            response = batch.list_jobs(
                jobQueue=job_queue,
                nextToken=next_token
            )
        else:
            response = batch.list_jobs(
                jobQueue=job_queue,
            )
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise

    return response

def create_service_role_with_policies(role_name, service_name, policy_arns):
    iam_client = boto3.client('iam')
    try:
        resp = iam_client.create_role(RoleName=role_name,
                                 AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Sid":"","Effect":"Allow","Principal":{"Service": "' + service_name+'"},"Action":"sts:AssumeRole"}]}')
        for policy in policy_arns:
            resp = iam_client.attach_role_policy(PolicyArn=policy,RoleName=role_name)
    except ClientError  as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            print(f"{role_name} already exists, ignore")
        else: 
            raise  e
    
    resp = iam_client.get_role(RoleName=role_name)
    return resp['Role']['Arn']

def delete_service_role_with_policies(role_name, policy_arns):
    iam_client = boto3.client('iam')
    try:
        for policy in policy_arns:
            try: 
                resp = iam_client.detach_role_policy(PolicyArn=policy,RoleName=role_name)
            except ClientError as ee:
               if ee.response['Error']['Code'] == 'NoSuchEntity':
                   print("Policy not attached, ignore")
                    
        resp = iam_client.delete_role(RoleName=role_name)
        print(f"deleted service role {role_name}")
    except ClientError  as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            print(f"{role_name} already deleted, ignore")
        else: 
            raise  e

def create_job_definition(proj_name, image_uri, batch_task_role_arn):
    batch_client = boto3.client('batch')
    job_def_name = f"JD-{proj_name}"
    
    job_def = batch_client.register_job_definition(
        jobDefinitionName=job_def_name,
        type='container',
        containerProperties={
            'image': image_uri,
            'vcpus': 2,
            'memory': 1024,
            'jobRoleArn': batch_task_role_arn,
            'logConfiguration': {
                'logDriver': 'awslogs'                
            }
        }
        
    )

    return job_def

def delete_codecommit_repo(proj_name):
    codecommit_client = boto3.client('codecommit')
    try:
        resp = codecommit_client.delete_repository(repositoryName=proj_name)
        print(f"Deleted codecommit repo {proj_name}")
    except ClientError as e:
        print(e)
                                                   
def delete_ecr_repo(proj_name):
    ecr = boto3.client('ecr')
    try:
        resp = ecr.delete_repository(repositoryName=proj_name, force=True)
        print(f"Deleted ecr repo {proj_name}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'RepositoryNotFoundException': 
            print("ECR repo doesn't exist, skip")
                                                   
 
# use None for parent_commit_id if new
def commit_files(proj_name, branch_name, put_files, parent_commit_id):
    codecommit_client = boto3.client('codecommit')
    if parent_commit_id:
        resp = codecommit_client.create_commit(repositoryName=proj_name, branchName=branch_name, 
                                               parentCommitId=parent_commit_id,
                                               putFiles=put_files)
    else:
        resp = codecommit_client.create_commit(repositoryName=proj_name, branchName=branch_name, 
                                               putFiles=put_files)
        
    print("Finished commit")
    
                                                   