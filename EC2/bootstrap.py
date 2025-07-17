import boto3
import logging

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

# --- SSL CERTIFICATE S3 PERSISTENCE LOGIC ---
BUCKET="sumitgoyalappxyz"  # <-- CHANGE THIS
REGION="ap-south-1"           # <-- CHANGE THIS
DOMAIN="sumitgoyalapp.xyz"     # <-- CHANGE THIS if needed
EMAIL="goyalridhi83@gmail.com" # <-- CHANGE THIS if needed

# Create S3 bucket if it doesn't exist (no error if it already exists)
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" --create-bucket-configuration LocationConstraint="$REGION"
fi

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

# Define variables for domain and email
DOMAIN="sumitgoyalapp.xyz"
EMAIL="goyalridhi83@gmail.com"
REPO_DIR="/home/ec2-user/newrepo"

# --- PACKAGE INSTALLATION ---
dnf update -y
dnf install -y git nginx python3 python3-pip

rpm -ivh --nodeps https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm

# --- ADD SWAP SPACE TO PREVENT OOM KILL ---
# Create and activate a 1GB swap file to provide extra memory
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

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# Test Nginx configuration and request SSL certificate
nginx -t
certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos -m ${EMAIL} --redirect

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
def launch_instance():
    logger.info("Launching new EC2 instance...")
    response = ec2_client.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        KeyName=key_name,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=security_group_ids,
        SubnetId=subnet_id,
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
