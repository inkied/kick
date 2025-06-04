FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy your script and any other files
COPY . .

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run your script
CMD ["python", "kick.py"]
