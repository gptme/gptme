import argparse
from pathlib import Path
import glob
import os
from gptme.eval.agents.swebench import SWEBenchAgent
from gptme.eval.swe_extra.swe_bench_extra_data import load_instance_by_id, load_top_50_easiest_task_instances
from gptme.dirs import get_logs_dir
from gptme.logmanager import LogManager, SWEBenchInfo
from gptme.tools import init_tools

def get_most_recent_log_dir():
    """Get the most recent log directory from the gptme logs folder."""
    logs_dir = get_logs_dir()
    log_dirs = glob.glob(str(logs_dir / "*gptme-evals-*"))
    if not log_dirs:
        return None
    return Path(max(log_dirs, key=os.path.getctime))

def clear_branch(resume_dir: Path, branch: str) -> None:
    """
    Clear the branch before resume
    """
    manager = LogManager.load(resume_dir, lock=False, create=True)
    if branch not in manager._branches: return
    manager._branches[branch].messages.clear()
    manager.write()
    print(f"Cleared branch {branch}")

def main(model: str, resume_dir: str | Path | None = None, branch_to_clear: str | None = None, **kwargs):
    """
    Run SWE-bench evaluation.
    
    Args:
        resume_dir: Path to resume from. If 'auto', uses most recent run. If None, starts new run.
        clear_branch: If True, clears the last conversation branch
        **kwargs: Additional arguments passed to evaluate_instance
    """
    init_tools()
    agent = SWEBenchAgent()
    
    eval_kwargs = {"understand": {"max_turns": 25}}
    eval_kwargs.update(kwargs)
    
    if resume_dir:
        if resume_dir == 'auto':
            resume_dir = get_most_recent_log_dir()
            if not resume_dir:
                print("No previous runs found to resume")
                return
            print(f"Auto-resuming from most recent run: {resume_dir}")
        else:
            resume_dir = Path(resume_dir).expanduser()
        info = SWEBenchInfo.load_from_log_dir(resume_dir)
        if not info: raise ValueError(f"No info found in {resume_dir}")
        instance = load_instance_by_id(info.instance_id)
        
        if branch_to_clear:
            clear_branch(resume_dir, branch_to_clear)
                
        agent.evaluate_instance(instance, model=info.model_name, resume_dir=resume_dir, **eval_kwargs)
    else:
        instance = load_top_50_easiest_task_instances()[0]
        agent.evaluate_instance(instance, model=model, **eval_kwargs)

def cli():
    """Command-line interface entry point"""
    parser = argparse.ArgumentParser(description='Run SWE-bench evaluation')
    parser.add_argument('-r', '--resume', 
                       help='Resume from a previous run directory. If no directory specified, resumes most recent run.',
                       nargs='?',
                       const='auto',
                       type=str)
    parser.add_argument('--branch-to-clear',
                       help='Clear the branch before resuming',
                       type=str)
    args = parser.parse_args()
    main(resume_dir=args.resume, branch_to_clear=args.branch_to_clear)

if __name__ == "__main__":
    # main("auto", branch_to_clear="reproduce")
    # main(model="openrouter/qwen/qwen-2.5-coder-32b-instruct")
    main(model="anthropic/claude-3-5-sonnet-20240620")
