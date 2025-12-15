from typing import cast
from datasets import load_dataset
import pandas as pd
import matplotlib.pyplot as plt
from gptme.logmanager import Log, SWEBenchInfo, get_user_conversations
from swebench.harness.constants import SWEbenchInstance
from datasets import Dataset
import json
from pathlib import Path

def load_my_trajectories():
    """
    Load trajectories from your agent's runs.
    """
    trajectories = []
    
    # Load all logs with SWE-bench info
    for conv in get_user_conversations():
        log = Log.read_jsonl(conv.path)
        swe_bench_info = SWEBenchInfo.load_from_log_dir(conv.path)
        if swe_bench_info:
            trajectories.append({
                'instance_id': swe_bench_info.instance_id,
                'model_name': swe_bench_info.model_name,
                'target': int(swe_bench_info.target),  # Convert bool to int for analysis
                'trajectory': [m.to_dict() for m in log.messages],
                'exit_status': swe_bench_info.exit_status,
                'generated_patch': swe_bench_info.generated_patch,
                'eval_logs': swe_bench_info.eval_logs
            })
    
    return pd.DataFrame(trajectories) if trajectories else pd.DataFrame()

def load_nebius_trajectories():
    dataset = load_dataset("nebius/SWE-agent-trajectories", split="train")
    return dataset.to_pandas()

def load_swe_bench_extra_issues():
    dataset = load_dataset("nebius/SWE-bench-extra", split="train")
    return dataset.to_pandas()

def filter_lite_issues(issues_df):
    """
    Filter issues to only include those that are considered "lite" based on meta field criteria.
    
    A lite issue should:
    1. Have meta.is_lite = true, or
    2. Not have any failed lite validators in meta.failed_lite_validators
    
    Args:
        issues_df: DataFrame containing issues with a 'meta' column
        
    Returns:
        DataFrame containing only the lite issues
    """
    def is_lite_issue(meta):
        # If meta is explicitly marked as lite, return True
        return meta.get('is_lite', False)

    # Apply the filter to each row's meta field
    return issues_df[issues_df['meta'].apply(is_lite_issue)]

def calculate_issue_difficulty(issue_id, trajectories_df):
    matching_trajectories = trajectories_df[trajectories_df['instance_id'] == issue_id]
    
    if len(matching_trajectories) == 0:
        return {
            'num_attempts': 0,
            'success_rate': 0.0,
            'avg_steps': 0,
            'success_by_model': {}
        }
    
    num_attempts = len(matching_trajectories)
    success_rate = matching_trajectories['target'].mean() if 'target' in matching_trajectories.columns else 0
    avg_steps = matching_trajectories['trajectory'].apply(len).mean() if 'trajectory' in matching_trajectories.columns else 0
    
    # Calculate success rate per model
    success_by_model = {}
    if 'model_name' in matching_trajectories.columns:
        for model in matching_trajectories['model_name'].unique():
            model_trajectories = matching_trajectories[matching_trajectories['model_name'] == model]
            success_by_model[model] = {
                'attempts': len(model_trajectories),
                'success_rate': model_trajectories['target'].mean() if 'target' in model_trajectories.columns else 0,
                'avg_steps': model_trajectories['trajectory'].apply(len).mean() if 'trajectory' in model_trajectories.columns else 0
            }
    
    return {
        'num_attempts': num_attempts,
        'success_rate': success_rate,
        'avg_steps': avg_steps,
        'success_by_model': success_by_model
    }

def analyze_issue_difficulties(issues_df=None, trajectories_df=None, min_attempts=5):
    """
    Analyze the difficulties of issues based on trajectory data.
    """
    # Load datasets if not provided
    if issues_df is None:
        issues_df = load_swe_bench_extra_issues()
    if trajectories_df is None:
        trajectories_df = load_nebius_trajectories()
    
    # Create a copy of the DataFrame to avoid warnings
    issues_df = issues_df.copy()
    
    # Calculate difficulty metrics for each issue
    difficulties = issues_df['instance_id'].apply(
        lambda x: calculate_issue_difficulty(x, trajectories_df)
    ).tolist()
    
    # Add difficulty metrics to issues DataFrame using loc
    issues_df.loc[:, 'difficulty_num_attempts'] = [d['num_attempts'] for d in difficulties]
    issues_df.loc[:, 'difficulty_success_rate'] = [d['success_rate'] for d in difficulties]
    issues_df.loc[:, 'difficulty_avg_steps'] = [d['avg_steps'] for d in difficulties]
    issues_df.loc[:, 'success_by_model'] = [d['success_by_model'] for d in difficulties]
    
    # Filter for issues with minimum number of attempts
    qualified_issues = issues_df[issues_df['difficulty_num_attempts'] >= min_attempts].copy()
    
    return {
        'issues_with_metrics': issues_df,
        'hardest_by_success_rate': qualified_issues.nsmallest(3, 'difficulty_success_rate'),
        'hardest_by_avg_steps': qualified_issues.nlargest(3, 'difficulty_avg_steps'),
        'most_attempted': issues_df.nlargest(3, 'difficulty_num_attempts'),
        'easiest_by_success_rate': qualified_issues.nlargest(3, 'difficulty_success_rate'),
        'easiest_by_avg_steps': qualified_issues.nsmallest(3, 'difficulty_avg_steps')
    }

