# Use the official Python image from the Docker Hub
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the files into the container
COPY app.py .
COPY templates/ templates/

# Install ExifTool, Flask, and PyYAML
RUN apt-get update && \
    apt-get install -y exiftool && \
    pip install flask pyyaml && \
    rm -rf /var/lib/apt/lists/*

# Expose port 5000 for Flask
EXPOSE 5000

# Command to run the Flask app
CMD ["python", "app.py"]