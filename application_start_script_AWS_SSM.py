import sys
import boto3
import time
from botocore.exceptions import ClientError, BotoCoreError

REGION = "us-east-1"
# APPLICATION_TAG = "myapp"  
# Reverse order for starting: database -> middleware -> frontend
ROLE_ORDER = ["database", "middleware", "frontend"]
SSM_COMMAND_WAIT = 5  
INSTANCE_START_WAIT = 30  # Wait for instances to be running before SSM commands

START_COMMANDS_BY_ROLE = {
    "frontend": ["systemctl start httpd"],
    "middleware": ["systemctl start httpd"],
    "database": ["systemctl start httpd"]
}


ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)

def get_instances_by_tags(app_name):
    """Fetch all stopped EC2 instances filtered by ApplicationName tag."""
    try:
        filters = [
            {"Name": "tag:applicationname", "Values": [app_name]},
            {"Name": "instance-state-name", "Values": ["stopped"]}
        ]
        response = ec2.describe_instances(Filters=filters)
        
        instances = []
        for reservation in response['Reservations']:
            for inst in reservation['Instances']:
                role_tag = next((tag['Value'] for tag in inst.get('Tags', []) if tag['Key'] == "Role"), None)
                instances.append({
                    "InstanceId": inst['InstanceId'],
                    "Role": role_tag
                })
        return instances
    except (ClientError, BotoCoreError) as e:
        print(f"Error fetching instances: {e}")
        return []

def start_instances(instance_ids):
    """Start EC2 instances and wait for them to be running."""
    if not instance_ids:
        return
    try:
        ec2.start_instances(InstanceIds=instance_ids)
        print(f"Start command sent for instances: {instance_ids}")
        
        # Wait for instances to be in running state
        print(f"Waiting for instances to reach 'running' state...")
        waiter = ec2.get_waiter('instance_running')
        waiter.wait(InstanceIds=instance_ids)
        print(f"Instances {instance_ids} are now running.")
        
        # Additional wait for SSM agent to be ready
        print(f"Waiting {INSTANCE_START_WAIT}s for SSM agent to be ready...")
        time.sleep(INSTANCE_START_WAIT)
        
    except (ClientError, BotoCoreError) as e:
        print(f"Error starting instances {instance_ids}: {e}")

def run_ssm_command(instance_ids, commands):
    """Run a list of commands on instances via SSM."""
    if not instance_ids:
        return []
    try:
        response = ssm.send_command(
            InstanceIds=instance_ids,
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
            TimeoutSeconds=600
        )
        command_id = response['Command']['CommandId']
        print(f"SSM Command sent. Command ID: {command_id}")
        
        # Wait and fetch command output
        time.sleep(SSM_COMMAND_WAIT)
        outputs = []
        for iid in instance_ids:
            try:
                output = ssm.get_command_invocation(CommandId=command_id, InstanceId=iid)
                outputs.append({
                    "InstanceId": iid,
                    "Status": output.get("Status", "Unknown"),
                    "StdOut": output.get("StandardOutputContent", ""),
                    "StdErr": output.get("StandardErrorContent", "")
                })
            except (ClientError, BotoCoreError) as e:
                outputs.append({
                    "InstanceId": iid,
                    "Status": "Failed",
                    "StdOut": "",
                    "StdErr": str(e)
                })
        return outputs
    except (ClientError, BotoCoreError) as e:
        print(f"Error sending SSM command: {e}")
        return []


def main():
    if len(sys.argv) < 2:
        print("Usage: python application_start_script_AWS_SSM.py <application_name>")
        sys.exit(1)
    
    application_name = sys.argv[1]
    print(f"Starting application: {application_name}")
    
    all_instances = get_instances_by_tags(application_name)
    if not all_instances:
        print("No instances found. Exiting.")
        return
    
    for role in ROLE_ORDER:
        role_instances = [inst["InstanceId"] for inst in all_instances if inst["Role"] == role]
        if not role_instances:
            continue
        
        print(f"\nProcessing Role: {role}, Instances: {role_instances}")
        
        # Start instances first
        start_instances(role_instances)
        
        # Then run application start commands
        start_app_commands = START_COMMANDS_BY_ROLE.get(role, [])
        output = run_ssm_command(role_instances, start_app_commands)
        for o in output:
            print(f"{o['InstanceId']} - Status: {o['Status']}")
            if o['StdOut']:
                print(f"StdOut: {o['StdOut']}")
            if o['StdErr']:
                print(f"StdErr: {o['StdErr']}")
        
        print(f"Instances {role_instances} started and applications launched.\n")
if __name__ == "__main__":
    main()
