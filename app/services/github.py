import os
import tempfile
from git import Repo
import shutil
import asyncio
from typing import Dict, Any, List, Tuple, Optional
import uuid
import random
import logging

import random
import aiohttp
import json
from app.utils.config import get_llm_api_key

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.models.schemas import JobStatus
from app.utils.file_utils import read_file_content

# In-memory storage for job results
analysis_jobs: Dict[str, Dict[str, Any]] = {}

def retrieve_repository(repo_url) -> str:
    """
    Clone a GitHub repository to a temporary directory.
    Use shallow clone for better performance.
    
    Args:
        repo_url: The URL of the GitHub repository (string or Pydantic URL object)
        
    Returns:
        str: Path to the cloned repository.
    """
    repo_url_str = str(repo_url)
    temp_dir = tempfile.mkdtemp(prefix="github_critic_")
    
    try:
        # Use shallow clone (depth=1) to only get the latest commit
        Repo.clone_from(
            repo_url_str, 
            temp_dir,
            depth=1,  # Only get the most recent commit
            single_branch=True  # Only clone the default branch
        )
        logger.info(f"Cloned repository: {repo_url_str} to {temp_dir}")
        return temp_dir
    
    except Exception as e:
        # Clean up the temporary directory if cloning fails
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise Exception(f"Failed to clone repository: {str(e)}")

def traverse_repository(repo_path: str) -> dict:
    """
    Traverse all directories in a repository and collect file paths.
    
    Args:
        repo_path (str): Path to the repository.
        
    Returns:
        dict: Dictionary mapping file extensions to lists of file paths.
    """
    # Common extensions to look for
    code_extensions = {
        # Languages
        '.py': 'Python',
        '.js': 'JavaScript',
        '.jsx': 'React',
        '.ts': 'TypeScript',
        '.tsx': 'React TypeScript',
        '.java': 'Java',
        '.c': 'C',
        '.cpp': 'C++',
        '.cs': 'C#',
        '.go': 'Go',
        '.rb': 'Ruby',
        '.php': 'PHP',
        '.swift': 'Swift',
        '.kt': 'Kotlin',
        '.rs': 'Rust',
        
        # Web
        '.html': 'HTML',
        '.css': 'CSS',
        '.scss': 'SCSS',
        '.json': 'JSON',
        
        # Config
        '.yml': 'YAML',
        '.yaml': 'YAML',
        '.xml': 'XML',
        '.toml': 'TOML',
        '.ini': 'INI',
        
        # Other
        '.md': 'Markdown',
        '.sh': 'Shell',
        '.sql': 'SQL'
    }
    
    # Directories to ignore
    ignore_dirs = ['.git', 'node_modules', 'venv', '__pycache__', '.idea', '.vscode', 'build', 'dist']
    
    # Dictionary to store files by extension
    files_by_extension = {ext: [] for ext in code_extensions}
    files_by_extension['other'] = []  # For files with unrecognized extensions
    
    for root, dirs, files in os.walk(repo_path):
        # Skip ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, repo_path)
            
            # Get file extension
            _, ext = os.path.splitext(file)
            ext = ext.lower()  # Normalize extension
            
            # Add file to appropriate category
            if ext in files_by_extension:
                files_by_extension[ext].append(relative_path)
            else:
                files_by_extension['other'].append(relative_path)
    
    return files_by_extension

def get_directory_contents(repo_path: str, dir_path: str = "") -> dict:
    """
    Get contents of a specific directory in the repository.
    
    Args:
        repo_path (str): Base path to the repository
        dir_path (str): Relative path within the repository
        
    Returns:
        dict: Directory contents with files and subdirectories
    """
    full_path = os.path.join(repo_path, dir_path)
    
    if not os.path.exists(full_path) or not os.path.isdir(full_path):
        return {"error": "Directory not found"}
    
    # Get list of ignored directories
    ignore_dirs = ['.git', 'node_modules', 'venv', '__pycache__', '.idea', '.vscode', 'build', 'dist']
    
    # List contents
    contents = {
        "current_path": dir_path,
        "directories": [],
        "files": []
    }
    
    try:
        for item in sorted(os.listdir(full_path)):
            item_path = os.path.join(full_path, item)
            rel_path = os.path.join(dir_path, item) if dir_path else item
            
            # Skip ignored directories
            if os.path.isdir(item_path) and item in ignore_dirs:
                continue
                
            if os.path.isdir(item_path):
                # Count files in directory for informational purposes
                file_count = 0
                for _, _, files in os.walk(item_path):
                    file_count += len(files)
                
                contents["directories"].append({
                    "name": item,
                    "path": rel_path,
                    "file_count": file_count
                })
            else:
                file_size = os.path.getsize(item_path)
                _, ext = os.path.splitext(item)
                
                contents["files"].append({
                    "name": item,
                    "path": rel_path,
                    "size": file_size,
                    "extension": ext.lower()
                })
    
    except Exception as e:
        return {"error": f"Failed to read directory: {str(e)}"}
    
    return contents

