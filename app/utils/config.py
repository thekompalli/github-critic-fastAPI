"""
Configuration utilities for the GitHub Critic application.
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_llm_api_key():
    """
    Get the API key for the LLM service from environment variables.
    
    Returns:
        str: The API key
    """
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable not set")
    return api_key