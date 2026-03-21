# TyphoonLineWebhook

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

TyphoonLineWebhook is a LINE-based chatbot designed to provide support and guidance for individuals dealing with substance abuse issues. The chatbot now uses xAI's Grok-4 model to deliver empathetic, non-judgmental responses in Thai language.

## 🌟 Features

- **Conversational Support**: Engages users in supportive dialogue about substance use concerns
- **Risk Assessment**: Automatically detects high-risk keywords and provides emergency resources
- **Follow-up System**: Scheduled follow-ups to check on user progress (1, 3, 7, 14, and 30-day intervals)
- **Progress Tracking**: Monitors user interactions and risk levels over time
- **Session Management**: Maintains conversation context with timeout notifications
- **Multi-component Architecture**: Uses Redis for caching, MySQL for persistent storage, and LINE for messaging

## 📋 Requirements

- Python 3.9+ (3.11 recommended)
- MySQL 8.0+
- Redis 6+
- LINE Messaging API credentials
- xAI API key (XAI_API_KEY)

## 🚀 Installation

### Option 1: Docker Installation (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/TyphoonLineWebhook.git
   cd TyphoonLineWebhook
   ```

2. Create a `.env` file from the template:
   ```bash
   cp .env.example .env
   ```

3. Edit the `.env` file with your credentials:
   ```
   LINE_CHANNEL_ACCESS_TOKEN=your_line_token
   LINE_CHANNEL_SECRET=your_line_secret
   XAI_API_KEY=your_xai_api_key
   ```

4. Build and start the containers:
   ```bash
   docker-compose up -d
   ```

### Option 2: Manual Installation

#### Windows

1. Run the installation script:
   ```
   install.bat
   ```

2. Follow the prompts to configure the application.

#### Linux/Ubuntu

1. Run the installation script with sudo:
   ```bash
   sudo bash install.sh
   ```

2. The script will set up all dependencies and configure the environment.

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API access token | - |
| `LINE_CHANNEL_SECRET` | LINE channel secret | - |
| `XAI_API_KEY` | xAI API key | - |
| `REDIS_HOST` | Redis host | localhost |
| `REDIS_PORT` | Redis port | 6379 |
| `MYSQL_HOST` | MySQL host | localhost |
| `MYSQL_USER` | MySQL username | root |
| `MYSQL_PASSWORD` | MySQL password | - |
| `MYSQL_DB` | MySQL database name | chatbot |
| `LOG_LEVEL` | Logging level | INFO |

### LINE Webhook Configuration

1. Create a LINE Bot account at [LINE Developers Console](https://developers.line.biz/)
2. Configure the webhook URL to point to your server:
   ```
   https://your-server-domain/callback
   ```
3. Enable webhook and disable auto-reply features

## 🏗️ Architecture

The application follows a modular architecture with these key components:

```
                  ┌─────────────┐
                  │  LINE API   │
                  └─────┬───────┘
                        │
                        ▼
┌────────────┐    ┌─────────────┐    ┌─────────────┐
│  xAI Grok  │◄───┤ App Server  ├───►│    Redis    │
└────────────┘    └─────┬───────┘    └─────────────┘
                        │
                        ▼
                  ┌─────────────┐
                  │    MySQL    │
                  └─────────────┘
```

### Key Components

- **app_main.py**: Main application handling LINE webhook events
- **async_api.py**: Backward-compatible async shim that forwards to xAI Grok
- **chat_history_db.py**: Database operations for conversation history
- **token_counter.py**: Token counting for API usage monitoring
- **middleware/rate_limiter.py**: Rate limiting implementation

## 🖥️ Development

### Project Structure

```
TyphoonLineWebhook/
│
├── app/                          # Application code
│   ├── __init__.py               # Package initialization
│   ├── app_main.py           # Main application
│   ├── async_api.py              # Asynchronous API client
│   ├── chat_history_db.py        # Database operations
│   ├── config.py                 # Configuration
│   ├── database_init.py          # Database initialization
│   ├── token_counter.py          # Token counting
│   ├── utils.py                  # Utilities
│   └── middleware/               # Middleware components
│       ├── __init__.py           # Package initialization
│       └── rate_limiter.py       # Rate limiting middleware
│
├── docker-compose.yml            # Docker compose configuration
├── Dockerfile                    # Docker configuration
├── logs/                         # Log directory
├── scripts/                      # Installation scripts
│   ├── install.bat               # Windows installation script
│   └── install.sh                # Linux installation script
├── wsgi.py                       # WSGI entry point
├── requirements.txt              # Python dependencies
├── .gitignore                    # Git ignore patterns
└── readme.md                     # This documentation
```

### Version Control

The project includes a comprehensive `.gitignore` file that excludes:
- Python bytecode and cache files
- Virtual environment directories
- Log files
- Local configuration and environment files
- IDE-specific files
- Database files

### Running Tests

```bash
pytest tests/
```

### Logging

Logs are stored in the `logs/` directory and rotated automatically when they reach 5&nbsp;MB. Verbosity is configurable through the `LOG_LEVEL` environment variable.

## 📱 Usage

### User Commands

| Command | Description |
|---------|-------------|
| `/help` | Display help information |
| `/status` | Show usage statistics |
| `/emergency` | Display emergency contacts |
| `/progress` | View progress report |
| `/followup` | Show next follow-up date and remaining schedule |

### Monitoring

Access the health check endpoint to monitor system status:
```
GET /health
```

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgements

- [LINE Messaging API](https://developers.line.biz/en/docs/messaging-api/)
- [xAI](https://x.ai) for Grok-4

- [Flask](https://flask.palletsprojects.com/) web framework