def get_subdirectory_sizes(repo_path: str, parent_path: str = "") -> list:
    """
    Get information about the size of subdirectories.
    
    Args:
        repo_path (str): Base path to the repository
        parent_path (str): Relative path to the parent directory
        
    Returns:
        list: List of subdirectory information
    """
    full_path = os.path.join(repo_path, parent_path)
    
    if not os.path.exists(full_path) or not os.path.isdir(full_path):
        return []
    
    # Get list of ignored directories
    ignore_dirs = ['.git', 'node_modules', 'venv', '__pycache__', '.idea', '.vscode', 'build', 'dist']
    
    # Code file extensions
    code_extensions = ['.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.cpp', 
                      '.cs', '.go', '.rb', '.php', '.swift', '.kt', '.rs']
    
    results = []
    
    for item in sorted(os.listdir(full_path)):
        item_path = os.path.join(full_path, item)
        rel_path = os.path.join(parent_path, item) if parent_path else item
        
        # Skip ignored directories
        if os.path.isdir(item_path) and item in ignore_dirs:
            continue
            
        if os.path.isdir(item_path):
            # Count files and subdirectories
            total_files = 0
            code_files = 0
            subdirs = 0
            
            for root, dirs, files in os.walk(item_path):
                total_files += len(files)
                code_files += sum(1 for f in files if os.path.splitext(f)[1].lower() in code_extensions)
                subdirs += len([d for d in dirs if d not in ignore_dirs])
            
            results.append({
                "path": rel_path,
                "name": item,
                "total_files": total_files,
                "code_files": code_files,
                "subdirectories": subdirs
            })
    
    return results

def count_files_in_paths(repo_path: str, paths: List[str], extensions: Optional[List[str]] = None) -> Tuple[int, List[str]]:
    """
    Count files in selected paths with optional extension filtering.
    
    Args:
        repo_path (str): Base path to the repository
        paths (list): List of relative paths to count files in
        extensions (list, optional): List of file extensions to include
        
    Returns:
        tuple: (total_files, file_paths)
    """
    total_files = 0
    file_paths = []
    
    for path in paths:
        full_path = os.path.join(repo_path, path)
        
        if os.path.isfile(full_path):
            # It's a single file
            if extensions is None or os.path.splitext(full_path)[1].lower() in extensions:
                total_files += 1
                file_paths.append(path)
        
        elif os.path.isdir(full_path):
            # It's a directory - walk through it
            for root, _, files in os.walk(full_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, repo_path)
                    
                    if extensions is None or os.path.splitext(file)[1].lower() in extensions:
                        total_files += 1
                        file_paths.append(rel_path)
    
    return total_files, file_paths

def sample_files_from_directory(repo_path: str, directory_path: str, sample_size: int, extensions: Optional[List[str]] = None) -> List[str]:
    """
    Get a random sample of files from a directory.
    
    Args:
        repo_path (str): Base path to the repository
        directory_path (str): Relative path to the directory
        sample_size (int): Number of files to sample
        extensions (list, optional): List of file extensions to include
        
    Returns:
        list: List of sampled file paths
    """
    full_path = os.path.join(repo_path, directory_path)
    
    if not os.path.exists(full_path) or not os.path.isdir(full_path):
        return []
    
    # Get all matching files
    all_files = []
    
    for root, _, files in os.walk(full_path):
        for file in files:
            if extensions is None or os.path.splitext(file)[1].lower() in extensions:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_path)
                all_files.append(rel_path)
    
    # Sample files
    if len(all_files) <= sample_size:
        return all_files
    
    return random.sample(all_files, sample_size)

