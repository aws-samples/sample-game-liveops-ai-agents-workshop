"""
Streamlit Frontend for AI Player Segmentation Workshop
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json
import os
import sys
import boto3
import uuid
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Remove local agent imports - using AgentCore runtime instead
from utils.segment_manager import SegmentManager, extract_player_ids_from_criteria, parse_segments_from_response
from utils.agentcore_client import AgentCoreClient

# Page config
st.set_page_config(
    page_title="Player Segmentation Dashboard",
    page_icon="🎮",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'segment_manager' not in st.session_state:
    st.session_state.segment_manager = SegmentManager()
if 'agentcore_client' not in st.session_state:
    st.session_state.agentcore_client = None
if 'campaign_agentcore_client' not in st.session_state:
    st.session_state.campaign_agentcore_client = None

def load_data_from_s3():
    """Load data from S3 games-data bucket using SSM parameter."""
    try:
        # Get current region with debugging
        session = boto3.Session()
        region = session.region_name
        
        # Use fallback if needed
        if not region:
            region = 'us-west-2'
        
        # Get bucket name from SSM
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.get_parameter(Name='/workshop/games-data-bucket-name')
        bucket_name = response['Parameter']['Value']
        
        # Load datasets from S3
        s3 = boto3.client('s3', region_name=region)
        datasets = {}
        
        dataset_files = {
            "player_profiles": "player_profiles.json",
            "lifetime_spend": "lifetime_spend.json", 
            "session_history": "session_history.json",
            "player_attributes": "player_attributes.json",
            "items": "items.json"
        }
        
        for name, filename in dataset_files.items():
            try:
                response = s3.get_object(Bucket=bucket_name, Key=filename)
                datasets[name] = json.loads(response['Body'].read().decode('utf-8'))
            except Exception as e:
                st.error(f"Error loading {name}: {e}")
                
        return datasets
    except Exception as e:
        st.error(f"Error loading data from S3: {e}")
        return {}

def load_comprehensive_data():
    """Load comprehensive player data from all datasets."""
    try:
        datasets = load_data_from_s3()
        
        # Start with all players from profiles
        profiles = datasets.get("player_profiles", [])
        player_lookup = {p["player_id"]: p.copy() for p in profiles}
        
        # Merge other datasets
        for dataset_name, dataset in datasets.items():
            if dataset_name == "player_profiles":
                continue
            for record in dataset:
                player_id = record.get("player_id")
                if player_id in player_lookup:
                    if dataset_name == "session_history":
                        if "session_count" not in player_lookup[player_id]:
                            player_lookup[player_id]["session_count"] = 0
                        player_lookup[player_id]["session_count"] += 1
                    else:
                        player_lookup[player_id].update(record)
        
        return pd.DataFrame(list(player_lookup.values()))
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return pd.DataFrame()

def create_enhanced_viz(df, viz_type="distribution"):
    """Create basic data visualizations without predefined segments."""
    if df.empty:
        return None
    
    if viz_type == "distribution":
        # Simple spending distribution - no segments
        if 'total_spend_usd' in df.columns:
            fig = px.histogram(df, x='total_spend_usd', nbins=20, 
                             title='Player Spending Distribution')
            return fig
    
    elif viz_type == "engagement_scatter":
        # Raw engagement vs churn data
        if 'engagement_intensity_score' in df.columns and 'churn_risk_score' in df.columns:
            fig = px.scatter(df, x='engagement_intensity_score', y='churn_risk_score',
                           color='platform', title='Engagement vs Churn Risk by Platform',
                           hover_data=['total_spend_usd'] if 'total_spend_usd' in df.columns else None)
            return fig
    
    elif viz_type == "platform":
        # Platform distribution
        if 'platform' in df.columns:
            platform_counts = df['platform'].value_counts()
            fig = px.bar(x=platform_counts.index, y=platform_counts.values,
                        title='Player Distribution by Platform')
            return fig
    
    return None

def show_data_overview(filtered_df):
    """Show data overview and visualizations page."""
    # Metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric("Total Players", len(filtered_df))
    with col2:
        if 'engagement_intensity_score' in filtered_df.columns:
            avg_engagement = filtered_df['engagement_intensity_score'].mean()
            st.metric("Avg Engagement", f"{avg_engagement:.1f}")
        else:
            st.metric("Avg Engagement", "N/A")
    with col3:
        if 'total_spend_usd' in filtered_df.columns:
            total_revenue = filtered_df['total_spend_usd'].sum()
            st.metric("Total Revenue", f"${total_revenue:,.0f}")
        else:
            st.metric("Total Revenue", "$0")
    with col4:
        if 'churn_risk_score' in filtered_df.columns:
            at_risk = len(filtered_df[filtered_df['churn_risk_score'] > 0.7])
            st.metric("At Risk Players", at_risk)
        else:
            st.metric("At Risk Players", "N/A")
    with col5:
        if 'session_count' in filtered_df.columns:
            avg_sessions = filtered_df['session_count'].mean()
            st.metric("Avg Sessions", f"{avg_sessions:.1f}")
        else:
            st.metric("Avg Sessions", "N/A")
    
    # Visualizations
    st.header("📊 Data Visualization")
    
    # First row - 2 visualizations
    viz_col1, viz_col2 = st.columns(2)
    
    with viz_col1:
        spending_fig = create_enhanced_viz(filtered_df, "distribution")
        if spending_fig:
            st.plotly_chart(spending_fig, use_container_width=True)
    
    with viz_col2:
        platform_fig = create_enhanced_viz(filtered_df, "platform")
        if platform_fig:
            st.plotly_chart(platform_fig, use_container_width=True)
    
    # Second row - engagement scatter (full width)
    engagement_fig = create_enhanced_viz(filtered_df, "engagement_scatter")
    if engagement_fig:
        st.plotly_chart(engagement_fig, use_container_width=True)
    
    # Data table
    st.header("📋 Player Data")
    st.write(f"**Available data columns:** {', '.join(filtered_df.columns)}")
    display_count = min(50, len(filtered_df))
    st.write(f"**Showing top {display_count} of {len(filtered_df)} total players**")
    st.dataframe(filtered_df.head(display_count), use_container_width=True)
    
    # Show visualizations from current conversation only
    if 'last_discovery' in st.session_state and 'visualizations' in st.session_state.last_discovery:
        st.header("📊 Generated Visualizations")
        for viz_path in st.session_state.last_discovery['visualizations']:
            if os.path.exists(viz_path):
                st.image(viz_path, width=400)  # Smaller width

def show_segment_discovery():
    """Show segment discovery with predefined prompts."""
    
    # Segment discovery
    st.subheader("🚀 Discover Segments")
    
    # Fixed prompt for segment discovery
    discovery_prompt = "Analyze our player data and discover meaningful player segments based on patterns you find. Focus on actionable segments that can drive monetization and retention strategies."
    
    # Show the prompt being sent (read-only)
    st.text_area("Prompt being sent to Segmentation Agent:", discovery_prompt, height=100, disabled=True)
    
    if st.button("🚀 Discover Segments", type="primary"):
        with st.spinner("Discovering segments..."):
            try:
                if st.session_state.agentcore_client:
                    response = st.session_state.agentcore_client.invoke_agent(discovery_prompt)
                    # Extract text immediately like the boto3 example
                    if isinstance(response, dict) and 'result' in response:
                        agent_text = response['result']['content'][0]['text']
                    else:
                        agent_text = str(response)
                else:
                    st.error("❌ No AgentCore endpoint configured. Please configure in the sidebar.")
                    return
                
                # Store the response
                st.session_state.last_discovery = {
                    "prompt": discovery_prompt,
                    "response": agent_text,  # Store the extracted text, not the full object
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                
                st.success("Segments discovered! See results below.")
                
            except Exception as e:
                st.error(f"Error: {e}")
    
    # Display last discovery results
    if 'last_discovery' in st.session_state:
        discovery = st.session_state.last_discovery
        
        st.subheader("📋 Discovery Results")
        st.write(f"**Time:** {discovery['timestamp']}")
        
        # Display the extracted text directly
        st.write(discovery["response"])
        
        # Auto-parse segments from response
        parsed_segments = parse_segments_from_response(discovery['response'])
        
        if parsed_segments:
            st.subheader("🎯 Auto-Detected Segments")
            st.success(f"Found {len(parsed_segments)} segments in the response!")
            
            # Save All Segments button
            if st.button("💾 Save All Segments", type="primary", key="save_all_segments"):
                player_data = load_comprehensive_data()
                saved_count = 0
                
                for segment in parsed_segments:
                    try:
                        # Query players based on criteria
                        player_ids = extract_player_ids_from_criteria(segment['criteria'], player_data)
                        
                        # Save segment
                        st.session_state.segment_manager.save_segment(
                            name=segment['name'],
                            description=segment['description'],
                            player_ids=player_ids,
                            criteria=segment['criteria']
                        )
                        saved_count += 1
                    except Exception as e:
                        st.error(f"Error saving {segment['name']}: {e}")
                
                if saved_count > 0:
                    st.success(f"✅ Saved {saved_count} segments successfully!")
                    # Remove st.rerun() to prevent double-saving
            
            st.write("---")
            st.subheader("Individual Segment Editing")
            
            # Display parsed segments and allow saving
            for i, segment in enumerate(parsed_segments):
                with st.expander(f"Segment {i+1}: {segment['name']}", expanded=True):
                    with st.form(f"auto_segment_form_{i}"):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            name = st.text_input("Segment Name:", 
                                               value=segment['name'], 
                                               key=f"auto_name_{i}")
                        with col2:
                            # Don't show estimated players - will be calculated when saved
                            pass
                        
                        description = st.text_area("Description:", 
                                                 value=segment['description'], 
                                                 key=f"auto_desc_{i}")
                        
                        criteria = st.text_area("SQL Criteria:", 
                                              value=segment.get('sql_criteria', segment.get('criteria', '')), 
                                              key=f"auto_criteria_{i}")
                        
                        business_value = st.text_area("Business Value:", 
                                                    value=segment.get('business_value', ''), 
                                                    key=f"auto_business_{i}")
                        
                        if st.form_submit_button(f"💾 Save {segment['name']}", type="primary"):
                            if name and description and criteria:
                                # Query players based on actual criteria
                                player_data = load_comprehensive_data()
                                if len(player_data) > 0:
                                    player_ids = extract_player_ids_from_criteria(criteria, player_data)
                                    
                                    # Update player count to actual result
                                    actual_count = len(player_ids)
                                    
                                    
                                    # Save segment
                                    segment_id = st.session_state.segment_manager.save_segment(
                                        name=name,
                                        description=description,
                                        player_ids=player_ids,
                                        criteria=criteria,
                                        
                                        
                                    )
                                    
                                    st.success(f"✅ Segment '{name}' saved with {actual_count} players !")
                                else:
                                    st.error("No player data available")
                            else:
                                st.error("Please fill in all fields")
        
        else:
            # Fallback to manual segment creation
            st.subheader("💾 Manual Segment Creation")
            st.info("No structured segments detected. Create segments manually:")
            
            # Dynamic segment creation
            if 'num_segments_to_save' not in st.session_state:
                st.session_state.num_segments_to_save = 1
            
            col1, col2 = st.columns([3, 1])
            with col1:
                num_segments = st.number_input("Number of segments to save:", min_value=1, max_value=10, 
                                             value=st.session_state.num_segments_to_save)
            with col2:
                if st.button("Update"):
                    st.session_state.num_segments_to_save = num_segments
                    st.rerun()
            
            # Create forms for each segment
            for i in range(st.session_state.num_segments_to_save):
                with st.expander(f"Segment {i+1}", expanded=True):
                    with st.form(f"manual_segment_form_{i}"):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            name = st.text_input(f"Segment Name:", 
                                               placeholder=f"e.g., High-Value Players", 
                                               key=f"manual_name_{i}")
                        with col2:
                            # Don't show estimated players - will be calculated when saved
                            pass
                        
                        description = st.text_area(f"Description:", 
                                                 placeholder="Brief description of this segment...", 
                                                 key=f"manual_desc_{i}")
                        
                        criteria = st.text_area(f"Segment Criteria:", 
                                              placeholder="Specific criteria that define this segment...", 
                                              key=f"manual_criteria_{i}")
                        
                        business_value = st.text_area(f"Business Value:", 
                                                    placeholder="Why this segment matters for business...", 
                                                    key=f"manual_business_{i}")
                        
                        if st.form_submit_button(f"Save Segment {i+1}"):
                            if name and description and criteria:
                                # Query players based on actual criteria
                                player_data = load_comprehensive_data()
                                if len(player_data) > 0:
                                    player_ids = extract_player_ids_from_criteria(criteria, player_data)
                                    
                                    # Update player count to actual result
                                    actual_count = len(player_ids)
                                    
                                    
                                    # Save segment
                                    segment_id = st.session_state.segment_manager.save_segment(
                                        name=name,
                                        description=description,
                                        player_ids=player_ids,
                                        criteria=criteria,
                                        
                                        
                                    )
                                    
                                    st.success(f"✅ Segment '{name}' saved with {actual_count} players !")
                                else:
                                    st.error("No player data available")
                            else:
                                st.error("Please fill in all fields")
def show_agent_chat():
    """Show free-form chat with agents."""
    
    # Initialize chat session ID if not exists
    if 'chat_session_id' not in st.session_state:
        import uuid
        st.session_state.chat_session_id = f"session-{uuid.uuid4().hex}"
    
    # Chat interface
    st.subheader("Ask Anything About Your Players")
    
    # Example queries
    st.write("**Try asking the agent:**")
    st.write("• Tell me everything about player_000558 - what makes them unique?")
    st.write("• Analyze player_000702's engagement and spending patterns")
    st.write("• Visualize spending distribution across different platforms")
    
    user_input = st.text_area("Your question:", 
                             placeholder="e.g., What are the characteristics of players who spend the most?",
                             height=100)
    
    if st.button("Send", type="primary") and user_input:
        with st.spinner("Thinking..."):
            try:
                if st.session_state.agentcore_client:
                    # Use session-based invocation for memory
                    import boto3
                    import json
                    import time
                    
                    # Get current region
                    session = boto3.Session()
                    region = session.region_name or 'us-west-2'
                    
                    # Use boto3 client directly for session support
                    agentcore_client = boto3.client('bedrock-agentcore', region_name=region)
                    
                    response = agentcore_client.invoke_agent_runtime(
                        agentRuntimeArn=st.session_state.agentcore_client.endpoint,
                        runtimeSessionId=st.session_state.chat_session_id,
                        payload=json.dumps({"prompt": user_input}),
                        qualifier="DEFAULT"
                    )
                    
                    # Parse response
                    response_body = response['response'].read().decode('utf-8')
                    response_data = json.loads(response_body)
                    agent_text = response_data['result']['content'][0]['text']
                    
                else:
                    st.error("❌ No AgentCore endpoint configured. Please configure in the sidebar.")
                    return
                
                # Store in chat history
                if 'chat_history' not in st.session_state:
                    st.session_state.chat_history = []
                
                st.session_state.chat_history.append({
                    "user": user_input,
                    "agent": agent_text,
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                })
                
                # Display response
                st.write("**Response:**")
                st.write(agent_text)
                
            except Exception as e:
                st.error(f"Error: {e}")
    
    # Display chat history
    if 'chat_history' in st.session_state and st.session_state.chat_history:
        st.subheader("Chat History")
        st.info(f"Session ID: {st.session_state.chat_session_id}")
        
        for i, chat in enumerate(reversed(st.session_state.chat_history[-5:])):
            with st.expander(f"🤖 {chat['timestamp']} - {chat['user'][:50]}..."):
                st.write("**You:**", chat['user'])
                st.write("**Agent:**", chat['agent'])
    
    # Clear chat button
    if st.button("🗑️ Clear Chat History"):
        st.session_state.chat_history = []
        # Generate new session ID for fresh conversation
        import uuid
        st.session_state.chat_session_id = f"session-{uuid.uuid4().hex}"
        st.success("Chat history cleared! New session started.")
        st.rerun()
    
    # S3 Visualization Gallery
    st.subheader("📊 Generated Visualizations")
    try:
        import boto3
        # Specify region explicitly
        region = 'us-west-2'  # Default region for the workshop
        s3 = boto3.client('s3', region_name=region)
        ssm = boto3.client('ssm', region_name=region)
        
        # Get bucket name from SSM
        bucket_name = ssm.get_parameter(Name='/workshop/games-data-bucket-name')['Parameter']['Value']
        
        # List visualization objects
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix='visualizations/')
        
        if 'Contents' in response:
            # Sort by last modified (newest first)
            images = sorted(response['Contents'], key=lambda x: x['LastModified'], reverse=True)
            
            st.write(f"Found {len(images)} visualizations:")
            
            # Display images in a grid
            cols = st.columns(3)
            for i, obj in enumerate(images[:12]):  # Show last 12 images
                with cols[i % 3]:
                    # Generate presigned URL for secure access
                    img_url = s3.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_name, 'Key': obj['Key']},
                        ExpiresIn=3600  # 1 hour
                    )
                    st.image(img_url, caption=obj['Key'].split('/')[-1], width=200)
                    st.caption(f"Created: {obj['LastModified'].strftime('%m/%d %H:%M')}")
        else:
            st.info("No visualizations found yet. Ask the agent to create some charts!")
            
    except Exception as e:
        st.warning(f"Could not load visualizations: {str(e)[:50]}...")
def show_ai_analysis(filtered_df):
    """Show segment analysis with sub-tabs."""
    st.header("🔍 Segment Analysis")
    st.write("Use AI to discover meaningful player segments and patterns in your data.")
    
    # Show data context
    if filtered_df.empty:
        st.warning("No data available after applying filters. Please adjust your filters in the sidebar.")
        return
    
    # Sub-tabs for different AI interactions
    sub_tab1, sub_tab2 = st.tabs(["🔍 Segment Discovery", "💬 Chat with Agent"])
    
    with sub_tab1:
        show_segment_discovery()
    
    with sub_tab2:
        show_agent_chat()
    
def show_segment_management():
    """Show segment management interface."""
    st.header("📋 Segment Management")
    
    # Load all segments
    segments = st.session_state.segment_manager.load_all_segments()
    
    if not segments:
        st.info("No saved segments yet. Create segments in the Segment Analysis tab first.")
        return
    
    # Segment library
    st.subheader("Saved Segments")
    
    for segment in segments:
        with st.expander(f"🎯 {segment['name']}"):
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.write(f"**Description:** {segment['description']}")
                st.write(f"**Created:** {segment['created_date']} ")
                
                st.write("**Criteria:**")
                st.code(segment.get('criteria', 'No criteria available'), language='sql')
            
            with col2:
                # Download player IDs button (TXT only) - generate on demand
                if st.button(f"📥 Download Player IDs", key=f"download_{segment['segment_id']}"):
                    with st.spinner("Generating player list..."):
                        # Run query only when download is requested
                        fresh_player_ids = extract_player_ids_from_criteria(segment['criteria'])
                        
                        if fresh_player_ids:
                            player_ids_data = '\n'.join(fresh_player_ids)
                            st.download_button(
                                label="📥 Download TXT File",
                                data=player_ids_data,
                                file_name=f"{segment['name']}_player_ids.txt",
                                mime="text/plain",
                                key=f"download_file_{segment['segment_id']}"
                            )
                            st.success(f"Generated {len(fresh_player_ids)} player IDs")
                        else:
                            st.error("No players found matching criteria")
                
                # Delete button
                if st.button(f"🗑️ Delete", key=f"del_{segment['segment_id']}", type="secondary"):
                    st.session_state.segment_manager.delete_segment(segment['segment_id'])
                    st.success("Segment deleted!")
                    st.rerun()
def show_campaign_recommender():
    """Show campaign recommender interface."""
    st.header("🎯 Campaign Recommender")
    st.write("Generate targeted marketing campaigns for your player segments using AI-powered recommendations.")
    
    # Load segments
    segments = st.session_state.segment_manager.load_all_segments()
    
    if not segments:
        st.warning("No segments available. Create segments in the Segment Analysis tab first.")
        return
    
    # Segment selection
    segment_options = {s['name']: s for s in segments}
    selected_segment_name = st.selectbox("Select Target Segment:", list(segment_options.keys()), key="campaign_segment_select")
    
    if selected_segment_name:
        selected_segment = segment_options[selected_segment_name]
        
        if st.button("🎯 Generate Campaign"):
            with st.spinner("Generating campaign recommendations..."):
                try:
                    # Prepare segment data for the campaign agent
                    segment_context = f"""Generate a targeted campaign for the following player segment:

    Segment: {selected_segment['name']}
    Description: {selected_segment['description']}
    Criteria: {selected_segment['criteria']}
    """
                    
                    # Show the prompt being sent (read-only)
                    st.text_area("Prompt being sent to Campaign Agent:", segment_context, height=150, disabled=True)
                    
                    # Get campaign recommendations
                    if st.session_state.campaign_agentcore_client:
                        response = st.session_state.campaign_agentcore_client.invoke_agent(segment_context)
                    else:
                        st.error("❌ No Campaign AgentCore endpoint configured. Please configure in the sidebar.")
                        return
                    
                    st.subheader("📋 Campaign Recommendations")
                    
                    # Extract text from nested response structure
                    if isinstance(response, dict) and 'result' in response:
                        result = response['result']
                        if isinstance(result, dict) and 'content' in result:
                            # Extract text from content array
                            campaign_text = result['content'][0]['text']
                            st.write(campaign_text)
                        else:
                            st.write(str(result))
                    else:
                        st.write(str(response))
                
                except Exception as e:
                    st.error(f"Error generating campaign: {e}")
def show_agent_comparison():
    """Show side-by-side agent comparison."""
    st.header("⚖️ Agent Comparison")
    st.write("**Compare how different data sources enhance AI segmentation analysis**")
    
    # Comparison prompts
    st.subheader("Select Analysis Prompt")
    comparison_prompts = [
        "Analyze our player data and discover meaningful segments based on patterns you find",
        "Identify our most valuable player segments and their characteristics", 
        "Which players are at risk of churning and suggest intervention strategies",
        "Create player segments for targeted marketing campaigns",
        "Analyze spending patterns and create monetization-focused segments"
    ]
    
    selected_prompt = st.selectbox("Choose prompt to compare:", comparison_prompts)
    
    # Initialize session state for comparison results
    if 'comparison_results' not in st.session_state:
        st.session_state.comparison_results = {}
    
    # Run comparison button
    if st.button("🚀 Run Comparison Analysis", type="primary"):
        with st.spinner("Running analysis on both agents..."):
            # Agent comparison requires AgentCore endpoint
            if not st.session_state.agentcore_client:
                st.error("❌ Agent comparison requires AgentCore endpoint configuration")
                return
                
            # Run analysis using AgentCore client
            try:
                # For comparison, we'll use the same client but indicate different agent types
                basic_response = st.session_state.agentcore_client.invoke_agent(f"[Basic Agent] {selected_prompt}")
                enhanced_response = st.session_state.agentcore_client.invoke_agent(f"[Enhanced Agent] {selected_prompt}")
                
                st.session_state.comparison_results = {
                    'prompt': selected_prompt,
                    'basic_response': str(basic_response),
                    'enhanced_response': str(enhanced_response),
                    'timestamp': datetime.now().strftime("%H:%M:%S")
                }
                
            except Exception as e:
                st.error(f"Error running comparison: {e}")
    
    # Display results if available
    if st.session_state.comparison_results:
        results = st.session_state.comparison_results
        
        st.subheader(f"📋 Analysis Results - {results['timestamp']}")
        st.info(f"**Prompt:** {results['prompt']}")
        
        # Side-by-side comparison
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("### 📊 Basic Agent (2 Datasets)")
            with st.container():
                st.write("**Datasets Used:**")
                st.write("✅ Player Profiles")
                st.write("✅ Lifetime Spend") 
                st.write("❌ Session History")
                st.write("❌ Player Attributes")
                
                st.write("**Analysis:**")
                with st.expander("View Basic Agent Response", expanded=True):
                    st.write(results['basic_response'])
        
        with col2:
            st.write("### 🔬 Enhanced Agent (4 Datasets)")
            with st.container():
                st.write("**Datasets Used:**")
                st.write("✅ Player Profiles")
                st.write("✅ Lifetime Spend")
                st.write("✅ Session History") 
                st.write("✅ Player Attributes")
                
                st.write("**Analysis:**")
                with st.expander("View Enhanced Agent Response", expanded=True):
                    st.write(results['enhanced_response'])
        
        # Business value section
        st.subheader("💼 Business Value Comparison")
        
        # ROI metrics
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.write("### 📊 Basic Agent")
            st.metric("Data Sources", "2", help="Player Profiles + Lifetime Spend")
            st.metric("Segment Depth", "Demographic", help="High/Low spenders, Engaged/Disengaged")
            st.metric("Analysis Type", "Static", help="Point-in-time snapshots")
            
        with col2:
            st.write("### 🔬 Enhanced Agent") 
            st.metric("Data Sources", "4", "+100%", help="Adds Session History + Player Attributes")
            st.metric("Segment Depth", "Behavioral", "Advanced", help="Weekend Warriors, Mission-focused, etc.")
            st.metric("Analysis Type", "Dynamic", "Temporal", help="Trend analysis and pattern recognition")
            
        with col3:
            st.write("### 📈 ROI Impact")
            st.success("**2x Data Sources**")
            st.info("**Richer Segmentation**")
            st.warning("**Better Targeting**")
        
        # Export option
        if st.button("📄 Export Comparison Report"):
            report_data = {
                'timestamp': results['timestamp'],
                'prompt': results['prompt'],
                'basic_analysis': results['basic_response'],
                'enhanced_analysis': results['enhanced_response']
            }
            
            st.json(report_data)
    
    else:
        # Instructions when no results
        st.info("👆 Select a prompt above and click 'Run Comparison Analysis' to see how different data sources enhance AI segmentation.")
def main():
    st.title("🎮 Player Segmentation Dashboard")
    
    # Load all data for visualizations (always use comprehensive data)
    df = load_comprehensive_data()
    
    if not df.empty:
        # AgentCore Configuration in sidebar
        st.sidebar.header("🔧 AgentCore Configuration")
        
        # Player Segmentation Agent Endpoint
        segmentation_endpoint = st.sidebar.text_input(
            "Player Segmentation Agent ARN", 
            value=os.environ.get('SEGMENTATION_AGENTCORE_ENDPOINT', ''),
            help="Enter the AgentCore runtime ARN for player segmentation agent (e.g., arn:aws:bedrock-agentcore:us-west-2:account:runtime/agent-id)",
            placeholder="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/player_segmentation_agent-abc123"
        )
        
        # Campaign Agent Endpoint
        campaign_endpoint = st.sidebar.text_input(
            "Campaign Agent ARN", 
            value=os.environ.get('CAMPAIGN_AGENTCORE_ENDPOINT', ''),
            help="Enter the AgentCore runtime ARN for campaign recommendation agent",
            placeholder="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/campaign_agent-xyz789"
        )
        
        # Configure Segmentation Agent
        if segmentation_endpoint:
            os.environ['SEGMENTATION_AGENTCORE_ENDPOINT'] = segmentation_endpoint
            try:
                if st.session_state.agentcore_client is None or st.session_state.agentcore_client.endpoint != segmentation_endpoint:
                    # Get current region
                    session = boto3.Session()
                    region = session.region_name or 'us-west-2'
                    st.session_state.agentcore_client = AgentCoreClient(segmentation_endpoint, region)
                
                # Health check using ping prompt
                try:
                    session = boto3.Session()
                    region = session.region_name or 'us-west-2'
                    agentcore_client = boto3.client('bedrock-agentcore', region_name=region)
                    
                    response = agentcore_client.invoke_agent_runtime(
                        agentRuntimeArn=segmentation_endpoint,
                        runtimeSessionId=f"health-{uuid.uuid4().hex}",
                        payload=json.dumps({"prompt": "ping"}),
                        qualifier="DEFAULT"
                    )
                    
                    st.sidebar.success("✅ Segmentation agent online")
                except Exception as health_error:
                    st.sidebar.error(f"❌ Agent offline: {str(health_error)[:50]}...")
                    
            except Exception as e:
                st.sidebar.error(f"❌ Segmentation agent configuration error: {str(e)}")
                st.session_state.agentcore_client = None
        else:
            st.sidebar.warning("⚠️ No segmentation agent endpoint configured")
            st.session_state.agentcore_client = None
            
        # Configure Campaign Agent
        if campaign_endpoint:
            os.environ['CAMPAIGN_AGENTCORE_ENDPOINT'] = campaign_endpoint
            try:
                if st.session_state.campaign_agentcore_client is None or st.session_state.campaign_agentcore_client.endpoint != campaign_endpoint:
                    # Get current region
                    session = boto3.Session()
                    region = session.region_name or 'us-west-2'
                    st.session_state.campaign_agentcore_client = AgentCoreClient(campaign_endpoint, region)
                
                st.sidebar.success("✅ Campaign agent configured")
            except Exception as e:
                st.sidebar.error(f"❌ Campaign agent configuration error: {str(e)}")
                st.session_state.campaign_agentcore_client = None
        else:
            st.sidebar.warning("⚠️ No campaign agent endpoint configured")
            st.session_state.campaign_agentcore_client = None
        
        # Main content with tabs
        tab1, tab2, tab3, tab4 = st.tabs([
            "📊 Data Dashboard", 
            "🔍 Segment Analysis", 
            "📋 Segment Management",
            "🎯 Campaign Recommender"
        ])
        
        with tab1:
            # Filters in main content area for Data Dashboard tab only
            st.subheader("🔧 Data Filters")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                # Platform filter
                platforms = ['All'] + list(df['platform'].unique())
                selected_platform = st.selectbox("Platform", platforms)
            
            with col2:
                # Faction filter
                if 'faction_allegiance' in df.columns:
                    factions = ['All'] + list(df['faction_allegiance'].unique())
                    selected_faction = st.selectbox("Faction", factions)
                else:
                    selected_faction = 'All'
            
            with col3:
                # Engagement range
                if 'engagement_intensity_score' in df.columns:
                    min_eng = int(df['engagement_intensity_score'].min())
                    max_eng = int(df['engagement_intensity_score'].max())
                    eng_min, eng_max = st.slider("Engagement Score Range", min_eng, max_eng, (min_eng, max_eng))
                else:
                    eng_min, eng_max = 0, 100
            
            # Apply filters
            filtered_df = df.copy()
            
            if selected_platform != 'All':
                filtered_df = filtered_df[filtered_df['platform'] == selected_platform]
            
            if selected_faction != 'All' and 'faction_allegiance' in df.columns:
                filtered_df = filtered_df[filtered_df['faction_allegiance'] == selected_faction]
            
            if 'engagement_intensity_score' in df.columns:
                filtered_df = filtered_df[
                    (filtered_df['engagement_intensity_score'] >= eng_min) & 
                    (filtered_df['engagement_intensity_score'] <= eng_max)
                ]
            
            show_data_overview(filtered_df)
        
        with tab2:
            show_ai_analysis(df)  # Use full dataset for AI analysis
        
        with tab3:
            show_segment_management()
        
        with tab4:
            show_campaign_recommender()
    
    else:
        st.warning("No data available. Please ensure data files are in the data directory.")

if __name__ == "__main__":
    main()
