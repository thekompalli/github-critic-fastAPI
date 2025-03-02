import os
import shutil
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cleanup_directory(directory_path: str) -> None:
    """
    Clean up a directory after it's no longer needed.
    
    Args:
        directory_path (str): Path to the directory to clean up.
    """
    if os.path.exists(directory_path):
        try:
            shutil.rmtree(directory_path, ignore_errors=True)
            logger.info(f"Cleaned up directory: {directory_path}")
        except Exception as e:
            logger.error(f"Failed to clean up directory {directory_path}: {str(e)}")

def read_file_content(file_path: str, max_size: int = 1_000_000) -> str:
    """
    Read the content of a file, with a size limit.
    
    Args:
        file_path (str): Path to the file.
        max_size (int): Maximum file size to read in bytes.
        
    Returns:
        str: Content of the file.
    """
    try:
        file_size = os.path.getsize(file_path)
        if file_size > max_size:
            return f"File too large to analyze ({file_size} bytes)"
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"