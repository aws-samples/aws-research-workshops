#!/bin/bash 

export IMAGE_TAG=smstudio-modulus

sm-docker build . -t smstudio-modulus -t <account-id>.dkr.ecr.us-east-1.amazonaws.com/smstudio-custom:smstudio-modulus --repository smstudio-custom:smstudio-modulus --role MySageMaker-ExecutionRole-Superman

