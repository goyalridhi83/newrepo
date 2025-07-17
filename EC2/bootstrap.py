import boto3
import logging
import os

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()

# --- AWS Configuration ---
region = "ap-south-1"
ami_id = "ami-0a1235697f4afa8a4"
instance_type = "t3.micro"
key_name = "fastapi-key"
security_group_ids = ["sg-03239302b41fb1f9b"]
subnet_id = "subnet-0c21652e5bde06a58"
eip_allocation_id = "eipalloc-000a4c982d826f568"

user_data_script = """#!/bin/bash
set -e

# Define variables for domain and email
DOMAIN="sumitgoyalapp.xyz"
EMAIL="goyalridhi83@gmail.com"
BUCKET="sumitgoyalappxyz"
REGION="ap-south-1"
REPO_DIR="/home/ec2-user/newrepo"

# --- PACKAGE INSTALLATION ---
dnf update -y
dnf install -y git nginx python3 python3-pip
rpm -ivh --nodeps https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm

# --- ADD SWAP SPACE TO PREVENT OOM KILL ---
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
# --- END SWAP SPACE ADDITION ---

# Now, install Certbot with the extra memory available
dnf install -y certbot python3-certbot-nginx

# --- CLEANUP SWAP ---
swapoff /swapfile
rm /swapfile
# --- END CLEANUP ---

fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
# --- END SWAP SPACE ADDITION ---

# Now, install Certbot with the extra memory available
dnf install -y certbot python3-certbot-nginx

# --- CLEANUP SWAP ---
# Deactivate and remove the swap file now that it's no longer needed
swapoff /swapfile
rm /swapfile
# --- END CLEANUP ---

# --- APPLICATION SETUP ---
# Enable and start Nginx
systemctl enable nginx
systemctl start nginx

# Clone your public GitHub repo
cd /home/ec2-user
git clone https://github.com/goyalridhi83/newrepo.git
cd newrepo
git checkout main_aws

# Set permissions for the entire repo directory
chown -R ec2-user:ec2-user ${REPO_DIR}

# Install Python dependencies
sudo -u ec2-user pip3 install --upgrade pip
sudo -u ec2-user pip3 install -r requirements.txt

# Make log file writable
touch ${REPO_DIR}/stock_scanner.log
chmod 664 ${REPO_DIR}/stock_scanner.log

# Create and load .env file
cat <<EOF > ${REPO_DIR}/.env
WEBHOOK_SECRET=xanvestatechsecret
KITE_API_KEY=wt1b63ihts1q60wt
KITE_API_SECRET=rhl5o9yydobp4hfpmgl3lx9kkotnyk0t
REQUEST_TOKEN_PATH=request_token.txt
ACCESS_TOKEN_PATH=access_token.txt
ACTIVE_CONTRACTS_PATH=cache/active_contracts.json
EOF

# Change the owner of the .env file to ec2-user so the service can read it
chown ec2-user:ec2-user ${REPO_DIR}/.env
chmod 600 ${REPO_DIR}/.env

# Create systemd service for the FastAPI app
cat <<EOF > /etc/systemd/system/fastapi-webhook.service
[Unit]
Description=FastAPI Webhook Service
After=network.target

[Service]
User=ec2-user
Group=ec2-user
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and start the FastAPI service
systemctl daemon-reload
systemctl enable fastapi-webhook
systemctl start fastapi-webhook

# Configure Nginx as a reverse proxy for HTTP
cat <<EOF > /etc/nginx/conf.d/webhook.conf
server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name ${DOMAIN};
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# Test Nginx configuration
nginx -t

# --- SSL CERTIFICATE S3 PERSISTENCE LOGIC ---
# Try to restore certs from S3
if aws s3 ls "s3://$BUCKET/letsencrypt/" 2>&1 | grep -q 'PRE'; then
    echo "Restoring SSL certificates from S3..."
    sudo mkdir -p /etc/letsencrypt
    sudo aws s3 sync "s3://$BUCKET/letsencrypt/" /etc/letsencrypt/
else
    echo "No SSL backup found on S3, will issue new certificate."
fi

# Issue certificate only if not present
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
    # Backup new certs to S3
    echo "Uploading new SSL certificates to S3..."
    sudo aws s3 sync /etc/letsencrypt/ "s3://$BUCKET/letsencrypt/"
else
    echo "SSL certificate already present, skipping issuance."
fi
# --- END SSL CERTIFICATE S3 PERSISTENCE LOGIC ---

# Reload Nginx to apply the new SSL configuration from Certbot
systemctl reload nginx
"""

# --- AWS Clients ---
ec2_client = boto3.client("ec2", region_name=region)
ec2_resource = boto3.resource("ec2", region_name=region)

# --- Step 1: Terminate Existing Running Instances ---
def terminate_running_instances():
    logger.info("Checking for running EC2 instances...")
    running_instances = ec2_resource.instances.filter(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "pending"]}]
    )
    instance_ids = [instance.id for instance in running_instances]

    if instance_ids:
        logger.info(f"Terminating instances: {instance_ids}")
        ec2_client.terminate_instances(InstanceIds=instance_ids)

        logger.info("Waiting for instances to terminate...")
        waiter = ec2_client.get_waiter("instance_terminated")
        waiter.wait(InstanceIds=instance_ids)
        logger.info("All previous EC2 instances terminated.")
    else:
        logger.info("No running instances found.")

