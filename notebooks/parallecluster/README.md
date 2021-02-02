# Using AWS ParallelCluster for Research

## Introduction

## About AWS ParallelCluster



## Getting started
We will be interacting with an instance of AWS ParallelCluster from a Jupyter Notebook. 

Step 1. Create a SageMaker Notebook from your AWS console
The sagemaker execution role attached to the notebook needs to have certain IAM permissions 

Create a MyPClusterPermissionPolicy following instruction on https://aws-parallelcluster.readthedocs.io/en/latest/iam.html. Additional permissions:
```
        {
            "Sid": "SSMDescribe",
            "Action": [
                "ssm:GetParametersByPath"
            ],
            "Effect": "Allow",
            "Resource": "*"
        },
        {
            "Sid": "route53",
            "Action": [
                "route53:CreateHostedZone",
                "route53:GetChange"
            ],
            "Effect": "Allow",
            "Resource": "*"
        }
```

Attach "MyPClusterPermissionPolicy" , "CloudWatchFullAccess", "LambdaFullAccess", "S3FullAccess" policy to the SageMaker execution role. 
Step 2. Open a termial windows in SageMaker Jupyter Notebook

