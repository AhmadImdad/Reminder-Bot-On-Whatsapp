#!/bin/bash

# Configuration
APP_DIR="/opt/reminder-bot"
VENV_DIR="$APP_DIR/venv"
USER="ubuntu" # Adjust if not running on Ubuntu default

echo "Setting up WhatsApp Reminder Bot..."

# 1. Update system and install dependencies
sudo apt update
sudo apt install -y python3-venv python3-pip

# 2. Setup App Directory
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

# Copy files to app directory
cp -r ./* $APP_DIR/

# 3. Create Virtual Environment
cd $APP_DIR
python3 -m venv $VENV_DIR
source $VENV_DIR/bin/activate

# 4. Install Python requirements
pip install -r requirements.txt

# 5. Create .env file if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️ Created .env file from .env.example."
    echo "Please edit $APP_DIR/.env with your API keys before starting the service."
fi

# 6. Setup systemd service
sudo cp reminder-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable reminder-bot.service

echo ""
echo "✅ Setup Complete!"
echo "Next steps:"
echo "1. Edit the config: nano $APP_DIR/.env"
echo "2. Start the service: sudo systemctl start reminder-bot"
echo "3. Check status: sudo systemctl status reminder-bot"
echo "4. View logs: tail -f $APP_DIR/app.log"
