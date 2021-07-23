# Using AWS ParallelCluster for Research

This workshop is intended for users who are familiar with AWS ParallelCluster, Jupyter Notebook and basic AWS infrastructure concepts. If you are looking for a more introductory workshop for AWS ParallelCluster, please visit https://www.hpcworkshops.com/. 

AWS ParallelCluster is an open source cluster management tool that makes it easy for you to deploy and manage High Performance Computing (HPC) clusters on AWS. Currently, using a scheduler like Slurm to submit a job, check queue status or list current job requires a user to SSH into the headnode of the cluster. 

In this workshop, we will learn how to enable SlurmREST API and Slurm Accounting, using post install scripts during the cluster creation. Instead of running pcluster and aws CLI on command line to build up infrastructure and the cluster, we will be using a fully managed Jupyter Notebook in Amazon SageMaker to execute the pcluster command and boto3 SDK for infrastructure creation. 

We will also use a popular MHD (Meganeto Hydrodynamics) package Athena++ as and example to show you how to run multi-node MPI programs on AWS ParallelCluster. 

Learning objects of this workshop
- How to interact with AWS ParallelCluster via REST API
- How to allocate cost of individual jobs using AWS Cost and Usage (CUR) data and Slurm Account.

## Introduction
Athena++ (https://www.athena-astro.app/) uses MPI and OpenMP to solve 1-3D meganetohydrodynamics problems in astrophysical environments. For more information about Athena++, please visit 
https://github.com/PrincetonUniversity/athena-public-version/wiki. The problem Athena++ can solve can be large. High resolution simulations sometimes require multiple nodes. 

In notebooks "pcluster-athena++" and "pcluster-athena++short" (more concise version), we will 
- Use Athena++ to learn how to run a tightly coupled numerical sumulation on AWS ParallelCluster with Slurm scheduler. 
- Use pcluster command line (CLI) along with AWS boto3 SDK to create the infrastructure (VPCs, Subnets, Security Groups, IAMs) and then the ParallelCluster itself. We will then prepare a initial input file of an Orszag-Tang vortext test (https://www.astro.princeton.edu/~jstone/Athena/tests/orszag-tang/pagesource.html) and submit the job to the ParallelcCluster through Slurm REST API, the end-point of which runs on the head-node of the ParallelCluster. 
- At the end, we will visulize the simulation results. 

In notebook "pcluster-accounting", we will exam the CUR using queries on Amazon Athena (not to be confused with MDH package Athena++) and how to correlate the cost of the cluster and queues with Slurm Accounting data. 


## Getting started
We will be creating and interacting with an instance of AWS ParallelCluster from this Jupyter Notebook. This requires the execution role of this Jupyter Notebook to have certain permissions. 

Details about the policies are described in this document. 
https://docs.aws.amazon.com/parallelcluster/latest/ug/iam.html#parallelclusteruserpolicy

### Step 1. Create a SageMaker Notebook from your AWS console
https://docs.aws.amazon.com/sagemaker/latest/dg/howitworks-create-ws.html

- For IAM role, choose either an existing IAM role in your account that has the necessary permissions to access SageMaker resources or choose Create a new role. 
- If you choose Create a new role, SageMaker creates an IAM role named AmazonSageMaker-ExecutionRole-YYYYMMDDTHHmmSS. The AWS managed policy AmazonSageMakerFullAccess is attached to the role. The role provides permissions that allow the notebook instance to call SageMaker and Amazon S3.

### Step 2. Start the notebook and open a terminal window

From File/New/Terminal , and run the following command

```
cd SageMaker
git clone https://github.com/aws-samples/aws-research-workshops
```

You will pull the content of this repo into the SageMaker notebook. 

### Step 3. Add additional permissions

Find the Sage execution role create two inline policy with the content of "policy"

The permissions are described in https://aws-parallelcluster.readthedocs.io/en/latest/iam.html. 

### Step 4. Open pcluster-athena++ notebooks

If you want to go through the entire process, including building of the pcluster, use pcluter-athena++.ipynb

If you want to skip the provision of the pcluster and just want to run Athena++, use pcluster-athena++-short.ipynb

### Step 5. Pleast don't forget to clean up. 

The last few steps in the notebooks can help you clean up resources created in this notebook
