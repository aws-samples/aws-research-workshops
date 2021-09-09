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
# It's used to hide the followign the "boring" stuff - if you want to get to running jobs on ParallalCluster 
# 1. Creation of S3 bucket, VPC, SSH key, MySQL database for Slurmdbd
# 2. Creation of ParallelCluster with post_install_script
# 
# ## Create a cluster
# If you have not installed aws-parallelcluster commandline tool, uncomment the next line of code and executed it. You only need to do it once. 
# 
# As an alternative, you can create a IAM user that has the policies mentioned above, and add the aws_access_key_id and aws_secret_access_key in the [aws] section of the following config file.  


import boto3
import botocore
import json
import time
import os
import subprocess
import base64
import docker
import pandas as pd
import importlib
import project_path # path to helper methods
from lib import workshop
from botocore.exceptions import ClientError
import requests
from IPython.display import HTML, display


class PClusterHelper:
    def __init__(self, pcluster_name, config_name, post_install_script, dbd_host='localhost', federation_name=''):
        self.my_account_id = boto3.client('sts').get_caller_identity().get('Account')
        self.session = boto3.session.Session()
        self.region = self.session.region_name
        self.pcluster_name = pcluster_name
        self.rds_secret_name = 'slurm_dbd_credential'
        self.db_name = 'pclusterdb'
        self.slurm_secret_name = "slurm_token_{}".format(pcluster_name)
        self.use_existing_vpc = True
        self.config_name = config_name
        self.post_install_script = post_install_script
        self.my_bucket_name = pcluster_name.lower()+'-'+self.my_account_id
        self.dbd_host = dbd_host
        self.mungekey_secret_name="munge_key"+'_'+federation_name
        self.federation_name=federation_name
        self.ssh_key_name='pcluster-athena-key'

        
    ### assuem you have created a database secret in SecretManager with the name "slurm_dbd_credential"
    def get_slurm_dbd_rds_secret(self):

        # Create a Secrets Manager client
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
            # Decrypts secret using the associated KMS CMK.
            # Depending on whether the secret is a string or binary, one of these fields will be populated.
            if 'SecretString' in get_secret_value_response:
                secret = get_secret_value_response['SecretString']
                return secret
            else:
                decoded_binary_secret = base64.b64decode(get_secret_value_response['SecretBinary'])
                return decoded_binary_secret
    ###
    # helper function to replace all place_holders in a string
    # content is a string, values is a dict with placeholder name and value as attributes
    ###
    def replace_placeholder(self, content, values):
        for k,v in values.items():
            content=content.replace(k, v)
        return content

    def template_to_file(self, source_file, target_file, mapping):
        with open(source_file, "rt") as f:
            content = f.read()
            with open(target_file, "wt") as fo:
                fo.write(self.replace_placeholder(content, mapping))    

    ###
    # Create a ParallelCluster with the following defaults:
    #  1. cluster is created in the default VPC 
    #  2. create an RDS MySQL in the same VPC, with port 3306 open to the VPC/16 range
    #  3. an ssh key 'pcluster-athena-key' is created automatically if it doesn't exist already and the key will be saved in the current folder. NOTE: please download that key to your #     local machine immediately and delete the copy in the notebook folder. 
    #  4. 
    ### 
    def create_before(self):
        ec2_client = boto3.client('ec2')
        # the slurm REST token is generated from the headnode and stored in Secrets Manager. This token is used in makeing REST API calls to the Slurm REST endpoint running on the headnode 

        # specify the following names

        # ssh key for access the pcluster. this key is not needed  in this excercise, but useful if you need to ssh into the headnode of the pcluster

        keypair_saved_path = './'+self.ssh_key_name+'.pem'


        # we will not need to use the ssh_key in this excercise. However, you can only download the key once during creation. we will save it in case
        try:
            workshop.create_keypair(self.region, self.session, self.ssh_key_name, keypair_saved_path)
        except ClientError as e:
            if e.response['Error']['Code'] == "InvalidKeyPair.Duplicate":
                print("KeyPair with the name {} alread exists. Skip".format(self.ssh_key_name))


        # ## VPC
        # 
        # You can use the existing default VPC or create a new VPC with 2 subnets. 
        # 
        # We will only be using one of the subnets for the ParallelCluster, but both are used for the RDS database. 

        if self.use_existing_vpc:
            vpc_filter = [{'Name':'isDefault', 'Values':['true']}]
            default_vpc = ec2_client.describe_vpcs(Filters=vpc_filter)
            self.vpc_id = default_vpc['Vpcs'][0]['VpcId']

            subnet_filter = [{'Name':'vpc-id', 'Values':[self.vpc_id]}]
            subnets = ec2_client.describe_subnets(Filters=subnet_filter)
            # only pick 1a, 1b az - others might have issue with resources
            for sn in subnets['Subnets']:
                if '-1a' in sn['AvailabilityZone'] :
                    subnet_id = sn['SubnetId']
                if '-1b' in sn['AvailabilityZone'] :
                    subnet_id2 = sn['SubnetId']    
        else: 
            vpc, subnet1, subnet2 = workshop.create_and_configure_vpc()
            self.vpc_id = vpc.id
            subnet_id = subnet1.id
            subnet_id2 = subnet2.id


        # Create the project bucket. 
        # we will use this bucket for the scripts, input and output files 


        bucket_prefix = self.pcluster_name.lower()+'-'+self.my_account_id

        # use the bucket prefix as name, don't use uuid suffix
        self.my_bucket_name = workshop.create_bucket(self.region, self.session, bucket_prefix, False)
        print(self.my_bucket_name)


        # ## RDS Database (MySQL) - used with ParallelCluster for accounting
        # 
        # We will create a simple MySQL RDS database instance to use as a data store for Slurmdbd for accounting. The username and password are stored as a secret in the Secrets Manager. 
        # The secret is later used to configure Slurmdbd. 
        # 
        # The RDS instance will be created asynchronuously. While the secret is created immediated, the hostname will be available only after the creation is completed. We will have to update the hostname in the secreat afterwards. 
        # 
        # We will update the security group to allow traffic to port 3306 from the cluster in the same vpc
        # 

        # create a simple mysql rds instance , the username and password will be stored in secrets maanger as a secret
        workshop.create_simple_mysql_rds(self.region, self.session, self.db_name, [subnet_id,subnet_id2] ,self.rds_secret_name)


        rds_client = self.session.client('rds', self.region)
        rds_waiter = rds_client.get_waiter('db_instance_available')

        try:
            print("Waiting for RDS instance creation to complete ... ")
            rds_waiter.wait(DBInstanceIdentifier=self.db_name) 
        except botocore.exceptions.WaiterError as e:
            print(e)

        #since the rds creation is asynch, need to wait till the creation is done to get the hostname, then update the secret with the hostname
        vpc_sgs = workshop.get_sgs_and_update_secret(self.region, self.session, self.db_name, self.rds_secret_name)
        print(vpc_sgs)

        # Step 3. get the vpc local CIDR range 
        ec2 = boto3.resource('ec2')
        vpc = ec2.Vpc(self.vpc_id)
        cidr = vpc.cidr_block

        # update the RDS security group to allow inbound traffic to port 3306
        workshop.update_security_group(vpc_sgs[0]['VpcSecurityGroupId'], cidr, 3306)

        print(os.popen("pcluster version").read())

        # ### ParallelCluster config file
        # Start with the the configuration template file 
        # 

        # #### Setup parameters for PCluster
        # 
        # We will be using a relational database on AWS (RDS) for Slurm accounting (slurmdbd). Please refer to this blog for how to set it up https://aws.amazon.com/blogs/compute/enabling-job-accounting-for-hpc-with-aws-parallelcluster-and-amazon-rds/
        # 
        # Once you set up the MySQL RDS, create a secret in SecretManager with the type "Credentials for RDS", so we don't need to expose the database username/password in plain text in this notebook. 



        # the response is a json {"username": "xxxx", "password": "xxxx", "engine": "mysql", "host": "xxxx", "port": "xxxx", "dbInstanceIdentifier", "xxxx"}
        rds_secret = json.loads(self.get_slurm_dbd_rds_secret())

        post_install_script_prefix = self.post_install_script
        post_install_script_location = "s3://{}/{}".format(self.my_bucket_name, post_install_script_prefix)
        post_install_script_args = "'" + rds_secret['host']+' '+str(rds_secret['port']) +' ' + rds_secret['username'] + ' ' + rds_secret['password'] + ' ' + self.pcluster_name  + ' ' + self.region  + ' ' + self.dbd_host + ' ' + self.federation_name + "'"


        # ### Post installation script
        # This script is used to recompile and configure slurm with slurmrestd. We also added the automation of compiling Athena++ in the script. 
        # 
        # Let's take a look at the scrupt:
        s3_client = self.session.client('s3')

        try:
            resp = s3_client.upload_file(post_install_script_prefix, self.my_bucket_name, post_install_script_prefix)
        except ClientError as e:
            print(e)


        # Replace the placeholder with value in config.ini
        print("Prepare the config file")


        ph = {'${REGION}': self.region, 
              '${VPC_ID}': self.vpc_id, 
              '${SUBNET_ID}': subnet_id, 
              '${KEY_NAME}': self.ssh_key_name, 
              '${POST_INSTALL_SCRIPT_LOCATION}': post_install_script_location, 
              '${POST_INSTALL_SCRIPT_ARGS}': post_install_script_args
             }

        self.template_to_file("config/"+self.config_name+".ini", "build/"+self.config_name, ph)

            
    def create_after(self):
        # ## Update IAM policy and security group 
        # 
        # Use boto3 to 
        # 1. Update a policy in parallelcluster head-node instance role, to allow the head-node to access Secret Manager.
        # 2. Add inbound rule to allow access to the REST API from this notebook
        # 

        # Use the stack name to find the resources created with the parallelcluster. Use some of the information to update
        # the IAM policy and security group
        cluster_stack_name = 'parallelcluster-'+self.pcluster_name


        #Step 1. Get the head-node's instanace role and headnode security group 
        cf_client = boto3.client('cloudformation')
        root_role_info = cf_client.describe_stack_resource(StackName=cluster_stack_name, LogicalResourceId='RootRole' )
        sg_info = cf_client.describe_stack_resource(StackName=cluster_stack_name, LogicalResourceId='MasterSecurityGroup' )

        #Root role  and security group physical resource id
        head_sg_name = sg_info['StackResourceDetail']['PhysicalResourceId']

        # Step 3. get the vpc local CIDR range 
        ec2 = boto3.resource('ec2')
        vpc = ec2.Vpc(self.vpc_id)
        cidr = vpc.cidr_block

        workshop.update_security_group(head_sg_name, cidr, 8082)


    def cleanup_after(self,KeepRDS=True,KeepSSHKey=True):
    
        # delete the rds database
        if not KeepRDS:
            workshop.detele_rds_instance(self.region, self.session, self.db_name)
            workshop.delete_secrets_with_force(self.region, self.session, [self.rds_secret_name])

        print(f"Deleting secret {self.slurm_secret_name}")
        workshop.delete_secrets_with_force(self.region, self.session, [self.slurm_secret_name])
        print(f"Deleting secret {self.mungekey_secret_name}")
        workshop.delete_secrets_with_force(self.region, self.session, [self.mungekey_secret_name])
        print(f"Deleting bucket {self.my_bucket_name}")
        workshop.delete_bucket_with_version(self.my_bucket_name)

        if not KeepSSHKey:
            print(f"Deleting ssh_key {self.ssh_key_name}")        
            workshop.delete_keypair(self.region, self.session, self.ssh_key_name)


    def test(self):
        print(os.popen('ls').read())


        # Helper function to display the queue status nicely.
    def display_table(self, data):
        html = "<table>"
        for row in data:
            html += "<tr>"
            for field in row:
                html += "<td><h4>%s</h4><td>"%(field)
            html += "</tr>"
        html += "</table>"
        display(HTML(html))

    ###
    # Retrieve the slurm_token from the SecretManager
    #
    def get_secret(self):

        # Create a Secrets Manager client
        client = self.session.client(
            service_name='secretsmanager',
            region_name=self.region
        )

        # In this sample we only handle the specific exceptions for the 'GetSecretValue' API.
        # See https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        # We rethrow the exception by default.

        try:
            get_secret_value_response = client.get_secret_value(SecretId=self.slurm_secret_name)
        except ClientError as e:
            print("Error", e)
        else:
            # Decrypts secret using the associated KMS CMK.
            # Depending on whether the secret is a string or binary, one of these fields will be populated.
            if 'SecretString' in get_secret_value_response:
                secret = get_secret_value_response['SecretString']
                return secret
            else:
                decoded_binary_secret = base64.b64decode(get_secret_value_response['SecretBinary'])
                return decoded_binary_secret

    ###
    # Retrieve the token and inject into the header for JWT auth
    #
    def update_header_token(self):
        # we use 'slurm' as the default user on head node for slurm commands
        token = self.get_secret()
        post_headers = {'X-SLURM-USER-NAME':'slurm', 'X-SLURM-USER-TOKEN': token, 'Content-type': 'application/json', 'Accept': 'application/json'}
        get_headers = {'X-SLURM-USER-NAME':'slurm', 'X-SLURM-USER-TOKEN': token, 'Content-type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
        return [post_headers, get_headers]

    ###
    # Convert response into json
    #
    def convert_response(self,resp):
        resp_str = resp.content.decode('utf-8')
        return json.loads(resp_str)



    ###
    # Print a json array in table format
    # input: headers [json attribute name, ... ]
    # input: a - array of json objects
    def print_table_from_json_array(self, headers, a):
        # add headers as the first row.
        t = [headers]
        for item in a:
            result = []
            for h in headers:
                result.append(item[h])
            t.append(result)
        self.display_table(t)

    def print_table_from_dict(self, headers, d):
        result = list()
        for k,v in d.items():
            result.append(v)
        self.print_table_from_json_array(headers, result)


    ### 
    # wrapper for get
    #
    def get_response_as_json(self, base_url):
        _, get_headers = self.update_header_token()
        try:
            resp = requests.get(base_url, headers=get_headers, verify=False)
#            if resp.status_code != 200:
#                # This means something went wrong.
#                print("Error" , resp.status_code)
        except requests.exceptions.ConnectionError:
            resp.status_code = "Connection refused"
        return self.convert_response(resp)


    ### 
    # wrapper for post
    #
    def post_response_as_json(self, base_url, data):
        post_headers, _ = self.update_header_token()
        resp = requests.post(base_url, headers=post_headers, data=data)
        if resp.status_code != 200:
            # This means something went wrong.
            print("Error" , resp.status_code)

        return self.convert_response(resp)

    ###
    # Epoch time conversion
    #
    def get_localtime(self, t):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))


    # create batch and 
    def upload_athena_files(self, input_file, batch_file, my_prefix):
        session = boto3.Session()
        s3_client = session.client('s3')

        try:
            resp = s3_client.upload_file('build/'+input_file, self.my_bucket_name, my_prefix+'/'+input_file)
            resp = s3_client.upload_file('build/'+batch_file, self.my_bucket_name, my_prefix+'/'+batch_file)
        except ClientError as e:
            print(e)