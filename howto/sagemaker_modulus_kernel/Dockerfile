FROM nvcr.io/nvidia/modulus/modulus:22.07

ARG NB_USER="sagemaker-user"
ARG NB_UID="1000"
ARG NB_GID="100"

RUN \
    useradd --create-home --shell /bin/bash --gid "${NB_GID}" --uid ${NB_UID} ${NB_USER} && \
    python -m pip install ipykernel && \
    python -m ipykernel install --sys-prefix --name=modulus


RUN pip install \
        'boto3>=1,<2' \
        'sagemaker>=2,<3' \
        'sagemaker-experiments>=0.1,<0.2' \
        'sagemaker-studio-image-build>=0.4,<0.5' \
        'smdebug>=0.9,<0.10'
# This image is using a version that's newer than what SageMaker is using. fix an imcompatibility issue 
RUN sed -i '70 i \                args_parser.add_argument("-f", "--fff", help="a dummy argument to fool ipython", default="1")' /modulus/modulus/hydra/utils.py

# Make the default shell bash (vs "sh") for a better Jupyter terminal UX
ENV SHELL=/bin/bash

# Install the AWS CLI:
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip
#USER root
RUN ./aws/install

USER $NB_UID

