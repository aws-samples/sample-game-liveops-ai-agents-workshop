import boto3
import json
import zipfile
import time
import requests
import base64
from botocore.exceptions import ClientError

def create_gateway_lambda(lambda_filename):
    """Create Lambda function for AgentCore Gateway"""
    from boto3 import Session
    import botocore.exceptions
    
    boto_session = Session()
    region = boto_session.region_name
    
    return_resp = {"lambda_function_arn": "Pending", "exit_code": 1}
    
    lambda_client = boto3.client('lambda', region_name=region)
    iam_client = boto3.client('iam', region_name=region)

    # Expect .py file, create .zip file
    if lambda_filename.endswith('.py'):
        zip_filename = lambda_filename.replace('.py', '.zip')
        py_filename = lambda_filename
    else:
        zip_filename = lambda_filename + '.zip'
        py_filename = lambda_filename + '.py'
    
    role_name = 'game-analytics-lambda-role'
    lambda_function_name = 'game-analytics-gateway-function'
    
    # Create deployment package
    with zipfile.ZipFile(zip_filename, 'w') as zip_file:
        zip_file.write(py_filename)
        # Add utils package if it exists
        import os
        if os.path.exists('utils'):
            for root, dirs, files in os.walk('utils'):
                for file in files:
                    if file.endswith('.py'):
                        file_path = os.path.join(root, file)
                        zip_file.write(file_path)
    
    print("Reading code from zip file")
    with open(zip_filename, 'rb') as f:
        lambda_function_code = f.read()
    
    try:
        print("Creating IAM role for lambda function")
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            Description="IAM role for gaming analytics Lambda function"
        )
        role_arn = response['Role']['Arn']
        
        print("Attaching policies to the IAM role")
        for policy_arn in [
            'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
            'arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess'
        ]:
            iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

        # Scoped S3 + Bedrock permissions 
        account_id = boto3.client('sts').get_caller_identity()['Account']
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName='ScopedS3BedrockAccess',
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [
                            "arn:aws:s3:::games-data-*",
                            "arn:aws:s3:::games-data-*/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream"
                        ],
                        "Resource": "arn:aws:bedrock:*::foundation-model/cohere.embed-english-v3"
                    }
                ]
            })
        )
        
        # Add S3 Vectors permissions
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName='S3VectorsAccess',
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": ["s3vectors:QueryVectors", "s3vectors:GetVectors", "s3vectors:GetIndex"],
                    "Resource": [
                        f"arn:aws:s3vectors:{region}:{account_id}:bucket/game-embeddings/*",
                        f"arn:aws:s3vectors:{region}:{account_id}:bucket/game-embeddings"
                    ]
                }]
            })
        )
        
        print(f"Role '{role_name}' created successfully: {role_arn}")
        
    except botocore.exceptions.ClientError as error:
        if error.response['Error']['Code'] == "EntityAlreadyExists":
            response = iam_client.get_role(RoleName=role_name)
            role_arn = response['Role']['Arn']
            print(f"IAM role {role_name} already exists. Using the same ARN {role_arn}")
        else:
            error_message = error.response['Error']['Code'] + "-" + error.response['Error']['Message']
            print(f"Error creating role: {error_message}")
            return_resp['lambda_function_arn'] = error_message
            return return_resp
    
    time.sleep(10)  # Wait for role to be ready
    
    print("Creating lambda function")
    try:
        lambda_response = lambda_client.create_function(
            FunctionName=lambda_function_name,
            Role=role_arn,
            Runtime='python3.11',
            Handler='game_analytics_lambda.lambda_handler',
            Code={'ZipFile': lambda_function_code},
            Description='Gaming analytics tools for AgentCore Gateway',
            Timeout=60,
            MemorySize=256
        )
        return_resp['lambda_function_arn'] = lambda_response['FunctionArn']
        return_resp['exit_code'] = 0
        
    except botocore.exceptions.ClientError as error:
        if error.response['Error']['Code'] == "ResourceConflictException":
            lambda_client.update_function_code(FunctionName=lambda_function_name, ZipFile=lambda_function_code)
            response = lambda_client.get_function(FunctionName=lambda_function_name)
            lambda_arn = response['Configuration']['FunctionArn']
            print(f"Lambda function {lambda_function_name} already exists. Updated code. ARN: {lambda_arn}")
            return_resp['lambda_function_arn'] = lambda_arn
            return_resp['exit_code'] = 0
        else:
            error_message = error.response['Error']['Code'] + "-" + error.response['Error']['Message']
            print(f"Error creating lambda function: {error_message}")
            return_resp['lambda_function_arn'] = error_message
    
    create_pandas_layer_and_attach()
    
    return return_resp

    
    return return_resp

