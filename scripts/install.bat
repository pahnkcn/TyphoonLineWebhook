@echo off
setlocal enabledelayedexpansion

echo ========================================
echo ใจดี Chatbot - Local Development Setup
echo ========================================
echo.

:: Check if Python is installed
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Python is not installed or not in PATH.
    echo Please install Python 3.11 or higher from https://www.python.org/downloads/
    exit /b 1
)

:: Check Python version
for /f "tokens=2 delims=." %%a in ('python -c "import sys; print(sys.version.split(\".\")[0])"') do set PYTHON_VERSION=%%a
if %PYTHON_VERSION% lss 3 (
    echo Python version 3 or higher is required. Found version %PYTHON_VERSION%.
    exit /b 1
)

:: Check if Docker is installed (optional)
where docker >nul 2>&1
set DOCKER_AVAILABLE=0
if %ERRORLEVEL% equ 0 (
    set DOCKER_AVAILABLE=1
    echo Docker is available. You can use Docker for deployment.
) else (
    echo Docker is not available. You can install it from https://www.docker.com/products/docker-desktop
    echo Continuing with local installation...
)

:: Create a virtual environment if it doesn't exist
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo Failed to create virtual environment.
        exit /b 1
    )
)

:: Activate the virtual environment and install dependencies
echo Activating virtual environment and installing dependencies...
call venv\Scripts\activate
if %ERRORLEVEL% neq 0 (
    echo Failed to activate virtual environment.
    exit /b 1
)

echo Installing required packages...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo Failed to install dependencies.
    exit /b 1
)

:: Check for .env file and create from .env.example
if not exist .env (
    if exist .env.example (
        echo Creating .env file from .env.example...
        copy .env.example .env
        echo Please edit the .env file with your actual credentials.
    ) else (
        echo WARNING: .env.example not found. Please create .env manually.
    )
)

echo.
echo Installation completed successfully!
echo.
echo Available options:
echo 1. Run locally with Python (Waitress)
echo 2. Run with Docker Compose (requires Docker)
echo 3. Run tests
echo 4. Exit

choice /c 1234 /n /m "Choose an option [1-4]: "

if %ERRORLEVEL% equ 1 (
    echo Starting the chatbot locally...
    python wsgi.py
) else if %ERRORLEVEL% equ 2 (
    if %DOCKER_AVAILABLE% equ 1 (
        echo Starting with Docker Compose...
        docker compose up -d
    ) else (
        echo Docker is not available. Cannot start with Docker Compose.
    )
) else if %ERRORLEVEL% equ 3 (
    echo Running tests...
    pytest tests/ -v --tb=short
) else (
    echo Exiting installation.
)

echo.
echo Thank you for installing ใจดี Chatbot!
echo.

:: Deactivate the virtual environment
call venv\Scripts\deactivate

endlocal