# Placeholder for now, would be replaced with actual LLM integration
async def analyze_code_file(file_path: str, file_content: str) -> List[str]:
    """
    Analyze a code file and generate basic critiques.
    This is a placeholder for LLM integration later.
    
    Args:
        file_path: Path to the file
        file_content: Content of the file
        
    Returns:
        list: List of critiques for the file
    """
    # Get file extension
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    
    # Basic critiques based on patterns
    critiques = []
    
    # Check file length
    lines = file_content.split('\n')
    if len(lines) > 500:
        critiques.append("This file has over 500 lines. Someone's been skipping the 'single responsibility' lectures, huh?")
    
    # Check for long lines
    long_lines = [i+1 for i, line in enumerate(lines) if len(line) > 100]
    if long_lines:
        critiques.append(f"Lines {', '.join(map(str, long_lines[:3]))}{'...' if len(long_lines) > 3 else ''} are longer than 100 chars. Horizontal scrolling: the preferred workout of developers who hate their colleagues.")
    
    # Language-specific checks
    if ext == '.py':
        # Python-specific checks
        if 'import *' in file_content:
            critiques.append("Using 'import *'? I see you like to live dangerously with namespace pollution. Bold move.")
        
        if 'except:' in file_content or 'except Exception:' in file_content:
            critiques.append("Naked except clause? Ah, the 'I have no idea what could go wrong, but I'll pretend everything's fine' approach.")
    
    elif ext in ['.js', '.jsx', '.ts', '.tsx']:
        # JavaScript/TypeScript checks
        if 'var ' in file_content:
            critiques.append("Still using 'var'? Welcome to 2015! Let me show you this cool new thing called 'let' and 'const'.")
        
        if 'console.log' in file_content:
            critiques.append("I see you've left some console.logs. The digital equivalent of leaving the price tag on your new hat.")
    
    # Generic checks
    if 'TODO' in file_content:
        critiques.append("Nice TODOs. Planning to finish them this decade, or...?")
    
    if 'FIXME' in file_content:
        critiques.append("I see FIXMEs. The 'I'll definitely come back to this later' that never happens.")
    
    # If we didn't find any specific issues, add a generic roast
    if not critiques:
        critiques.append("This code looks suspiciously adequate. Either you're good, or I'm not looking hard enough.")
    
    return critiques

