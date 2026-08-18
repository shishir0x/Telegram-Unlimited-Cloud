FROM python:3.11.7-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=7860

# Create user with UID 1000 for Hugging Face Spaces compatibility
RUN useradd -m -u 1000 user

WORKDIR /home/user/app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files and set permissions
COPY --chown=user:user . .

# Ensure cache directory exists and is writable
RUN mkdir -p /home/user/app/cache /home/user/app/downloads && \
    chown -R user:user /home/user/app

USER user

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
