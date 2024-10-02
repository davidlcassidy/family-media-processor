# Use a lightweight Linux distribution with PowerShell and ExifTool
FROM mcr.microsoft.com/powershell:latest

# Install dependencies (ExifTool and git if needed)
RUN apt-get update && \
    apt-get install -y libimage-exiftool-perl && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy the PowerShell script into the container
COPY process-photos.ps1 /app/process-photos.ps1

# Set the PowerShell script as the entry point
ENTRYPOINT ["pwsh", "/app/process-photos.ps1"]