async def process_repository_async(job_id: str, repo_url: str):
    """
    Process repository analysis in the background.
    
    Args:
        job_id: Unique identifier for this job
        repo_url: URL of the repository to analyze
    """
    try:
        # Update job status to cloning
        analysis_jobs[job_id] = {
            "status": JobStatus.CLONING,
            "message": "Cloning repository...",
            "job_id": job_id
        }
        
        # Perform the shallow clone
        repo_path = retrieve_repository(repo_url)
        
        # Update job status to analyzing
        analysis_jobs[job_id] = {
            "status": JobStatus.ANALYZING,
            "message": "Analyzing repository structure...",
            "job_id": job_id,
            "repo_path": repo_path  # Save the path for later
        }
        
        # Perform the analysis
        files_by_extension = traverse_repository(repo_path)
        total_files = sum(len(files) for files in files_by_extension.values())
        file_stats = {ext: len(files) for ext, files in files_by_extension.items() if files}
        
        # Store results
        analysis_jobs[job_id] = {
            "status": JobStatus.COMPLETED,
            "repo_url": repo_url,
            "repo_path": repo_path,  # Keep the path for later
            "total_files": total_files,
            "file_stats": file_stats,
            "message": "Repository analyzed successfully",
            "job_id": job_id
        }
        
    except Exception as e:
        # Update job status to failed
        analysis_jobs[job_id] = {
            "status": JobStatus.FAILED,
            "message": "Repository analysis failed",
            "error": str(e),
            "job_id": job_id
        }
        
        # Try to clean up the repo if it exists
        try:
            if 'repo_path' in locals() and os.path.exists(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
        except:
            pass  # Ignore cleanup errors

async def process_repository_structure(job_id: str, repo_url: str):
    """
    Process repository cloning and return structure without analysis.
    
    Args:
        job_id: Unique identifier for this job
        repo_url: URL of the repository to analyze
    """
    try:
        # Update job status to cloning
        analysis_jobs[job_id] = {
            "status": JobStatus.CLONING,
            "message": "Cloning repository...",
            "job_id": job_id
        }
        
        # Perform the shallow clone
        repo_path = retrieve_repository(repo_url)
        
        # Update job status to analyzing structure
        analysis_jobs[job_id] = {
            "status": JobStatus.ANALYZING,
            "message": "Analyzing repository structure...",
            "job_id": job_id,
            "repo_path": repo_path  # Save the path for later use
        }
        
        # Get the root directory contents
        structure = get_directory_contents(repo_path)
        
        # Store results
        analysis_jobs[job_id] = {
            "status": JobStatus.COMPLETED,
            "repo_url": repo_url,
            "repo_path": repo_path,  # Keep the path for later analysis
            "structure": structure,
            "message": "Repository structure retrieved successfully",
            "job_id": job_id
        }
        
    except Exception as e:
        # Update job status to failed
        analysis_jobs[job_id] = {
            "status": JobStatus.FAILED,
            "message": "Repository structure retrieval failed",
            "error": str(e),
            "job_id": job_id
        }
        
        # Try to clean up the repo if it exists
        try:
            if 'repo_path' in locals() and os.path.exists(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
        except:
            pass  # Ignore cleanup errors

async def analyze_selected_paths(job_id: str, paths: list):
    """
    Analyze selected paths from a previously cloned repository.
    
    Args:
        job_id: Job ID where the repo was already cloned
        paths: List of paths to analyze
    """
    try:
        # Get the job data to find the repository path
        job_data = analysis_jobs.get(job_id)
        if not job_data or "repo_path" not in job_data:
            raise Exception("Repository not found. Please get the structure first.")
        
        repo_path = job_data["repo_path"]
        
        # Update job status
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.ANALYZING,
            "message": "Analyzing selected paths...",
        }
        
        # Analyze each path
        results = {}
        
        for path in paths:
            full_path = os.path.join(repo_path, path)
            
            if os.path.isfile(full_path):
                # Analyze a single file
                content = read_file_content(full_path)
                critiques = await analyze_code_file(full_path, content)
                
                results[path] = {
                    "type": "file",
                    "critiques": critiques
                }
            
            elif os.path.isdir(full_path):
                # Analyze all files in a directory
                dir_results = {}
                
                for root, _, files in os.walk(full_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, repo_path)
                        
                        # Skip non-code files
                        _, ext = os.path.splitext(file)
                        if ext.lower() not in ['.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.cpp', 
                                              '.cs', '.go', '.rb', '.php', '.html', '.css']:
                            continue
                        
                        content = read_file_content(file_path)
                        critiques = await analyze_code_file(file_path, content)
                        
                        dir_results[rel_path] = {
                            "type": "file",
                            "critiques": critiques
                        }
                
                results[path] = {
                    "type": "directory",
                    "files": dir_results
                }
            
            else:
                results[path] = {
                    "type": "error",
                    "message": "Path not found"
                }
        
        # Update job with results
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.COMPLETED,
            "message": "Analysis completed",
            "analysis_results": results
        }
        
    except Exception as e:
        # Update job status to failed
        if job_id in analysis_jobs:
            job_data = analysis_jobs[job_id]
            analysis_jobs[job_id] = {
                **job_data,
                "status": JobStatus.FAILED,
                "message": "Analysis failed",
                "error": str(e)
            }
        else:
            analysis_jobs[job_id] = {
                "status": JobStatus.FAILED,
                "message": "Analysis failed",
                "error": str(e),
                "job_id": job_id
            }

