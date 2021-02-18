# Using AWS ParallelCluster for Research

## Introduction
Athena++ (https://www.athena-astro.app/) uses MPI and OpenMP to solve 1-3D megetohydrodynamics problems in astrophysical environments. For more information about Athena++, please visit 
https://github.com/PrincetonUniversity/athena-public-version/wiki. The problem Athena++ can solve can be large. High resolution simulations sometimes require multiple nodes. 

In this notebook, we will use Athena++ to learn how to run a tightly coupled numerical sumulation on AWS ParallelCluster. 

We will be using pcluster command line (CLI) along with AWS boto3 SDK to create the infrastructure (VPCs, Subnets, Security Groups, IAMs) and then the ParallelCluster itself. We will then prepare a initial input file of an Orszag-Tang vortext test (https://www.astro.princeton.edu/~jstone/Athena/tests/orszag-tang/pagesource.html) and submit the job to the ParallelcCluster through Slurm REST API, the end-point of which runs on the head-node of the ParallelCluster. 

At the end, we will visulize the simulation results. 

## About AWS ParallelCluster - https://aws.amazon.com/hpc/parallelcluster/

AWS ParallelCluster is an AWS-supported open source cluster management tool that makes it easy for you to deploy and manage High Performance Computing (HPC) clusters on AWS. ParallelCluster uses a simple plaintext configuration files to specify the infrastructure. A python command (pcluster - PyPI) then use this config file to provision the cluster. AWS ParallelCluster supports multiple scheduer, but in this notebook, we will use Slurm. 


## Getting started
We will be creating and interacting with an instance of AWS ParallelCluster from this Jupyter Notebook. This requires the execution role of this Jupyter Notebook to have certain permissions. 

The "pclusterDefaultPolicy.json" and "pclusterNotebookPolicy.json" can be used to set the permissions. 

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
