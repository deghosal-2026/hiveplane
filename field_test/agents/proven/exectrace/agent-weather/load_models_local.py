"""Load model for local OMLX server."""
import os
os.environ.setdefault('OPENAI_API_KEY', 'omlx-test')
os.environ.setdefault('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1')
