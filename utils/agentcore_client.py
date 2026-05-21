"""
AgentCore Runtime Client for invoking segmentation and campaign agents using boto3
"""
import boto3
import json
import base64
import uuid
from typing import Dict, Any, Optional

class AgentCoreClient:
    """Client for interacting with AgentCore runtime using boto3"""
    
    def __init__(self, agent_runtime_arn: str, region: str = 'us-west-2'):
        self.agent_runtime_arn = agent_runtime_arn
        self.region = region
        self.client = boto3.client('bedrock-agentcore', region_name=region)
        self.endpoint = agent_runtime_arn
    
    def invoke_agent(self, prompt: str) -> Dict[str, Any]:
        try:
            payload = json.dumps({'prompt': prompt})
            session_id = str(uuid.uuid4()).replace('-', '') + 'session'
            
            response = self.client.invoke_agent_runtime(
                agentRuntimeArn=self.agent_runtime_arn,
                runtimeSessionId=session_id,
                payload=payload,
                qualifier='DEFAULT'
            )
            
            response_body = response['response'].read()
            response_data = json.loads(response_body)
            return response_data
            
        except Exception as e:
            return {'error': f'AgentCore invocation failed: {str(e)}', 'fallback': True}
