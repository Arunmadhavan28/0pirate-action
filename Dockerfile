# Dockerfile for the GitHub Action
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the action's script and make it executable
COPY run_action.py .
RUN chmod +x /app/run_action.py

# Define the entrypoint for the action
ENTRYPOINT ["python", "/app/run_action.py"]

