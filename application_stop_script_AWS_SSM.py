import boto3
import sys
import time
from botocore.exceptions import ClientError, BotoCoreError

REGION = "us-east-1"
# APPLICATION_TAG = "myapp"  
ROLE_ORDER = ["frontend", "middleware", "database"]
SSM_COMMAND_WAIT = 30

STOP_COMMANDS_BY_ROLE = {
    "frontend": ["systemctl stop httpd"],
    "middleware": ["systemctl stop httpd"],
    "database": ["systemctl stop httpd"]
}


ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)

def get_instances_by_tags(app_name):
    """Fetch all running EC2 instances filtered by ApplicationName tag."""
    try:
        filters = [
            {"Name": "tag:applicationname", "Values": [app_name]},
            {"Name": "instance-state-name", "Values": ["running"]}
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

def stop_instances(instance_ids):
    """Stop EC2 instances."""
    if not instance_ids:
        return
    try:
        ec2.stop_instances(InstanceIds=instance_ids)
        print(f"Stop command sent for instances: {instance_ids}")
    except (ClientError, BotoCoreError) as e:
        print(f"Error stopping instances {instance_ids}: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python application_stop_script_AWS_SSM.py <application_name>")
        sys.exit(1)
    
    application_name = sys.argv[1]
    print(f"Stopping application: {application_name}")
    
    all_instances = get_instances_by_tags(application_name)
    if not all_instances:
        print("No instances found. Exiting.")
        return
    
    for role in ROLE_ORDER:
        role_instances = [inst["InstanceId"] for inst in all_instances if inst["Role"] == role]
        if not role_instances:
            continue
        
        print(f"\nProcessing Role: {role}, Instances: {role_instances}")
        
        stop_app_commands = STOP_COMMANDS_BY_ROLE.get(role, [])
        output = run_ssm_command(role_instances, stop_app_commands)
        for o in output:
            print(f"{o['InstanceId']} - Status: {o['Status']}")
            if o['StdOut']:
                print(f"StdOut: {o['StdOut']}")
            if o['StdErr']:
                print(f"StdErr: {o['StdErr']}")
        
        stop_instances(role_instances)
        print(f"Instances {role_instances} are shutting down.\n")
        
if __name__ == "__main__":
    main()
