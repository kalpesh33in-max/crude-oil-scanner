# gunicorn.conf.py

# Set the number of worker processes to 1. 
# This is CRITICAL to prevent Gunicorn from fighting with the background thread's asyncio loop.
workers = 1 

# Set the number of threads per worker to 1 (optional, but good practice for stability).
threads = 1
