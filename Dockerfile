# Use official Python 3.10 slim image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy requirements if you have one (optional)
# For this example, we only need telegram and requests
RUN pip install --no-cache-dir python-telegram-bot requests

# Copy the script and wordlist into the container
COPY checker.py .
COPY wordlist.txt .

# Set environment variables placeholder (set these in Railway dashboard instead)
# ENV TELEGRAM_BOT_TOKEN=your_token
# ENV TELEGRAM_CHAT_ID=your_chat_id
# ENV WEBSHARE_API_KEY=your_webshare_api_key

# Run the script
CMD ["python3", "checker.py"]
