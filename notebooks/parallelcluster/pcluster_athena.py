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
import base64
import docker
import pandas as pd
import importlib
import project_path # path to helper methods
from lib import workshop
from botocore.exceptions import ClientError


session = boto3.session.Session()
region = session.region_name


### assuem you have created a database secret in SecretManager with the name "slurm_dbd_credential"
def get_slurm_dbd_rds_secret(secret_name):

    # Create a Secrets Manager client
    client = session.client(
        service_name='secretsmanager',
        region_name=region
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
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
def replace_placeholder(content, values):
    for k,v in values.items():
        content=content.replace(k, v)
    return content
        
def template_to_file(source_file, target_file, mapping):
    with open(source_file, "rt") as f:
        content = f.read()
        with open(target_file, "wt") as fo:
            fo.write(replace_placeholder(content, mapping))    

            
def create_pcluster():
    
    ec2_client = boto3.client('ec2')

    # Get the aws account number where this notebook, the cluster sits. It's used in IAM policy resource
    my_account_id = boto3.client('sts').get_caller_identity().get('Account')

    # specify the following names

    # ssh key for access the pcluster. this key is not needed  in this excercise, but useful if you need to ssh into the headnode of the pcluster
    key_name = 'pcluster-athena-key'
    keypair_saved_path = './'+key_name+'.pem'
    # unique name of the pcluster
    pcluster_name = 'myTestCluster'
    # the rds for the Slurmdbd datastore. We will use a MySQL server as the data store. Server's hostname, username, password will be saved in a secret in Secrets Manager
    rds_secret_name = 'slurm_dbd_credential'
    db_name = 'pclusterdb'

    # the slurm REST token is generated from the headnode and stored in Secrets Manager. This token is used in makeing REST API calls to the Slurm REST endpoint running on the headnode 
    slurm_secret_name = "slurm_token_{}".format(pcluster_name)
    # We only need one subnet for the pcluster, but two subnets are needed for RDS instance. If use existing VPC, we will use the default VPC, and the first subnet in default VPC
    use_existing_vpc = True


    # we will not need to use the ssh_key in this excercise. However, you can only download the key once during creation. we will save it in case
    try:
        workshop.create_keypair(region, session, key_name, keypair_saved_path)
    except ClientError as e:
        if e.response['Error']['Code'] == "InvalidKeyPair.Duplicate":
            print("KeyPair with the name {} alread exists. Skip".format(key_name))


    # ## VPC
    # 
    # You can use the existing default VPC or create a new VPC with 2 subnets. 
    # 
    # We will only be using one of the subnets for the ParallelCluster, but both are used for the RDS database. 

    if use_existing_vpc:
        vpc_filter = [{'Name':'isDefault', 'Values':['true']}]
        default_vpc = ec2_client.describe_vpcs(Filters=vpc_filter)
        vpc_id = default_vpc['Vpcs'][0]['VpcId']

        subnet_filter = [{'Name':'vpc-id', 'Values':[vpc_id]}]
        subnets = ec2_client.describe_subnets(Filters=subnet_filter)
        subnet_id = subnets['Subnets'][0]['SubnetId']
        subnet_id2 = subnets['Subnets'][1]['SubnetId']    
    else: 
        vpc, subnet1, subnet2 = workshop.create_and_configure_vpc()
        vpc_id = vpc.id
        subnet_id = subnet1.id
        subnet_id2 = subnet2.id


    # Create the project bucket. 
    # we will use this bucket for the scripts, input and output files 


    bucket_prefix = pcluster_name.lower()+'-'+my_account_id

    # use the bucket prefix as name, don't use uuid suffix
    my_bucket_name = workshop.create_bucket(region, session, bucket_prefix, False)
    print(my_bucket_name)


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
    workshop.create_simple_mysql_rds(region, session, db_name, [subnet_id,subnet_id2] ,rds_secret_name)


    rds_client = session.client('rds', region)
    rds_waiter = rds_client.get_waiter('db_instance_available')

    try:
        print("Waiting for RDS instance creation to complete ... ")
        rds_waiter.wait(DBInstanceIdentifier=db_name) 
    except botocore.exceptions.WaiterError as e:
        print(e)

    #since the rds creation is asynch, need to wait till the creation is done to get the hostname, then update the secret with the hostname
    vpc_sgs = workshop.get_sgs_and_update_secret(region, session, db_name, rds_secret_name)
    print(vpc_sgs)

    # Step 3. get the vpc local CIDR range 
    ec2 = boto3.resource('ec2')
    vpc = ec2.Vpc(vpc_id)
    cidr = vpc.cidr_block

    # update the RDS security group to allow inbound traffic to port 3306
    workshop.update_security_group(vpc_sgs[0]['VpcSecurityGroupId'], cidr, 3306)

    os.system('pcluster version')


    # ### ParallelCluster config file
    # Start with the the configuration template file 
    # 

    # #### Setup parameters for PCluster
    # 
    # We will be using a relational database on AWS (RDS) for Slurm accounting (slurmdbd). Please refer to this blog for how to set it up https://aws.amazon.com/blogs/compute/enabling-job-accounting-for-hpc-with-aws-parallelcluster-and-amazon-rds/
    # 
    # Once you set up the MySQL RDS, create a secret in SecretManager with the type "Credentials for RDS", so we don't need to expose the database username/password in plain text in this notebook. 



    # the response is a json {"username": "xxxx", "password": "xxxx", "engine": "mysql", "host": "xxxx", "port": "xxxx", "dbInstanceIdentifier", "xxxx"}
    rds_secret = json.loads(get_slurm_dbd_rds_secret(rds_secret_name))

    post_install_script_prefix = 'scripts/post_install_script.sh'
    post_install_script_location = "s3://{}/{}".format(my_bucket_name, post_install_script_prefix)
    post_install_script_args = "'" + rds_secret['host']+' '+str(rds_secret['port']) +' ' + rds_secret['username'] + ' ' + rds_secret['password'] + ' ' + pcluster_name +"'" 


    # ### Post installation script
    # This script is used to recompile and configure slurm with slurmrestd. We also added the automation of compiling Athena++ in the script. 
    # 
    # Let's take a look at the scrupt:
    s3_client = session.client('s3')

    try:
        resp = s3_client.upload_file('scripts/pcluster_post_install.sh', my_bucket_name, post_install_script_prefix)
    except ClientError as e:
        print(e)


    # Replace the placeholder with value in config.ini
    print("Prepare the config file")
    

    ph = {'${REGION}': region, 
          '${VPC_ID}': vpc_id, 
          '${SUBNET_ID}': subnet_id, 
          '${KEY_NAME}': key_name, 
          '${POST_INSTALL_SCRIPT_LOCATION}': post_install_script_location, 
          '${POST_INSTALL_SCRIPT_ARGS}': post_install_script_args
         }

    template_to_file("config/config.ini", "build/config", ph)


    # #### Create a pcluster with the config file
    # 
    # The -nr note is used to tell cloudformation not to roll back when there is an error - this is only needed for development. 
    # 
    # After the cluster is created, we will use boto to setup the following permissions
    # 1. Add IAM permission on the head-node instance role to allow access to Secret Manager for storing slurm token 
    # 2. Add Inbound rule to allow "All traffic" from the SageMaker notebook instance (for Slurmrest API access)
    # 



#    os.system('pcluster create {} -nr -c build/config'.format(pcluster_name))

    print(os.popen('pcluster create {} -nr -c build/config'.format(pcluster_name)).read())
    
    # ## Update IAM policy and security group 
    # 
    # Use boto3 to 
    # 1. Update a policy in parallelcluster head-node instance role, to allow the head-node to access Secret Manager.
    # 2. Add inbound rule to allow access to the REST API from this notebook
    # 

    # Use the stack name to find the resources created with the parallelcluster. Use some of the information to update
    # the IAM policy and security group
    cluster_stack_name = 'parallelcluster-'+pcluster_name


    #Step 1. Get the head-node's instanace role and headnode security group 
    cf_client = boto3.client('cloudformation')
    root_role_info = cf_client.describe_stack_resource(StackName=cluster_stack_name, LogicalResourceId='RootRole' )
    sg_info = cf_client.describe_stack_resource(StackName=cluster_stack_name, LogicalResourceId='MasterSecurityGroup' )

    #Root role  and security group physical resource id
    root_role_name = root_role_info['StackResourceDetail']['PhysicalResourceId']
    head_sg_name = sg_info['StackResourceDetail']['PhysicalResourceId']

    # Step 2. Add Secret Manager access permission to the role
    # pcluster will create an inline policy "parallelcluster" and attach to the root role, we will update that
    iam_client = boto3.client('iam')

    policy_doc = iam_client.get_role_policy(RoleName=root_role_name, PolicyName="parallelcluster").get('PolicyDocument')
    policy_statement = policy_doc.get('Statement')    

    # in this notebook, we might re-run this block multiple times , to avoid duplication of the tokensecret sid need to do this loop
    flag = False
    sid = 'tokensecret'
    for stmt in policy_statement:
        if ( stmt ['Sid'] == sid):
            flag = True
            print("{} statement is already there, skip".format(sid))
            break

    if not flag :        
        my_doc_statement = {}
        my_doc_statement['Action'] = ['secretsmanager:DescribeSecret','secretsmanager:CreateSecret','secretsmanager:UpdateSecret']
        my_doc_statement['Resource'] = ['arn:aws:secretsmanager:us-east-1:{}:secret:*'.format(my_account_id)]
        my_doc_statement['Effect'] = 'Allow'
        my_doc_statement['Sid'] = sid
        policy_doc['Statement'].append(my_doc_statement)
        iam_client.put_role_policy(RoleName=root_role_name,PolicyName="parallelcluster", PolicyDocument=json.dumps(policy_doc))

    # Step 3. get the vpc local CIDR range 
    ec2 = boto3.resource('ec2')
    vpc = ec2.Vpc(vpc_id)
    cidr = vpc.cidr_block

    workshop.update_security_group(head_sg_name, cidr, 8082)


    return my_bucket_name, db_name, slurm_secret_name, rds_secret_name


def cleanup_cluster(my_bucket_name, db_name, slurm_secret_name, rds_secret_name, pcluster_name):
    os.system('pcluster delete {}'.format(pcluster_name) )
    # delete the rds database
    workshop.detele_rds_instance(region, session, db_name)
    #Delete the secrets
    workshop.delete_secrets_with_force(region, session, [slurm_secret_name, rds_secret_name])
    workshop.delete_bucket_with_version(my_bucket_name)
    
    
def test():
    print(os.popen('ls').read())
