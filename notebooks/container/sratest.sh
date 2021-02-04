#!/bin/bash
set -x

# this is where ncbi/sra-toolkit is installed on the container inside the pegi3s/sratookit image
#export PATH="/opt/sratoolkit.2.9.6-ubuntu64/bin:${PATH}"
prefetch $PACKAGE_NAME --output-directory /tmp
fasterq-dump $PACKAGE_NAME -e 18
aws s3 sync . $SRA_OUTPUT/$PACKAGE_NAME
    
