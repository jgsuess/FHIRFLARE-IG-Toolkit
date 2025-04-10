# 1. Base Image: Use an official Python runtime as a parent image
FROM python:3.10-slim AS base

# Set environment variables
# Prevents python creating .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
# Prevents python buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

# 2. Set Work Directory: Create and set the working directory in the container
WORKDIR /app

# 3. Install Dependencies: Copy only the requirements file first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy Application Code: Copy the rest of your application code into the work directory
COPY . .

# 5. Expose Port: Tell Docker the container listens on port 5000
EXPOSE 5000

# 6. Run Command: Specify the command to run when the container starts
#    Using "flask run --host=0.0.0.0" makes the app accessible from outside the container
#    Note: FLASK_APP should be set, often via ENV or run.py structure
#    Note: For development, FLASK_DEBUG=1 might be useful (e.g., ENV FLASK_DEBUG=1)
CMD ["flask", "run", "--host=0.0.0.0"]