def plot_lite_50_success_rate(top_50_df):
    """
    Create a bar graph showing the average success rate for the top 50 easiest issues by model
    """
    plt.figure(figsize=(12, 6))
    
    # Get all unique models
    all_models = set()
    for _, row in top_50_df.iterrows():
        all_models.update(row['success_by_model'].keys())
    all_models = sorted(list(all_models))
    
    # Calculate average success rate per model
    model_success_rates = []
    model_names = []
    for model in all_models:
        rates = []
        for _, row in top_50_df.iterrows():
            if model in row['success_by_model']:
                rates.append(row['success_by_model'][model]['success_rate'])
        if rates:
            model_success_rates.append(sum(rates) / len(rates))
            model_names.append(f"{model}\n(n={len(rates)})")
    
    # Create bars
    x = range(len(model_names))
    bars = plt.bar(x, model_success_rates, color='skyblue')
    model_success_rates
    # Add value labels on top of bars
    for i, v in enumerate(model_success_rates):
        plt.text(i, v, f'{v:.1%}', ha='center', va='bottom')
    
    # Customize plot
    plt.title('Average Success Rate by Model for Top 50 Easiest Lite Issues')
    plt.ylabel('Success Rate')
    plt.ylim(0, 1.0)
    plt.xticks(x, model_names, rotation=45)
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    
    # Adjust layout to prevent label cutoff
    plt.tight_layout()
    
    return plt

def filter_top_50_easiest_issues(issues_df):
    lite_issues_df = filter_lite_issues(issues_df)
    
    # Analyze difficulties of lite issues
    results = analyze_issue_difficulties(issues_df=lite_issues_df, min_attempts=5)
    
    # Get top 50 easiest issues (high success rate, low average steps)
    qualified_issues = results['issues_with_metrics']
    qualified_issues = qualified_issues[qualified_issues['difficulty_num_attempts'] >= 1].copy()
    
    # Normalize success rate (higher is better) and avg_steps (lower is better)
    max_steps = qualified_issues['difficulty_avg_steps'].max()
    qualified_issues.loc[:, 'normalized_steps'] = 1 - (qualified_issues['difficulty_avg_steps'] / max_steps)
    
    # Combined score (50% success rate, 50% normalized steps)
    qualified_issues.loc[:, 'ease_score'] = (
        0.5 * qualified_issues['difficulty_success_rate'] + 
        0.5 * qualified_issues['normalized_steps']
    )
    
    # Get top 50 easiest issues
    top_50_easiest = qualified_issues.nlargest(50, 'ease_score')
    
    return top_50_easiest

def issues_to_task_instances(issues_df):
    dataset = Dataset.from_pandas(issues_df)
    return [cast(SWEbenchInstance, instance) for instance in dataset]

def issue_to_task_instance(issue_df: pd.DataFrame) -> SWEbenchInstance:
    return cast(SWEbenchInstance, issue_df.iloc[0])

def load_instance_by_id(instance_id: str) -> SWEbenchInstance:
    issues_df = load_swe_bench_extra_issues()
    return issue_to_task_instance(issues_df[issues_df['instance_id'] == instance_id])

def get_cache_path():
    # Store cache in the same directory as this file, using pickle extension
    return Path("top_50_cache.pkl")

def load_top_50_easiest_task_instances():
    cache_path = get_cache_path()
    
    # Try to load from cache first
    if cache_path.exists():
        try:
            # Use pandas pickle read instead of JSON
            cached_df = pd.read_pickle(cache_path)
            return issues_to_task_instances(cached_df)
        except Exception as e:
            print(f"Failed to load cache: {e}")
    
    # If cache doesn't exist or failed to load, compute from scratch
    issues_df = load_swe_bench_extra_issues()
    top_50_easiest = filter_top_50_easiest_issues(issues_df)
    
    # Save to cache using pandas pickle
    try:
        top_50_easiest.to_pickle(cache_path)
    except Exception as e:
        print(f"Failed to save cache: {e}")
    
    return issues_to_task_instances(top_50_easiest)

if __name__ == "__main__":
    # Configure pandas display settings
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.float_format', lambda x: '%.3f' % x)
    
    # Load and filter lite issues
    issues_df = load_swe_bench_extra_issues()
    top_50_easiest = filter_top_50_easiest_issues(issues_df)
    
    print("\nTop 50 easiest lite issues:")
    print(top_50_easiest[['instance_id', 'difficulty_num_attempts', 
                         'difficulty_success_rate', 'difficulty_avg_steps', 
                         'ease_score']].to_string())
    
    # Save the issue IDs for running your agent
    top_50_ids = top_50_easiest['instance_id'].tolist()
    print("\nTop 50 issue IDs to test:")
    for id in top_50_ids:
        print(id)
    
    # After running your agent, load both sets of trajectories
    nebius_trajectories = load_nebius_trajectories()
    my_trajectories = load_my_trajectories()
    
    # Combine trajectories
    all_trajectories = pd.concat([nebius_trajectories, my_trajectories])
    
    # Analyze with combined data
    results = analyze_issue_difficulties(
        issues_df=top_50_easiest, 
        trajectories_df=all_trajectories,
        min_attempts=1
    )
    
    # Plot comparison
    plt = plot_lite_50_success_rate(results['issues_with_metrics'])
    plt.savefig('lite_50_model_comparison.png', dpi=300, bbox_inches='tight')
    plt.close()