# --- Step 2: Launch New EC2 Instance ---
import botocore
import json

def ensure_iam_role_and_instance_profile():
    iam = boto3.client('iam')
    role_name = 'EC2S3SSLCertRole'
    instance_profile_name = role_name + 'InstanceProfile'
    policy_name = 'EC2S3SSLCertPolicy'
    bucket_name = 'sumitgoyalappxyz'

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }
        ]
    }
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:CreateBucket",
                    "s3:ListBucket",
                    "s3:GetObject",
                    "s3:PutObject"
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*"
                ]
            }
        ]
    }
    # Create role if not exists
    try:
        iam.get_role(RoleName=role_name)
        print(f"Role {role_name} already exists.")
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for EC2 to persist SSL certs to S3"
        )
        print(f"Created role {role_name}.")
    # Create policy if not exists
    # Find or create the policy in your account
    policy_arn = None
    paginator = iam.get_paginator('list_policies')
    for page in paginator.paginate(Scope='Local'):
        for pol in page['Policies']:
            if pol['PolicyName'] == policy_name:
                policy_arn = pol['Arn']
                print(f"Policy {policy_name} already exists.")
                break
        if policy_arn:
            break

    if not policy_arn:
        policy_response = iam.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_doc)
        )
        policy_arn = policy_response['Policy']['Arn']
        print(f"Created policy {policy_name}.")
    # Attach the policy to the role
    try:
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn
        )
    except Exception as e:
        print(f"Policy already attached or error: {e}")
    # Create instance profile if not exists
    try:
        iam.get_instance_profile(InstanceProfileName=instance_profile_name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_instance_profile(InstanceProfileName=instance_profile_name)
        iam.add_role_to_instance_profile(
            InstanceProfileName=instance_profile_name,
            RoleName=role_name
        )
        # Wait for propagation
        import time
        for _ in range(20):
            try:
                iam.get_instance_profile(InstanceProfileName=instance_profile_name)
                break
            except iam.exceptions.NoSuchEntityException:
                time.sleep(3)
        else:
            raise Exception("Instance profile did not propagate in time.")
    return instance_profile_name

def launch_instance():
    logger.info("Launching new EC2 instance...")
    instance_profile_name = ensure_iam_role_and_instance_profile()
    response = ec2_client.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        KeyName=key_name,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=security_group_ids,
        SubnetId=subnet_id,
        IamInstanceProfile={"Name": instance_profile_name},
        UserData=user_data_script,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "AutoEC2"}],
            }
        ],
    )

    instance_id = response["Instances"][0]["InstanceId"]
    logger.info(f"Instance {instance_id} launched. Waiting until running...")
    waiter = ec2_client.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])
    logger.info(f"Instance {instance_id} is now running.")
    return instance_id

# --- Step 3: Detach Existing EIP (if any) ---
def detach_eip_if_associated():
    logger.info(f"Checking EIP {eip_allocation_id} for existing associations...")
    addresses = ec2_client.describe_addresses(AllocationIds=[eip_allocation_id])
    if addresses['Addresses'] and 'AssociationId' in addresses['Addresses'][0]:
        association_id = addresses['Addresses'][0]['AssociationId']
        instance_id = addresses['Addresses'][0].get('InstanceId', 'unknown')
        logger.info(f"EIP is associated with instance {instance_id}, detaching...")
        ec2_client.disassociate_address(AssociationId=association_id)
        logger.info("EIP disassociated.")
    else:
        logger.info("EIP is not currently associated.")

# --- Step 4: Attach EIP to New Instance ---
def attach_eip(instance_id):
    logger.info(f"Attaching EIP {eip_allocation_id} to instance {instance_id}...")
    ec2_client.associate_address(
        InstanceId=instance_id,
        AllocationId=eip_allocation_id
    )
    logger.info("EIP attached successfully.")

# --- Execute Automation ---
if __name__ == "__main__":
    terminate_running_instances()
    new_instance_id = launch_instance()
    detach_eip_if_associated()
    attach_eip(new_instance_id)

    # --- Stream /var/log/cloud-init-output.log from the instance at the end ---
    import time
    import sys
    import paramiko
    import socket
    # Get public IP of the instance
    desc = ec2_client.describe_instances(InstanceIds=[new_instance_id])
    public_ip = desc['Reservations'][0]['Instances'][0]['PublicIpAddress']
    logger.info(f"Attempting SSH to {public_ip} to stream /var/log/cloud-init-output.log ...")
    key_path = f"./key/{key_name}.pem"  # Update path if your key is elsewhere
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    timeout = 600
    poll_interval = 10
    elapsed = 0
    while elapsed < timeout:
        try:
            ssh.connect(public_ip, username='ec2-user', key_filename=os.path.expanduser(key_path), timeout=10)
            logger.info("SSH connection established. Streaming cloud-init log:")
            stdin, stdout, stderr = ssh.exec_command('sudo cat /var/log/cloud-init-output.log')
            for line in stdout:
                sys.stdout.write(line)
            for line in stderr:
                sys.stdout.write(line)
            ssh.close()
            break
        except (paramiko.ssh_exception.NoValidConnectionsError, socket.timeout, TimeoutError, paramiko.ssh_exception.SSHException) as e:
            logger.info(f"Waiting for SSH: {e}")
            time.sleep(poll_interval)
            elapsed += poll_interval
        except Exception as e:
            logger.error(f"Unexpected SSH error: {e}")
            break
