# WhatsApp Reminder Bot

A robust, intelligent WhatsApp reminder bot powered by Green API for messaging and Groq's APIs (Whisper & LLaMA 3.1) for natural language processing and voice transcription.

## Features
- **Natural Language Parsing**: "Remind me to call John tomorrow at 5 PM"
- **Voice Messages**: Send a voice note, and it will be transcribed and processed.
- **Smart Confirmation Flow**: Asks for clarification if the date or time is ambiguous.
- **Background Scheduler**: Accurately triggers messages when the reminder is due.
- **Robust Error Handling**: Connection retries, invalid input handling, and fallback capabilities.

## Prerequisites

1. An **Oracle Cloud Always Free Instance (Ubuntu)**.
2. A **Green API** account (Instance ID and Token).
3. A **Groq API Key**.
4. Basic knowledge of SSH and terminal usage.

## Installation & Deployment (Ubuntu)

The repository provides a deployment script `setup.sh` which installs dependencies, sets up a virtual environment, and configures a systemd service.

### 1. Clone the repository
Send all the project files into your desired directory on the server (the default in `setup.sh` is `/opt/reminder-bot`).

### 2. Run the setup script
```bash
sudo chmod +x setup.sh
sudo ./setup.sh
```

### 3. Configuration
After running the script, edit the environment variables in `/opt/reminder-bot/.env`.
```bash
sudo nano /opt/reminder-bot/.env
```
Fill in the following variables:
```
# Green API Credentials
GREEN_API_INSTANCE_ID="your_instance_id"
GREEN_API_TOKEN="your_token"

# Groq API Credentials
GROQ_API_KEY="your_groq_key"
```

### 4. Start the Service
```bash
sudo systemctl start reminder-bot
sudo systemctl enable reminder-bot
```

### 5. Webhook Configuration (Green API)
Log into your Green API console.
Set the webhook URL to point to your server's IP address and port 5000:
`http://<YOUR_SERVER_IP>:5000/webhook`
Make sure to open port 5000 in your Oracle Cloud VCN firewall rules.

## Using the Bot
Send a message from WhatsApp to the integrated number:
- *Text*: "Set reminder for meeting on Monday at 10 AM"
- *Voice*: Speak your reminder.
- *Commands*:
  - `list reminders`: Show pending items
  - `cancel <id>`: Delete a reminder
  - `help`: Print help manual

## Testing API Locally
You can test the health endpoint easily:
```bash
curl http://localhost:5000/health
```
