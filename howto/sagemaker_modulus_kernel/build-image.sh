#!/bin/bash 

export IMAGE_TAG=smstudio-modulus
ACCOUNT_ID=716665088992

sm-docker build . -t smstudio-modulus -t $ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/smstudio-custom:smstudio-modulus --repository smstudio-custom:smstudio-modulus --role MySageMaker-ExecutionRole-Superman