async def analyze_selected_paths_in_batches(job_id: str, batch_size: int = 20):
    """
    Process repository analysis in batches to handle large directories.
    
    Args:
        job_id: Job ID where the repo was already cloned
        batch_size: Number of files to process in each batch
    """
    try:
        # Get the job data
        job_data = analysis_jobs.get(job_id)
        if not job_data or "repo_path" not in job_data or "file_paths" not in job_data:
            raise Exception("Repository not found or no files selected.")
        
        repo_path = job_data["repo_path"]
        file_paths = job_data["file_paths"]
        
        # Calculate total batches
        total_files = len(file_paths)
        total_batches = (total_files + batch_size - 1) // batch_size  # Ceiling division
        
        # Update job status
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.ANALYZING,
            "message": f"Processing {total_files} files in {total_batches} batches...",
            "total_batches": total_batches,
            "completed_batches": 0,
            "analysis_results": {}
        }
        
        # Process files in batches
        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, total_files)
            batch = file_paths[start_idx:end_idx]
            
            # Update status
            job_data = analysis_jobs[job_id]  # Get fresh job data
            analysis_jobs[job_id] = {
                **job_data,
                "message": f"Processing batch {batch_idx + 1}/{total_batches} ({start_idx + 1}-{end_idx} of {total_files} files)..."
            }
            
            # Process batch
            batch_results = {}
            for file_path in batch:
                full_path = os.path.join(repo_path, file_path)
                
                if os.path.isfile(full_path):
                    content = read_file_content(full_path)
                    critiques = await analyze_code_file(full_path, content)
                    
                    batch_results[file_path] = {
                        "type": "file",
                        "critiques": critiques
                    }
            
            # Update results
            job_data = analysis_jobs[job_id]  # Get fresh job data
            analysis_jobs[job_id] = {
                **job_data,
                "completed_batches": batch_idx + 1,
                "analysis_results": {**job_data.get("analysis_results", {}), **batch_results}
            }
        
        # Mark job as completed
        job_data = analysis_jobs[job_id]
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.COMPLETED,
            "message": f"Analysis completed. Processed {total_files} files in {total_batches} batches."
        }
        
    except Exception as e:
        # Update job status to failed
        if job_id in analysis_jobs:
            job_data = analysis_jobs[job_id]
            analysis_jobs[job_id] = {
                **job_data,
                "status": JobStatus.FAILED,
                "message": "Analysis failed",
                "error": str(e)
            }

async def select_files_for_roasting(repo_path: str, job_id: str, api_key: str) -> list:
    """
    Intelligently select files from a repository for roasting by an LLM.
    
    Args:
        repo_path (str): Path to the cloned repository
        job_id (str): Current job ID
        api_key (str): API key for the LLM service
        
    Returns:
        List[Tuple[str, str]]: List of (file_path, roast) tuples
    """
    # Code file extensions to consider for roasting
    code_extensions = [
        '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.cpp', 
        '.cs', '.go', '.rb', '.php', '.swift', '.kt', '.rs'
    ]
    
    # Collect code files
    code_files = []
    for root, _, files in os.walk(repo_path):
        for file in files:
            _, ext = os.path.splitext(file)
            if ext.lower() in code_extensions:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_path)
                
                # Get file size and line count for better selection
                try:
                    size = os.path.getsize(file_path)
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        line_count = len(content.split('\n'))
                        
                    # We want files that are:
                    # 1. Not too small (trivial)
                    # 2. Not too large (would exceed token limits)
                    # 3. Have some complexity (more lines)
                    if 500 <= size <= 15000 and 50 <= line_count <= 500:
                        code_files.append((rel_path, content, line_count, size))
                except Exception as e:
                    logger.warning(f"Error reading file {rel_path}: {str(e)}")
    
    # If we don't have enough qualified files, relax the constraints
    if len(code_files) < 5:
        # Collect any code files without strict size/line requirements
        code_files = []
        for root, _, files in os.walk(repo_path):
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in code_extensions:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, repo_path)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            line_count = len(content.split('\n'))
                        
                        # More permissive constraints
                        if line_count <= 1000:  # Just ensure it's not massive
                            code_files.append((rel_path, content, line_count, os.path.getsize(file_path)))
                    except Exception:
                        pass
    
    # Sort by a score that favors:
    # - Medium-sized files (not too small, not too large)
    # - Files with a good amount of lines (heuristic for complexity)
    def complexity_score(item):
        _, _, lines, size = item
        # Ideal file is around 200 lines and 5000 bytes
        lines_score = abs(lines - 200)
        size_score = abs(size - 5000)
        return lines_score + size_score/100
    
    # Sort by our complexity score (lower is better)
    code_files.sort(key=complexity_score)
    
    # Take top 10 candidates
    candidates = code_files[:10]
    
    # Randomly select 2 files from candidates
    # This adds some randomness while still favoring "interesting" files
    selected = random.sample(candidates, min(2, len(candidates)))
    
    # Process each selected file
    results = []
    for file_path, content, _, _ in selected:
        # Call LLM to roast the code
        roast = await roast_code_with_llm(content, file_path, api_key)
        results.append((file_path, roast))
    
    return results