def create_agentcore_gateway_role(agent_name):
    """Create IAM role for AgentCore Gateway with comprehensive permissions"""
    iam_client = boto3.client('iam')
    sts_client = boto3.client('sts')
    
    agentcore_role_name = f'agentcore-{agent_name}-role'
    region = boto3.Session().region_name
    account_id = sts_client.get_caller_identity()["Account"]
    
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockPermissions",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:DescribeLogStreams",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogGroups",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets"
                ],
                "Resource": ["*"]
            },
            {
                "Effect": "Allow",
                "Resource": "*",
                "Action": "cloudwatch:PutMetricData",
                "Condition": {
                    "StringEquals": {
                        "cloudwatch:namespace": "bedrock-agentcore"
                    }
                }
            },
            {
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": f"arn:aws:lambda:{region}:{account_id}:function:game-analytics-*"
            },
            {
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": f"arn:aws:iam::{account_id}:role/agentcore-{agent_name}-role"
            },
            {
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId"
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{agent_name}-*"
                ]
            }
        ]
    }
    
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {
                    "Service": "bedrock-agentcore.amazonaws.com"
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": f"{account_id}"
                    },
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"
                    }
                }
            }
        ]
    }

    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy_document)
        )
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(RoleName=agentcore_role_name, MaxItems=100)
        for policy_name in policies['PolicyNames']:
            iam_client.delete_role_policy(RoleName=agentcore_role_name, PolicyName=policy_name)
        iam_client.delete_role(RoleName=agentcore_role_name)
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy_document)
        )

    try:
        iam_client.put_role_policy(
            PolicyDocument=json.dumps(role_policy),
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_role_name
        )
    except Exception as e:
        print(e)

    return agentcore_iam_role

def get_or_create_user_pool(cognito, USER_POOL_NAME):
    response = cognito.list_user_pools(MaxResults=60)
    for pool in response["UserPools"]:
        if pool["Name"] == USER_POOL_NAME:
            user_pool_id = pool["Id"]
            response = cognito.describe_user_pool(
                UserPoolId=user_pool_id
            )
        
            # Get the domain from user pool description
            user_pool = response.get('UserPool', {})
            domain = user_pool.get('Domain')
        
            if domain:
                region = user_pool_id.split('_')[0] if '_' in user_pool_id else REGION
                domain_url = f"https://{domain}.auth.{region}.amazoncognito.com"
                print(f"Found domain for user pool {user_pool_id}: {domain} ({domain_url})")
            else:
                print(f"No domains found for user pool {user_pool_id}")
            return pool["Id"]
    print('Creating new user pool')
    created = cognito.create_user_pool(PoolName=USER_POOL_NAME)
    user_pool_id = created["UserPool"]["Id"]
    user_pool_id_without_underscore_lc = user_pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(
        Domain=user_pool_id_without_underscore_lc,
        UserPoolId=user_pool_id
    )
    print("Domain created as well")
    return created["UserPool"]["Id"]

def get_or_create_resource_server(cognito_client, user_pool_id, server_id, server_name, scopes):
    """Get existing resource server or create new one"""
    try:
        try:
            cognito_client.describe_resource_server(UserPoolId=user_pool_id, Identifier=server_id)
            return
        except cognito_client.exceptions.ResourceNotFoundException:
            pass
        
        cognito_client.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=server_id,
            Name=server_name,
            Scopes=scopes
        )
    except Exception as e:
        raise Exception(f"Error with resource server: {e}")

def get_or_create_m2m_client(cognito_client, user_pool_id, client_name, resource_server_id):
    """Get existing M2M client or create new one"""
    response = cognito_client.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)
    for client in response["UserPoolClients"]:
        if client["ClientName"] == client_name:
            describe = cognito_client.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client["ClientId"])
            return client["ClientId"], describe["UserPoolClient"]["ClientSecret"]
    
    print('creating new m2m client')
    created = cognito_client.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[f"{resource_server_id}/gateway:read", f"{resource_server_id}/gateway:write"],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"]
    )
    return created["UserPoolClient"]["ClientId"], created["UserPoolClient"]["ClientSecret"]

def get_token(user_pool_id, client_id, client_secret, scope, region):
    """Get access token from Cognito using client credentials flow"""
    try:
        user_pool_id_without_underscore = user_pool_id.replace("_", "")
        url = f"https://{user_pool_id_without_underscore}.auth.{region}.amazoncognito.com/oauth2/token"
        
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope
        }
        
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        print(f"Cognito OAuth error: {e}")
        return {"error": str(e)}
    
def delete_gateway(gateway_client, gateway_id):
    """Delete AgentCore Gateway"""
    try:
        gateway_client.delete_gateway(gatewayIdentifier=gateway_id)
        print("Gateway deleted successfully")
    except Exception as e:
        print(f"Error deleting gateway: {e}")

def create_pandas_layer_and_attach():
    """Attach AWS managed pandas layer to Lambda function"""
    lambda_client = boto3.client('lambda')
    lambda_function_name = 'game-analytics-gateway-function'
    
    # Use AWS managed pandas layer for us-west-2, Python 3.11
    aws_pandas_layer_arn = 'arn:aws:lambda:us-west-2:336392948345:layer:AWSSDKPandas-Python311:24'
    
    # Wait for Lambda function to be ready and retry
    print("Waiting for Lambda function to be ready...")
    max_retries = 10
    for attempt in range(max_retries):
        try:
            # Attach layer to Lambda
            lambda_client.update_function_configuration(
                FunctionName=lambda_function_name,
                Layers=[aws_pandas_layer_arn]
            )
            print(f"Attached AWS managed pandas layer: {aws_pandas_layer_arn}")
            return aws_pandas_layer_arn
        except lambda_client.exceptions.ResourceConflictException:
            if attempt < max_retries - 1:
                print(f"Lambda still updating, waiting 15 seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(15)
            else:
                raise
    
    return aws_pandas_layer_arn
