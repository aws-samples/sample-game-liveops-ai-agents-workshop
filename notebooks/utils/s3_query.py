"""
S3 Select utilities for querying player data.
"""

import boto3
import json
import pandas as pd
import re
from typing import List, Dict

def execute_s3_select_query(sql_criteria: str, bucket_name: str, region: str = 'us-west-2') -> List[str]:
    """Execute SQL query on S3 data and return player IDs."""
    try:
        s3 = boto3.client('s3', region_name=region)
        
        # Load all 4 datasets
        profiles_response = s3.get_object(Bucket=bucket_name, Key='player_profiles.json')
        profiles_df = pd.DataFrame(json.loads(profiles_response['Body'].read().decode('utf-8')))
        
        spend_response = s3.get_object(Bucket=bucket_name, Key='lifetime_spend.json')
        spend_df = pd.DataFrame(json.loads(spend_response['Body'].read().decode('utf-8')))
        
        attributes_response = s3.get_object(Bucket=bucket_name, Key='player_attributes.json')
        attributes_data = json.loads(attributes_response['Body'].read().decode('utf-8'))
        
        # Flatten player_attributes nested data
        flattened_attributes = []
        for record in attributes_data:
            flat_record = {'player_id': record['player_id']}
            attrs = record.get('additional_attributes', {})
            
            # Extract crew stats
            crew = attrs.get('crew_composition', [])
            flat_record['crew_count'] = len(crew)
            flat_record['avg_crew_level'] = sum(c.get('level', 0) for c in crew) / len(crew) if crew else 0
            
            # Extract mission stats  
            missions = attrs.get('recent_missions', [])
            flat_record['recent_missions_count'] = len(missions)
            completed_missions = [m for m in missions if m.get('outcome') == 'completed']
            flat_record['mission_success_rate'] = len(completed_missions) / len(missions) if missions else 0
            flat_record['avg_mission_time'] = sum(m.get('completion_time', 0) for m in missions) / len(missions) if missions else 0
            
            # Extract skills
            skills = attrs.get('skill_upgrades', [])
            flat_record['skill_count'] = len(skills)
            
            flattened_attributes.append(flat_record)
        
        attributes_df = pd.DataFrame(flattened_attributes)
        
        session_response = s3.get_object(Bucket=bucket_name, Key='session_history.json')
        session_df = pd.DataFrame(json.loads(session_response['Body'].read().decode('utf-8')))
        
        # Join all datasets
        merged_df = profiles_df.merge(spend_df, on='player_id', how='left')
        merged_df = merged_df.merge(attributes_df, on='player_id', how='left')
        
        # Aggregate session data
        session_agg = session_df.groupby('player_id').agg({
            'session_duration_minutes': ['mean', 'sum', 'count']
        }).reset_index()
        session_agg.columns = ['player_id', 'avg_session_duration', 'total_session_time', 'session_count']
        merged_df = merged_df.merge(session_agg, on='player_id', how='left')
        
        # Fill NaN values
        merged_df = merged_df.fillna(0)
        
        # Extract WHERE clause. The agent often passes a bare condition with no
        # SELECT/WHERE wrapper (e.g. 'platform = "PC" AND total_spend_usd > 50'),
        # so fall back to treating the whole string as the condition.
        if 'WHERE' in sql_criteria.upper():
            idx = sql_criteria.upper().index('WHERE')
            where_clause = sql_criteria[idx + len('WHERE'):].strip()
        else:
            where_clause = sql_criteria.strip()
        
        if not where_clause:
            return merged_df['player_id'].tolist()
        
        print(f"Original WHERE clause: {where_clause}")
        print(f"Merged dataframe shape: {merged_df.shape}")
        print(f"Available columns: {list(merged_df.columns)}")
        
        # Parse conditions with proper pandas syntax
        conditions = where_clause
        
        # Replace table aliases (p. and s. prefixes)
        conditions = re.sub(r'\bp\.', '', conditions)
        conditions = re.sub(r'\bs\.', '', conditions)
        
        # Convert a single '=' to '==' (ignoring '==', '>=', '<=', '!=') regardless of spacing.
        # Must run before column-name wrapping so it does not touch injected merged_df[...] text.
        conditions = re.sub(r'(?<![<>=!])=(?!=)', ' == ', conditions)
        
        # Handle BETWEEN clauses
        between_pattern = r'(\w+)\s+BETWEEN\s+(\d+(?:\.\d+)?)\s+AND\s+(\d+(?:\.\d+)?)'
        def replace_between(match):
            col, start, end = match.groups()
            return f'({col} >= {start}) & ({col} <= {end})'
        conditions = re.sub(between_pattern, replace_between, conditions)
        
        # Handle IS NULL and IS NOT NULL
        conditions = re.sub(r'(\w+)\s+IS\s+NULL', r'(\1 == 0)', conditions)
        conditions = re.sub(r'(\w+)\s+IS\s+NOT\s+NULL', r'(\1 > 0)', conditions)
        
        # Handle IN clauses properly
        in_pattern = r"(\w+)\s+IN\s+\(([^)]+)\)"
        def replace_in_clause(match):
            column = match.group(1)
            values = match.group(2)
            # Convert 'iOS', 'Android' to ['iOS', 'Android']
            values_list = [v.strip().strip("'\"") for v in values.split(',')]
            return f'{column}.isin({values_list})'
        conditions = re.sub(in_pattern, replace_in_clause, conditions)
        
        # Add dataframe references to column names.
        # Sort longest-first and skip names already wrapped in merged_df["..."].
        for col in sorted(merged_df.columns, key=len, reverse=True):
            pattern = r'(?<!["\w])' + re.escape(col) + r'(?!["\w])'
            replacement = f'merged_df["{col}"]'
            conditions = re.sub(pattern, replacement, conditions)
        
        # Handle AND/OR operators with proper parentheses
        conditions = conditions.replace(' AND ', ') & (').replace(' OR ', ') | (')
        conditions = f'({conditions})'
        
        print(f"Final conditions: {conditions}")
        
        # Execute the filter
        try:
            mask = eval(conditions)
            filtered_df = merged_df[mask]
            print(f"Filtered results: {len(filtered_df)} players")
            return filtered_df['player_id'].tolist()
        except Exception as e:
            print(f"Error evaluating conditions '{conditions}': {e}")
            return []
        
        return filtered_df['player_id'].tolist()
        
    except Exception as e:
        print(f"Error executing S3 query: {e}")
        return []

def get_segment_count(sql_criteria: str, bucket_name: str, region: str = 'us-west-2') -> int:
    """Get count of players matching SQL criteria."""
    player_ids = execute_s3_select_query(sql_criteria, bucket_name, region)
    return len(player_ids)