async def roast_code_with_llm(code_content: str, file_path: str, api_key: str, style: str = "brutal", description: str = None) -> str:
    """
    Send code to LLM for roasting with a specified style.
    
    Args:
        code_content: The content of the code file
        file_path: Path to the file (for context)
        api_key: API key for the LLM service
        style: The style of critique (brutal, constructive, educational, etc.)
        description: Optional custom description of what to focus on
        
    Returns:
        str: The LLM's critique of the code
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    url = "https://api.edenai.run/v2/multimodal/chat"
    
    # Extract file name and extension for context
    file_name = os.path.basename(file_path)
    _, ext = os.path.splitext(file_name)
    
    # Create style-specific prompts
    style_prompts = {
        "brutal": "You are a github code critic with a brutally honest and humorous style. Roast this code mercilessly, pointing out bad practices, inefficiencies, and stylistic issues in a funny way.",
        
        "constructive": "You are a senior developer reviewing code. Provide constructive criticism that highlights issues but also suggests specific improvements. Be direct but professional, and explain why certain patterns are problematic.",
        
        "educational": "You are a programming instructor reviewing student code. Explain what's wrong with the code in an educational way, referencing best practices and design patterns. Focus on teaching, not criticizing.",
        
        "funny": "You are a stand-up comedian who happens to be a brilliant programmer. Create a humorous critique of this code that's genuinely funny but also technically accurate. Use analogies, metaphors and exaggeration for comic effect.",
        
        "security": "You are a cybersecurity expert focused on code security. Analyze this code for security vulnerabilities, potential exploits, and security best practices violations. Suggest specific security improvements."
    }
    
    # Get the appropriate prompt for the requested style
    style_prompt = style_prompts.get(style.lower(), style_prompts["brutal"])
    
    # Add custom description if provided
    if description:
        style_prompt += f" Focus particularly on {description}."
    
    # Create the full prompt
    prompt = f"{style_prompt} This code is from the file '{file_name}' in a repository:\n\n{code_content}"
    
    body = {
        "providers": ["anthropic/claude-3-7-sonnet-20250219"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "content": {
                            "text": prompt
                        }
                    }
                ]
            }
        ]
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    # Log the full response to debug
                    logger.info(f"LLM API Response: {json.dumps(result, indent=2)}")
                    
                    # Try different ways to extract the response based on EdenAI format
                    if "anthropic/claude-3-7-sonnet-20250219" in result:
                        provider_result = result["anthropic/claude-3-7-sonnet-20250219"]
                        
                        # Check different possible response formats
                        if "message" in provider_result:
                            message = provider_result["message"]
                            
                            if isinstance(message, dict) and "content" in message:
                                if isinstance(message["content"], str):
                                    return message["content"]
                                elif isinstance(message["content"], list):
                                    # Concatenate all text content
                                    return " ".join([
                                        item.get("text", "") 
                                        for item in message["content"]
                                        if item.get("type") == "text"
                                    ])
                            
                        # Try alternative paths
                        if "generated_text" in provider_result:
                            return provider_result["generated_text"]
                        
                        if "response" in provider_result:
                            return provider_result["response"]
                    
                    # If we got here, we couldn't parse the response in expected ways
                    # Return the full result as a string to help with debugging
                    return f"API returned an unexpected format. Raw response: {json.dumps(result)}"
                else:
                    response_text = await response.text()
                    logger.error(f"API error: {response.status} - {response_text}")
                    return f"API error: {response.status} - {response_text}"
    except Exception as e:
        logger.exception(f"Error sending code to LLM: {str(e)}")
        return f"Error sending code to LLM: {str(e)}"
    

async def generate_improvement_suggestions(file_path: str, code_content: str, api_key: str, mode: str = "basic") -> str:
    """
    Generate improvement suggestions for a code file.
    
    Args:
        file_path: Path to the file
        code_content: Content of the code file
        api_key: API key for the LLM service
        mode: Level of detail for suggestions (basic or detailed)
        
    Returns:
        str: Improvement suggestions
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    url = "https://api.edenai.run/v2/multimodal/chat"
    
    # Extract file name and extension for context
    file_name = os.path.basename(file_path)
    _, ext = os.path.splitext(file_name)
    
    # Different prompts based on the mode
    if mode == "detailed":
        prompt = f"""Analyze this code and provide detailed improvements to make it better. Your response should include:

1. Specific code changes with before/after examples
2. Detailed explanations of why each change improves the code
3. Higher-level architectural or design pattern recommendations if applicable
4. Performance optimization suggestions
5. Security improvements if applicable

This is the code from file '{file_name}':

{code_content}

Format your response with clear sections and code examples."""

    else:  # Basic mode
        prompt = f"""Analyze this code and suggest practical improvements. Focus on the most important issues. Your response should include:

1. The top 3-5 most important improvements
2. Brief explanation of why each change would help
3. Simple examples where helpful

This is the code from file '{file_name}':

{code_content}

Keep your suggestions concise and actionable."""
    
    body = {
        "providers": ["anthropic/claude-3-7-sonnet-20250219"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "content": {
                            "text": prompt
                        }
                    }
                ]
            }
        ]
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    # Extract the suggestions from the response
                    if "anthropic/claude-3-7-sonnet-20250219" in result:
                        provider_result = result["anthropic/claude-3-7-sonnet-20250219"]
                        
                        if "message" in provider_result:
                            message = provider_result["message"]
                            
                            if isinstance(message, dict) and "content" in message:
                                if isinstance(message["content"], str):
                                    return message["content"]
                                elif isinstance(message["content"], list):
                                    # Concatenate all text content
                                    return " ".join([
                                        item.get("text", "") 
                                        for item in message["content"]
                                        if item.get("type") == "text"
                                    ])
                        
                        if "generated_text" in provider_result:
                            return provider_result["generated_text"]
                        
                        if "response" in provider_result:
                            return provider_result["response"]
                    
                    return "Failed to generate suggestions from API response."
                else:
                    return f"API error: {response.status} - {await response.text()}"
    except Exception as e:
        logger.exception(f"Error generating suggestions: {str(e)}")
        return f"Error generating suggestions: {str(e)}"
    
