"""
Segment management utilities for the Player Segmentation Workshop.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
import pandas as pd
import re

def parse_segments_from_response(response_text: str) -> List[Dict]:
    """Parse segments from AI response text."""
    try:
        # Handle nested response structure
        if isinstance(response_text, str):
            # Try to extract from nested response format
            if "result" in response_text and "content" in response_text:
                try:
                    response_dict = eval(response_text) if isinstance(response_text, str) else response_text
                    if "result" in response_dict:
                        content = response_dict["result"].get("content", [])
                        if isinstance(content, list) and len(content) > 0 and "text" in content[0]:
                            response_text = content[0]["text"]
                except:
                    pass
        
        # Try to parse JSON segments
        if '{' in response_text and '"segments"' in response_text:
            # Find JSON block with segments
            lines = response_text.split('\n')
            json_lines = []
            in_json = False
            brace_count = 0
            
            for line in lines:
                if '{' in line and '"segments"' in line:
                    in_json = True
                    brace_count += line.count('{') - line.count('}')
                    json_lines.append(line)
                elif in_json:
                    brace_count += line.count('{') - line.count('}')
                    json_lines.append(line)
                    if brace_count <= 0:
                        break
            
            if json_lines:
                json_text = '\n'.join(json_lines)
                try:
                    data = json.loads(json_text)
                    segments = data.get('segments', [])
                    if segments:
                        return segments
                except json.JSONDecodeError:
                    pass
        
        # Alternative: look for segments array directly
        if '"segments": [' in response_text:
            start = response_text.find('"segments": [')
            if start != -1:
                # Find the matching closing bracket
                bracket_count = 0
                end = start + len('"segments": [')
                for i, char in enumerate(response_text[end:], end):
                    if char == '[':
                        bracket_count += 1
                    elif char == ']':
                        if bracket_count == 0:
                            end = i + 1
                            break
                        bracket_count -= 1
                
                segments_text = '{"segments": ' + response_text[start + len('"segments": '):end] + '}'
                try:
                    data = json.loads(segments_text)
                    return data.get('segments', [])
                except:
                    pass
                    
    except Exception as e:
        print(f"Error parsing segments: {e}")
    
    return []  # Return empty list if no segments found


def extract_player_ids_from_criteria(criteria: str, player_data=None) -> List[str]:
    """Extract player IDs using S3 Select queries."""
    try:
        from utils.s3_query import execute_s3_select_query
        import boto3
        
        # Get bucket name from SSM
        session = boto3.Session()
        region = session.region_name or 'us-west-2'
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.get_parameter(Name='/workshop/games-data-bucket-name')
        bucket_name = response['Parameter']['Value']
        
        # Execute S3 Select query
        return execute_s3_select_query(criteria, bucket_name, region)
        
    except Exception as e:
        print(f"Error extracting player IDs with S3 Select: {e}")
        # Fallback to local data if available
        if player_data is not None and not player_data.empty:
            return player_data['player_id'].sample(min(50, len(player_data))).tolist()
        return []


class SegmentManager:
    """Manages player segments - saving, loading, and exporting."""
    
    def __init__(self, segments_dir: str = "segments"):
        self.segments_dir = segments_dir
        os.makedirs(segments_dir, exist_ok=True)
    
    def save_segment(self, name: str, description: str, player_ids: List[str], 
                    criteria: str) -> str:
        """Save a player segment."""
        # Check if segment with same name already exists
        existing_segments = self.load_all_segments()
        existing_segment = next((s for s in existing_segments if s['name'] == name), None)
        
        if existing_segment:
            # Update existing segment
            segment_id = existing_segment['segment_id']
            segment_data = {
                "segment_id": segment_id,
                "name": name,
                "description": description,
                "criteria": criteria,
                "created_date": existing_segment['created_date'],
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        else:
            # Create new segment
            segment_id = str(uuid.uuid4())
            segment_data = {
                "segment_id": segment_id,
                "name": name,
                "description": description,
                "criteria": criteria,
                "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        
        filename = f"{segment_id}.json"
        filepath = os.path.join(self.segments_dir, filename)
        
        with open(filepath, 'w') as f:
            json.dump(segment_data, f, indent=2)
        
        return segment_id
    
    def load_all_segments(self) -> List[Dict]:
        """Load all saved segments."""
        segments = []
        
        if not os.path.exists(self.segments_dir):
            return segments
        
        for filename in os.listdir(self.segments_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.segments_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        segment_data = json.load(f)
                        segments.append(segment_data)
                except Exception as e:
                    print(f"Error loading segment {filename}: {e}")
        
        # Sort by creation date (newest first)
        segments.sort(key=lambda x: x.get('created_date', ''), reverse=True)
        return segments
    
    def delete_segment(self, segment_id: str) -> bool:
        """Delete a segment."""
        filename = f"{segment_id}.json"
        filepath = os.path.join(self.segments_dir, filename)
        
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
        except Exception as e:
            print(f"Error deleting segment: {e}")
        
        return False
    
    def export_player_ids(self, segment_id: str, format: str = 'txt') -> str:
        """Export player IDs for a segment."""
        filename = f"{segment_id}.json"
        filepath = os.path.join(self.segments_dir, filename)
        
        if not os.path.exists(filepath):
            return ""
        
        try:
            with open(filepath, 'r') as f:
                segment_data = json.load(f)
            
            player_ids = segment_data.get('player_ids', [])
            
            if format == 'txt':
                return '\n'.join(player_ids)
            elif format == 'json':
                return json.dumps({
                    'segment_name': segment_data.get('name', ''),
                    'player_ids': player_ids,
                    'total_count': len(player_ids),
                    'exported_at': datetime.now().isoformat()
                }, indent=2)
            
        except Exception as e:
            print(f"Error exporting segment: {e}")
        
        return ""
