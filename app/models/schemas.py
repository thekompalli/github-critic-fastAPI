from pydantic import BaseModel, HttpUrl, Field
from typing import Dict, List, Optional, Any
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "pending"
    CLONING = "cloning"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"

class CritiqueStyle(str, Enum):
    BRUTAL = "brutal"
    CONSTRUCTIVE = "constructive"
    EDUCATIONAL = "educational"
    FUNNY = "funny"
    SECURITY = "security"

class SuggestionMode(str, Enum):
    NONE = "none"
    BASIC = "basic"
    DETAILED = "detailed"

# Repository request models
class RepositoryRequest(BaseModel):
    repo_url: HttpUrl = Field(..., description="URL of the GitHub repository to analyze")

class JobIdRequest(BaseModel):
    job_id: str

class AutoRoastRequest(BaseModel):
    job_id: str
    style: CritiqueStyle = CritiqueStyle.BRUTAL
    description: Optional[str] = None
    extensions: Optional[List[str]] = None
    directories: Optional[List[str]] = None
    file_count: int = 2
    suggestions: SuggestionMode = SuggestionMode.NONE

# Directory navigation models
class DirectoryItem(BaseModel):
    name: str
    path: str
    
class FileItem(DirectoryItem):
    size: int
    extension: str
    
class DirectoryFolder(DirectoryItem):
    file_count: int
    
class DirectoryContents(BaseModel):
    current_path: str
    directories: List[DirectoryFolder]
    files: List[FileItem]
    error: Optional[str] = None

class DirectoryRequest(BaseModel):
    job_id: str
    path: str = ""  # Empty string means root directory

# Directory size models
class DirectorySizeInfo(BaseModel):
    path: str
    name: str
    total_files: int
    code_files: int
    subdirectories: int
    
class DirectorySizeResponse(BaseModel):
    directories: List[DirectorySizeInfo]
    total_count: int

# Selection models
class SelectionRequest(BaseModel):
    job_id: str
    paths: List[str] = Field(..., description="List of paths to select for analysis")

class SelectionResponse(BaseModel):
    job_id: str
    selected_paths: List[str]
    total_files: int
    message: str

# Sampling models
class SamplingRequest(BaseModel):
    job_id: str
    path: str
    sample_size: int = Field(20, gt=0, le=50, description="Number of files to sample")
    
class SamplingResponse(BaseModel):
    job_id: str
    path: str
    sampled_files: List[str]
    sample_size: int
    message: str

# Batch processing models
class BatchProcessRequest(BaseModel):
    job_id: str
    batch_size: int = Field(20, ge=5, le=50, description="Number of files per batch")

class BatchProcessResponse(BaseModel):
    job_id: str
    total_batches: int
    total_files: int
    message: str

# Analysis job models
class AnalysisJob(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.PENDING
    message: str = "Repository analysis started"

class FileCritique(BaseModel):
    type: str
    critiques: List[str] = []
    files: Optional[Dict[str, 'FileCritique']] = None
    message: Optional[str] = None

FileCritique.update_forward_refs()

class AnalysisResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    analysis_results: Optional[Dict[str, FileCritique]] = None
    error: Optional[str] = None

class ProgressResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    total_files: Optional[int] = None
    completed_files: Optional[int] = None
    total_batches: Optional[int] = None
    completed_batches: Optional[int] = None
    progress_percentage: Optional[float] = None
    error: Optional[str] = None

class RepositoryResponse(BaseModel):
    repo_url: str
    total_files: int
    file_stats: Dict[str, int]
    message: str