async def generate_repo_summary(file_critiques: list, api_key: str) -> str:
    """
    Generate a summary of common issues across multiple files.
    
    Args:
        file_critiques: List of (file_path, critique) tuples
        api_key: API key for the LLM service
        
    Returns:
        str: Summary of common issues
    """
    if not file_critiques:
        return "No files were analyzed."
    
    headers = {"Authorization": f"Bearer {api_key}"}
    url = "https://api.edenai.run/v2/multimodal/chat"
    
    # Prepare a summary of critiques for each file
    critique_summaries = []
    for file_path, critique in file_critiques:
        file_name = os.path.basename(file_path)
        critique_summaries.append(f"File: {file_name}\nCritique: {critique[:500]}...")
    
    critique_text = "\n\n".join(critique_summaries)
    
    prompt = f"""Based on the following code critiques, identify common issues, patterns, and anti-patterns across the files. 
Summarize the key problems that appear in multiple files or represent significant concerns.
Focus on actionable insights that would help improve the overall codebase.

Critiques:
{critique_text}

Please provide:
1. A summary of the most important common issues
2. Suggestions for codebase-wide improvements
3. Any positive patterns you noticed that should be maintained"""
    
    body = {
        "providers": ["anthropic/claude-3-7-sonnet-20250219"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "content": {
                            "text": prompt
                        }
                    }
                ]
            }
        ]
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    # Extract the summary from the response
                    if "anthropic/claude-3-7-sonnet-20250219" in result:
                        provider_result = result["anthropic/claude-3-7-sonnet-20250219"]
                        
                        if "message" in provider_result:
                            message = provider_result["message"]
                            
                            if isinstance(message, dict) and "content" in message:
                                if isinstance(message["content"], str):
                                    return message["content"]
                                elif isinstance(message["content"], list):
                                    # Concatenate all text content
                                    return " ".join([
                                        item.get("text", "") 
                                        for item in message["content"]
                                        if item.get("type") == "text"
                                    ])
                        
                        if "generated_text" in provider_result:
                            return provider_result["generated_text"]
                        
                        if "response" in provider_result:
                            return provider_result["response"]
                    
                    return "Failed to generate summary from API response."
                else:
                    return f"API error: {response.status} - {await response.text()}"
    except Exception as e:
        logger.exception(f"Error generating summary: {str(e)}")
        return f"Error generating summary: {str(e)}"
    

