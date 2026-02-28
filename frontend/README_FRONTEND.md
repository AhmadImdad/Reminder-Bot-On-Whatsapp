# WhatsApp Reminder Bot Dashboard

A beautiful, secure, and responsive web dashboard built with Streamlit to manage and monitor your WhatsApp Reminder Bot.

## Features
- 🔐 **First-Time Setup**: Securely create an admin user with bcrypt password hashing.
- 📋 **Upcoming Reminders**: View what's scheduled, color-coded urgencies, one-click manual deletion, and a button to test Green API.
- 📜 **Full History**: View your completed, cancelled, or failed reminders via a paginated data table. Filter and export safely to CSV!
- ➕ **Creation Form**: Manually schedule highly customized reminders directly from the web without texting the bot!
- 📊 **Analytics**: Easy-to-read charts calculating success rates and system health.
- 🗃️ **Backup**: Download an exact `.db` snapshot of your SQLite database immediately from the settings page.

## Running Locally

1. Create a virtual environment or use the bots existing virtual environment:
   ```bash
   conda activate reminderBot  # (from Backend)
   # Or using standard python:
   python3 -m venv venv && source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   cd frontend
   pip install -r requirements_frontend.txt
   ```

3. Start up the Dashboard:
   ```bash
   streamlit run dashboard.py
   ```

When you first launch the app, you will be prompted to create an Admin username and strong password. Be sure to remember them! It will hash the resulting password securely inside `auth.yaml`.

## Deploying on Oracle Cloud via Systemd

You can run this side-by-side with your backend API!

1. Edit the environment variables located in the provided `dashboard.service` file to match your Ubuntu home directory layout (Default uses `/home/ubuntu/reminder-bot`).

2. Copy the system file and enable it:
```bash
sudo cp dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dashboard.service
sudo systemctl start dashboard.service
```

3. Expose Port 3000 to the internet via your Oracle Cloud Security Lists, OR configure a free Cloudflare Tunnel:
```bash
cloudflared tunnel --url http://127.0.0.1:3000
```
