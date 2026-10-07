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
# The sample code; software libraries; command line tools; proofs of concept; templates; or other related technology (including any of the 
# foregoing that are provided by our personnel) is provided to you as AWS Content under the AWS Customer Agreement, or the relevant 
# written agreement between you and AWS (whichever applies). You should not use this AWS Content in your production accounts, or on 
# production or other critical data. You are responsible for testing, securing, and optimizing the AWS Content, such as sample code, as 
# appropriate for production grade use based on your specific quality control practices and standards. Deploying AWS Content may incur AWS 
# charges for creating or using AWS chargeable resources, such as running Amazon EC2 instances or using Amazon S3 storage.

# Introduction to AWS ParallelCluster
# This script is the same as the walk through in pcluster-athena++ notebook. 
# It's used to hide the following "boring" stuff - if you want to get to running jobs on ParallelCluster 
# 1. Creation of S3 bucket, VPC, SSH key, MySQL database for Slurmdbd
# 2. Creation of ParallelCluster with post_install_script


import boto3
import botocore
import json
import time
import os
import base64
import project_path # path to helper methods
from lib import workshop
from botocore.exceptions import ClientError


class PClusterHelper:
    def __init__(self, pcluster_name, config_name, post_install_script, dbd_host='localhost', federation_name=''):
        self.my_account_id = boto3.client('sts').get_caller_identity().get('Account')
        self.session = boto3.session.Session()
        self.region = self.session.region_name
        self.pcluster_name = pcluster_name
        self.rds_secret_name = 'slurm_dbd_credential'
        self.db_name = 'pclusterdb'
        self.use_existing_vpc = True
        self.config_name = config_name
        self.post_install_script = post_install_script
        self.my_bucket_name = pcluster_name.lower()+'-'+self.my_account_id
        self.dbd_host = dbd_host
        self.mungekey_secret_name = "munge_key"+'_'+federation_name
        self.federation_name = federation_name
        self.ssh_key_name = 'pcluster-athena-key'

        
    def get_slurm_dbd_rds_secret(self):
        """Retrieve the RDS secret for Slurm accounting database from Secrets Manager."""
        client = self.session.client(
            service_name='secretsmanager',
            region_name=self.region
        )

        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=self.rds_secret_name
            )
        except ClientError as e:
            raise e
        else:
            if 'SecretString' in get_secret_value_response:
                secret = get_secret_value_response['SecretString']
                return secret
            else:
                decoded_binary_secret = base64.b64decode(get_secret_value_response['SecretBinary'])
                return decoded_binary_secret

    def replace_placeholder(self, content, values):
        """Replace all placeholders in a string. values is a dict with placeholder name and value."""
        for k, v in values.items():
            content = content.replace(k, v)
        return content

    def template_to_file(self, source_file, target_file, mapping):
        """Read a template file, replace placeholders, and write to target file."""
        with open(source_file, "rt") as f:
            content = f.read()
            with open(target_file, "wt") as fo:
                fo.write(self.replace_placeholder(content, mapping))    

    def create_before(self):
        """
        Create pre-requisites for the ParallelCluster:
         1. Cluster is created in the default VPC 
         2. Create an RDS MySQL in the same VPC, with port 3306 open to the VPC/16 range
         3. An ssh key 'pcluster-athena-key' is created automatically if it doesn't exist
         4. Upload post-install script to S3
         5. Generate the ParallelCluster config from template
        """
        ec2_client = boto3.client('ec2')

        keypair_saved_path = './'+self.ssh_key_name+'.pem'

        try:
            workshop.create_keypair(self.region, self.session, self.ssh_key_name, keypair_saved_path)
        except ClientError as e:
            if e.response['Error']['Code'] == "InvalidKeyPair.Duplicate":
                print("KeyPair with the name {} already exists. Skip".format(self.ssh_key_name))

        # VPC - use existing default VPC or create a new one with 2 subnets
        if self.use_existing_vpc:
            vpc_filter = [{'Name':'isDefault', 'Values':['true']}]
            default_vpc = ec2_client.describe_vpcs(Filters=vpc_filter)
            self.vpc_id = default_vpc['Vpcs'][0]['VpcId']

            subnet_filter = [{'Name':'vpc-id', 'Values':[self.vpc_id]}]
            subnets = ec2_client.describe_subnets(Filters=subnet_filter)
            for sn in subnets['Subnets']:
                if sn['AvailabilityZone'].endswith('a'):
                    subnet_id = sn['SubnetId']
                if sn['AvailabilityZone'].endswith('b'):
                    subnet_id2 = sn['SubnetId']    
        else: 
            vpc, subnet1, subnet2 = workshop.create_and_configure_vpc()
            self.vpc_id = vpc.id
            subnet_id = subnet1.id
            subnet_id2 = subnet2.id

        # Create the project bucket
        bucket_prefix = self.pcluster_name.lower()+'-'+self.my_account_id
        self.my_bucket_name = workshop.create_bucket(self.region, self.session, bucket_prefix, False)
        print(self.my_bucket_name)

        # RDS Database (MySQL) - used with ParallelCluster for Slurm accounting
        workshop.create_simple_mysql_rds(self.region, self.session, self.db_name, [subnet_id, subnet_id2], self.rds_secret_name)

        rds_client = self.session.client('rds', self.region)
        rds_waiter = rds_client.get_waiter('db_instance_available')

        try:
            print("Waiting for RDS instance creation to complete ... ")
            rds_waiter.wait(DBInstanceIdentifier=self.db_name) 
        except botocore.exceptions.WaiterError as e:
            print(e)

        # Update the secret with the RDS hostname and get security groups
        vpc_sgs = workshop.get_sgs_and_update_secret(self.region, self.session, self.db_name, self.rds_secret_name)
        print(vpc_sgs)

        # Update the RDS security group to allow inbound traffic to port 3306
        ec2 = boto3.resource('ec2')
        vpc = ec2.Vpc(self.vpc_id)
        cidr = vpc.cidr_block
        workshop.update_security_group(vpc_sgs[0]['VpcSecurityGroupId'], cidr, 3306)

        # Prepare the ParallelCluster config from template
        rds_secret = json.loads(self.get_slurm_dbd_rds_secret())

        post_install_script_prefix = self.post_install_script
        post_install_script_location = "s3://{}/{}".format(self.my_bucket_name, post_install_script_prefix)

        # Upload post-install script to S3
        s3_client = self.session.client('s3')
        try:
            resp = s3_client.upload_file(post_install_script_prefix, self.my_bucket_name, post_install_script_prefix)
        except ClientError as e:
            print(e)

        # Replace placeholders in config template
        print("Prepare the config file")

        ph = {'${REGION}': self.region, 
              '${VPC_ID}': self.vpc_id, 
              '${SUBNET_ID}': subnet_id, 
              '${KEY_NAME}': self.ssh_key_name, 
              '${POST_INSTALL_SCRIPT_LOCATION}': post_install_script_location, 
              '${POST_INSTALL_SCRIPT_ARGS_1}': "'"+rds_secret['host']+"'",
              '${POST_INSTALL_SCRIPT_ARGS_2}': "'"+str(rds_secret['port'])+"'",
              '${POST_INSTALL_SCRIPT_ARGS_3}': "'"+rds_secret['username']+"'",
              # Pass the Secrets Manager secret NAME, not the plaintext password. The
              # post-install script fetches the password at runtime on the head node
              # (which has SecretsManagerReadWrite). This keeps the credential out of the
              # cluster config, CloudFormation parameters, cfnconfig, and CloudWatch logs.
              '${POST_INSTALL_SCRIPT_ARGS_4}': "'"+self.rds_secret_name+"'",
              '${POST_INSTALL_SCRIPT_ARGS_5}': "'"+self.pcluster_name+"'",
              '${BUCKET_NAME}': self.my_bucket_name
             }

        self.template_to_file("config/"+self.config_name+".ini", "build/"+self.config_name, ph)

            
    def cleanup_after(self, KeepRDS=True, KeepSSHKey=True):
        """Clean up resources created for the cluster."""
        if not KeepRDS:
            workshop.detele_rds_instance(self.region, self.session, self.db_name)
            workshop.delete_secrets_with_force(self.region, self.session, [self.rds_secret_name])

        print(f"Deleting secret {self.mungekey_secret_name}")
        workshop.delete_secrets_with_force(self.region, self.session, [self.mungekey_secret_name])
        print(f"Deleting bucket {self.my_bucket_name}")
        workshop.delete_bucket_with_version(self.my_bucket_name)

        if not KeepSSHKey:
            print(f"Deleting ssh_key {self.ssh_key_name}")        
            workshop.delete_keypair(self.region, self.session, self.ssh_key_name)


    def upload_athena_files(self, input_file, batch_file, my_prefix):
        """Upload Athena++ input and batch files to S3."""
        session = boto3.Session()
        s3_client = session.client('s3')

        try:
            resp = s3_client.upload_file('build/'+input_file, self.my_bucket_name, my_prefix+'/'+input_file)
            resp = s3_client.upload_file('build/'+batch_file, self.my_bucket_name, my_prefix+'/'+batch_file)
        except ClientError as e:
            print(e)