async def select_files_for_roasting(
    repo_path: str, 
    job_id: str, 
    api_key: str, 
    style: str = "brutal", 
    description: str = None,
    extensions: list = None,
    directories: list = None,
    file_count: int = 2,
    suggestions_mode: str = "none"
) -> list:
    """
    Intelligently select files from a repository for roasting by an LLM.
    
    Args:
        repo_path: Path to the cloned repository
        job_id: Current job ID
        api_key: API key for the LLM service
        style: Style of critique to perform
        description: Custom focus for the critique
        extensions: List of file extensions to filter by (e.g., ['.py', '.js'])
        directories: List of directories to search in
        file_count: Number of files to select
        suggestions_mode: Whether to generate improvement suggestions and what level
        
    Returns:
        List[Tuple[str, str, str]]: List of (file_path, roast, suggestions) tuples
    """
    # Default code file extensions if none specified
    if not extensions:
        code_extensions = [
            '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.cpp', 
            '.cs', '.go', '.rb', '.php', '.swift', '.kt', '.rs'
        ]
    else:
        # Make sure extensions start with a dot
        code_extensions = [ext if ext.startswith('.') else f'.{ext}' for ext in extensions]
    
    # Collect code files
    code_files = []
    
    # Function to check if a path is within specified directories
    def is_in_specified_dirs(path):
        if not directories:
            return True
        return any(path.startswith(d) for d in directories)
    
    for root, _, files in os.walk(repo_path):
        rel_root = os.path.relpath(root, repo_path)
        
        # Skip if not in specified directories (if directories are provided)
        if directories and not is_in_specified_dirs(rel_root):
            continue
            
        for file in files:
            _, ext = os.path.splitext(file)
            if ext.lower() in code_extensions:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_path)
                
                # Get file size and line count for better selection
                try:
                    size = os.path.getsize(file_path)
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        line_count = len(content.split('\n'))
                        
                    # We want files that are:
                    # 1. Not too small (trivial)
                    # 2. Not too large (would exceed token limits)
                    # 3. Have some complexity (more lines)
                    if 500 <= size <= 15000 and 50 <= line_count <= 500:
                        code_files.append((rel_path, content, line_count, size))
                except Exception as e:
                    logger.warning(f"Error reading file {rel_path}: {str(e)}")
    
    # If we don't have enough qualified files, relax the constraints
    if len(code_files) < 5:
        # Collect any code files without strict size/line requirements
        code_files = []
        for root, _, files in os.walk(repo_path):
            rel_root = os.path.relpath(root, repo_path)
            
            # Skip if not in specified directories
            if directories and not is_in_specified_dirs(rel_root):
                continue
                
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in code_extensions:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, repo_path)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            line_count = len(content.split('\n'))
                        
                        # More permissive constraints
                        if line_count <= 1000:  # Just ensure it's not massive
                            code_files.append((rel_path, content, line_count, os.path.getsize(file_path)))
                    except Exception:
                        pass
    
    # Sort by a score that favors:
    # - Medium-sized files (not too small, not too large)
    # - Files with a good amount of lines (heuristic for complexity)
    def complexity_score(item):
        _, _, lines, size = item
        # Ideal file is around 200 lines and 5000 bytes
        lines_score = abs(lines - 200)
        size_score = abs(size - 5000)
        return lines_score + size_score/100
    
    # Sort by our complexity score (lower is better)
    code_files.sort(key=complexity_score)
    
    # Take top candidates based on requested file count
    # Add some buffer for selection
    candidates = code_files[:min(file_count*3, len(code_files))]
    
    # Randomly select requested number of files from candidates
    # This adds some randomness while still favoring "interesting" files
    selected = random.sample(candidates, min(file_count, len(candidates)))
    
    # Process each selected file
    results = []
    for file_path, content, _, _ in selected:
        # Call LLM to roast the code with the specified style
        roast = await roast_code_with_llm(content, file_path, api_key, style, description)
        
        # Generate improvement suggestions if requested
        suggestions = None
        if suggestions_mode != "none":
            suggestions = await generate_improvement_suggestions(file_path, content, api_key, suggestions_mode)
        
        # If suggestions were requested, add them to the results
        if suggestions:
            results.append((file_path, roast, suggestions))
        else:
            results.append((file_path, roast))
    
    return results