from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import HttpUrl
import asyncio
import uuid

from app.models.schemas import (
    RepositoryRequest, RepositoryResponse, 
    AnalysisJob, AnalysisResponse, JobStatus,
    DirectoryRequest, DirectoryContents,
    SelectionRequest, SelectionResponse,
    SamplingRequest, SamplingResponse,
    BatchProcessRequest, BatchProcessResponse,
    ProgressResponse, JobIdRequest,
    DirectorySizeResponse, AutoRoastRequest,
    CritiqueStyle, SuggestionMode
)

from app.services.github import (
    retrieve_repository, traverse_repository, 
    analysis_jobs, process_repository_async,
    process_repository_structure, get_directory_contents,
    count_files_in_paths, analyze_selected_paths,
    sample_files_from_directory, get_subdirectory_sizes,
    analyze_selected_paths_in_batches, select_files_for_roasting,
    generate_repo_summary
)

from app.utils.file_utils import cleanup_directory
from app.utils.config import get_llm_api_key

router = APIRouter(
    prefix="/repositories",
    tags=["repositories"]
)

# Original synchronous endpoint
@router.post("/analyze", response_model=RepositoryResponse)
async def analyze_repository(repo_request: RepositoryRequest, background_tasks: BackgroundTasks):
    """
    Analyze a GitHub repository and return stats about its files.
    The repository will be cloned to a temporary directory and analyzed.
    """
    try:
        # Convert URL to string to avoid Pydantic URL object issues
        repo_url_str = str(repo_request.repo_url)
        
        # Retrieve the repository
        repo_path = retrieve_repository(repo_url_str)
        
        # Add cleanup task to run after response is sent
        background_tasks.add_task(cleanup_directory, repo_path)
        
        # Traverse and analyze the repository
        files_by_extension = traverse_repository(repo_path)
        
        # Count total files
        total_files = sum(len(files) for files in files_by_extension.values())
        
        # Prepare the response
        file_stats = {
            ext: len(files) for ext, files in files_by_extension.items() if files
        }
        
        return RepositoryResponse(
            repo_url=repo_url_str,
            total_files=total_files,
            file_stats=file_stats,
            message="Repository analyzed successfully"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze repository: {str(e)}")

# Asynchronous repository analysis
@router.post("/analyze/async", response_model=AnalysisJob)
async def analyze_repository_async(repo_request: RepositoryRequest):
    """
    Start asynchronous analysis of a GitHub repository.
    Returns a job ID that can be used to check the status.
    """
    try:
        # Generate a unique job ID
        job_id = str(uuid.uuid4())
        
        # Store initial job status
        analysis_jobs[job_id] = {
            "status": JobStatus.PENDING,
            "message": "Analysis started",
            "job_id": job_id
        }
        
        # Start background task
        asyncio.create_task(process_repository_async(
            job_id=job_id, 
            repo_url=str(repo_request.repo_url)
        ))
        
        return AnalysisJob(job_id=job_id)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start analysis: {str(e)}")

@router.get("/analyze/status/{job_id}", response_model=RepositoryResponse)
async def get_analysis_status(job_id: str):
    """
    Get the status of an asynchronous repository analysis job.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if job_data.get("status") != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400, 
            detail=f"Job not completed yet. Current status: {job_data.get('status')}"
        )
    
    return RepositoryResponse(
        repo_url=job_data.get("repo_url", ""),
        total_files=job_data.get("total_files", 0),
        file_stats=job_data.get("file_stats", {}),
        message=job_data.get("message", "")
    )

# Directory structure endpoints
@router.post("/structure", response_model=AnalysisJob)
async def get_repository_structure(repo_request: RepositoryRequest):
    """
    Get the directory structure of a GitHub repository.
    Returns a job ID that can be used to check the status.
    """
    try:
        # Generate a unique job ID
        job_id = str(uuid.uuid4())
        
        # Store initial job status
        analysis_jobs[job_id] = {
            "status": JobStatus.PENDING,
            "message": "Structure retrieval started",
            "job_id": job_id
        }
        
        # Start background task
        asyncio.create_task(process_repository_structure(
            job_id=job_id, 
            repo_url=str(repo_request.repo_url)
        ))
        
        return AnalysisJob(job_id=job_id)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start structure retrieval: {str(e)}")

@router.post("/explore", response_model=DirectoryContents)
async def explore_directory(request: DirectoryRequest):
    """
    Explore a specific directory in the repository.
    """
    job_id = request.job_id
    
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "repo_path" not in job_data:
        raise HTTPException(
            status_code=400, 
            detail="Repository not cloned or structure not retrieved"
        )
    
    repo_path = job_data["repo_path"]
    
    # Get directory contents
    contents = get_directory_contents(repo_path, request.path)
    
    if "error" in contents:
        raise HTTPException(status_code=400, detail=contents["error"])
    
    return contents

@router.post("/directory-sizes", response_model=DirectorySizeResponse)
async def get_directory_sizes(request: DirectoryRequest):
    """
    Get information about the size of subdirectories.
    Useful for deciding which directories to select for analysis.
    """
    job_id = request.job_id
    
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "repo_path" not in job_data:
        raise HTTPException(status_code=400, detail="Repository not cloned")
    
    repo_path = job_data["repo_path"]
    
    # Get subdirectory sizes
    directories = get_subdirectory_sizes(repo_path, request.path)
    
    return DirectorySizeResponse(
        directories=directories,
        total_count=len(directories)
    )

# Selection endpoints
@router.post("/select", response_model=SelectionResponse)
async def select_paths(request: SelectionRequest):
    """
    Select paths for analysis and get information about the selection.
    """
    job_id = request.job_id
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    if "repo_path" not in job_data:
        raise HTTPException(status_code=400, detail="Repository not cloned")
    
    repo_path = job_data["repo_path"]
    
    # Count files in selected paths
    code_extensions = ['.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.cpp', 
                      '.cs', '.go', '.rb', '.php', '.swift', '.kt', '.rs']
    
    total_files, file_paths = count_files_in_paths(repo_path, request.paths, code_extensions)
    
    # Define limits
    WARNING_THRESHOLD = 50
    MAX_FILES_LIMIT = 200
    
    # Check if selection exceeds the maximum limit
    if total_files > MAX_FILES_LIMIT:
        return SelectionResponse(
            job_id=job_id,
            selected_paths=request.paths,
            total_files=total_files,
            message=f"⚠️ Your selection contains {total_files} files, which exceeds the maximum limit of {MAX_FILES_LIMIT}. Please select fewer files or specific subdirectories."
        )
    
    # Prepare warning message if above threshold
    message = f"Selected {len(request.paths)} paths containing {total_files} code files"
    if total_files > WARNING_THRESHOLD:
        message = f"⚠️ Warning: Your selection contains {total_files} files. Analysis may take some time. Consider selecting specific subdirectories instead."
    
    # Store the selection in the job data
    analysis_jobs[job_id] = {
        **job_data,
        "selected_paths": request.paths,
        "file_paths": file_paths
    }
    
    return SelectionResponse(
        job_id=job_id,
        selected_paths=request.paths,
        total_files=total_files,
        message=message
    )

@router.post("/sample", response_model=SamplingResponse)
async def sample_directory(request: SamplingRequest):
    """
    Get a random sample of files from a large directory.
    """
    job_id = request.job_id
    
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "repo_path" not in job_data:
        raise HTTPException(status_code=400, detail="Repository not cloned")
    
    repo_path = job_data["repo_path"]
    
    # Sample files from the directory
    code_extensions = ['.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.cpp', 
                      '.cs', '.go', '.rb', '.php', '.swift', '.kt', '.rs']
    
    sampled_files = sample_files_from_directory(
        repo_path, 
        request.path, 
        request.sample_size,
        code_extensions
    )
    
    # Store the sampled files for analysis
    analysis_jobs[job_id] = {
        **job_data,
        "selected_paths": sampled_files,
        "file_paths": sampled_files
    }
    
    return SamplingResponse(
        job_id=job_id,
        path=request.path,
        sampled_files=sampled_files,
        sample_size=len(sampled_files),
        message=f"Sampled {len(sampled_files)} files from {request.path}"
    )

# Analysis endpoints
@router.post("/analyze/paths", response_model=AnalysisJob)
async def analyze_selected_paths_endpoint(job_id_request: JobIdRequest):
    """
    Analyze previously selected paths from a repository.
    """
    job_id = job_id_request.job_id
    
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "repo_path" not in job_data:
        raise HTTPException(
            status_code=400, 
            detail="Repository not cloned or structure not retrieved"
        )
    
    if "selected_paths" not in job_data:
        raise HTTPException(
            status_code=400, 
            detail="No paths selected for analysis. Use /select endpoint first."
        )
    
    try:
        # Update job status
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.PENDING,
            "message": "Analysis queued"
        }
        
        # Start background task
        asyncio.create_task(analyze_selected_paths(
            job_id=job_id,
            paths=job_data["selected_paths"]
        ))
        
        return AnalysisJob(job_id=job_id)
        
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to start analysis: {str(e)}"
        )

@router.post("/analyze/batch", response_model=BatchProcessResponse)
async def analyze_in_batches(request: BatchProcessRequest):
    """
    Analyze selected paths in batches.
    Useful for large directories.
    """
    job_id = request.job_id
    
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "file_paths" not in job_data:
        raise HTTPException(
            status_code=400, 
            detail="No files selected for analysis. Use /select endpoint first."
        )
    
    file_paths = job_data["file_paths"]
    total_files = len(file_paths)
    total_batches = (total_files + request.batch_size - 1) // request.batch_size
    
    # Start batch processing
    asyncio.create_task(analyze_selected_paths_in_batches(
        job_id=job_id,
        batch_size=request.batch_size
    ))
    
    return BatchProcessResponse(
        job_id=job_id,
        total_batches=total_batches,
        total_files=total_files,
        message=f"Started processing {total_files} files in {total_batches} batches"
    )

@router.get("/analyze/paths/{job_id}", response_model=AnalysisResponse)
async def get_path_analysis_results(job_id: str):
    """
    Get the results of a path analysis job.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    return AnalysisResponse(
        job_id=job_id,
        status=job_data.get("status", JobStatus.FAILED),
        message=job_data.get("message", ""),
        analysis_results=job_data.get("analysis_results"),
        error=job_data.get("error")
    )

@router.get("/structure/{job_id}", response_model=dict)
async def get_structure_status(job_id: str):
    """
    Get the status of a repository structure retrieval job.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    return {
        "job_id": job_id,
        "status": job_data.get("status", JobStatus.FAILED),
        "repo_url": job_data.get("repo_url"),
        "structure": job_data.get("structure"),
        "message": job_data.get("message", ""),
        "error": job_data.get("error")
    }

@router.get("/analyze/progress/{job_id}", response_model=ProgressResponse)
async def get_analysis_progress(job_id: str):
    """
    Get detailed progress of an analysis job.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    total_files = len(job_data.get("file_paths", []))
    total_batches = job_data.get("total_batches", 0)
    completed_batches = job_data.get("completed_batches", 0)
    
    # Calculate progress percentage
    progress_percentage = None
    if total_batches > 0:
        progress_percentage = (completed_batches / total_batches) * 100
    
    return ProgressResponse(
        job_id=job_id,
        status=job_data.get("status", JobStatus.PENDING),
        message=job_data.get("message", ""),
        total_files=total_files,
        completed_files=len(job_data.get("analysis_results", {})),
        total_batches=total_batches,
        completed_batches=completed_batches,
        progress_percentage=progress_percentage,
        error=job_data.get("error")
    )


@router.post("/auto-roast", response_model=dict)
async def auto_roast_repository(roast_request: AutoRoastRequest):
    """
    Automatically select files from a repository and critique them.
    
    Args:
        roast_request: Request with job ID and critique parameters
    """
    job_id = roast_request.job_id
    
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "repo_path" not in job_data:
        raise HTTPException(
            status_code=400, 
            detail="Repository not cloned or structure not retrieved"
        )
    
    try:
        # Update job status
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.ANALYZING,
            "message": f"Selecting files for {roast_request.style} critique...",
        }
        
        # Get the repository path
        repo_path = job_data["repo_path"]
        
        # Get API key from environment
        api_key = get_llm_api_key()
        
        # Select files and get critiques with the specified parameters
        roasted_files = await select_files_for_roasting(
            repo_path, 
            job_id, 
            api_key,
            style=roast_request.style,
            description=roast_request.description,
            extensions=roast_request.extensions,
            directories=roast_request.directories,
            file_count=roast_request.file_count,
            suggestions_mode=roast_request.suggestions
        )
        
        # Generate summary of common issues if we have more than one file
        summary = None
        if len(roasted_files) > 1:
            # Extract just the file paths and critiques (not suggestions) for summary
            critique_pairs = [(file_data[0], file_data[1]) for file_data in roasted_files]
            summary = await generate_repo_summary(critique_pairs, api_key)
        
        # Update job status
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.COMPLETED,
            "message": f"Critique completed for {len(roasted_files)} files in {roast_request.style} style",
            "roasted_files": roasted_files,
            "summary": summary,
            "style": roast_request.style,
            "parameters": {
                "style": roast_request.style,
                "extensions": roast_request.extensions,
                "directories": roast_request.directories,
                "file_count": roast_request.file_count,
                "description": roast_request.description,
                "suggestions": roast_request.suggestions
            }
        }
        
        response_data = {
            "job_id": job_id,
            "status": JobStatus.COMPLETED,
            "message": f"Critique completed for {len(roasted_files)} files in {roast_request.style} style",
            "roasted_files": roasted_files,
            "parameters": {
                "style": roast_request.style,
                "extensions": roast_request.extensions,
                "directories": roast_request.directories,
                "file_count": roast_request.file_count,
                "description": roast_request.description,
                "suggestions": roast_request.suggestions
            }
        }
        
        if summary:
            response_data["summary"] = summary
            
        return response_data
        
    except Exception as e:
        # Update job status to failed
        analysis_jobs[job_id] = {
            **job_data,
            "status": JobStatus.FAILED,
            "message": "Auto-critique failed",
            "error": str(e)
        }
        
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to critique files: {str(e)}"
        )

@router.get("/auto-roast/{job_id}", response_model=dict)
async def get_auto_roast_results(job_id: str):
    """
    Get the results of an auto-roast job.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = analysis_jobs[job_id]
    
    if "roasted_files" not in job_data:
        raise HTTPException(
            status_code=400,
            detail="No roasted files found for this job"
        )
    
    response_data = {
        "job_id": job_id,
        "status": job_data.get("status", JobStatus.FAILED),
        "message": job_data.get("message", ""),
        "roasted_files": job_data.get("roasted_files", []),
        "error": job_data.get("error")
    }
    
    # Add optional fields if they exist
    if "summary" in job_data:
        response_data["summary"] = job_data["summary"]
        
    if "parameters" in job_data:
        response_data["parameters"] = job_data["parameters"]