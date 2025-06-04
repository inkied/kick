# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and script
COPY requirements.txt .
COPY username_checker.py .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the checker script
CMD ["python", "kick.py"]
