import os
import sys
import subprocess
import sqlite3
import shutil
import uuid
import signal
import time
import requests
import threading
import base64
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from zipfile import ZipFile

# Flask App Initialization
app = Flask(__name__)
app.secret_key = "ZENITSU_BOT_HOST_SECRET_KEY_2024"
UPLOAD_FOLDER = 'user_bots'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- ADMIN CREDENTIALS ---
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# --- SIMPLE USER DATABASE ---
def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database with simple user system"""
    conn = get_db()
    c = conn.cursor()
    
    # Users Table (Simple)
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE,
                  password TEXT,
                  email TEXT,
                  plan_type TEXT DEFAULT 'Free',
                  bot_limit INTEGER DEFAULT 3,
                  is_banned INTEGER DEFAULT 0,
                  joined_at TEXT)''')
    
    # Bots Table
    c.execute('''CREATE TABLE IF NOT EXISTS bots
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  bot_name TEXT,
                  pid INTEGER,
                  status TEXT,
                  extract_path TEXT,
                  working_dir TEXT,
                  main_file TEXT,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    
    # Settings Table
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    # Default settings
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('vip_price', '200')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('premium_price', '100')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('upi_id', 'lordleadernikki-1@oksbi')")
    
    # Add default admin user if not exists
    c.execute("SELECT * FROM users WHERE username = ?", (ADMIN_USER,))
    if not c.fetchone():
        c.execute('INSERT INTO users (username, password, plan_type, bot_limit) VALUES (?, ?, ?, ?)',
                 (ADMIN_USER, ADMIN_PASS, 'Admin', 999))
    
    conn.commit()
    conn.close()

init_db()
running_processes = {}

# --- TELEGRAM CREDENTIALS ---
TELEGRAM_TOKEN = "8583304774:AAGJ9qfys5g5yW2d36WD4TGJWK-93Zi34Gw"
TELEGRAM_CHAT_ID = "8028357250"

def send_telegram_notification(message):
    """Send notification to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except:
        pass

# --- HELPER FUNCTIONS ---
def find_python_env(root_folder):
    """Find main Python file in folder"""
    for root, dirs, files in os.walk(root_folder):
        for file in files:
            if file.endswith(".py") and file not in ["setup.py", "__init__.py"]:
                return file, root
    return None, None

# --- AUTHENTICATION ROUTES ---
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', 
                           (username, password)).fetchone()
        conn.close()
        
        if user:
            if user['is_banned']:
                return render_template('login.html', error="🚫 Account Banned")
            
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash("Login successful!", "success")
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid username or password")
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email', '')
        
        conn = get_db()
        # Check if username exists
        existing = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if existing:
            conn.close()
            return render_template('register.html', error="Username already exists")
        
        # Create new user
        import datetime
        date = datetime.datetime.now().strftime("%Y-%m-%d")
        conn.execute('INSERT INTO users (username, password, email, joined_at) VALUES (?, ?, ?, ?)',
                    (username, password, email, date))
        conn.commit()
        conn.close()
        
        flash("Registration successful! Please login.", "success")
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully!", "info")
    return redirect(url_for('login'))

# --- DASHBOARD ROUTES ---
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    bots = conn.execute('SELECT * FROM bots WHERE user_id = ?', (session['user_id'],)).fetchall()
    
    # Get settings
    vip_price = conn.execute("SELECT value FROM settings WHERE key='vip_price'").fetchone()['value']
    premium_price = conn.execute("SELECT value FROM settings WHERE key='premium_price'").fetchone()['value']
    upi_id = conn.execute("SELECT value FROM settings WHERE key='upi_id'").fetchone()['value']
    
    conn.close()
    
    return render_template('dashboard.html', 
                          user=user, 
                          bots=bots,
                          vip_price=vip_price,
                          premium_price=premium_price,
                          upi_id=upi_id)

@app.route('/upload_bot', methods=['POST'])
def upload_bot():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    # Check bot limit
    current_bots = conn.execute('SELECT COUNT(*) FROM bots WHERE user_id = ?', 
                               (session['user_id'],)).fetchone()[0]
    
    if current_bots >= user['bot_limit']:
        conn.close()
        flash(f"Bot limit reached! Upgrade plan to add more bots.", "error")
        return redirect(url_for('dashboard'))
    
    # Get form data
    file = request.files['bot_file']
    bot_name = request.form['bot_name']
    
    # Create unique folder
    bot_uuid = str(uuid.uuid4())[:8]
    extract_path = os.path.join(UPLOAD_FOLDER, f"{session['username']}_{bot_uuid}")
    os.makedirs(extract_path, exist_ok=True)
    
    main_file = None
    working_dir = extract_path
    
    # Handle ZIP file
    if file.filename.endswith('.zip'):
        zip_path = os.path.join(extract_path, "upload.zip")
        file.save(zip_path)
        try:
            with ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            os.remove(zip_path)
            main_file, working_dir = find_python_env(extract_path)
        except:
            shutil.rmtree(extract_path, ignore_errors=True)
            flash("Invalid ZIP file!", "error")
            return redirect(url_for('dashboard'))
    
    # Handle Python file
    elif file.filename.endswith('.py'):
        main_file = file.filename
        file.save(os.path.join(extract_path, main_file))
        working_dir = extract_path
    
    else:
        shutil.rmtree(extract_path, ignore_errors=True)
        flash("❌ Please upload .zip or .py file only", "error")
        return redirect(url_for('dashboard'))
    
    if not main_file:
        shutil.rmtree(extract_path, ignore_errors=True)
        conn.close()
        flash("❌ No Python file found in upload", "error")
        return redirect(url_for('dashboard'))
    
    # Install requirements if exists
    req_file = os.path.join(working_dir, "requirements.txt")
    if os.path.exists(req_file):
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-warn-script-location",
                                  "-r", req_file], cwd=working_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            flash("✅ Dependencies installed", "success")
        except:
            flash("⚠️ Some dependencies failed to install", "warning")
    
    # Save bot to database
    conn.execute('INSERT INTO bots (user_id, bot_name, status, extract_path, working_dir, main_file) VALUES (?, ?, ?, ?, ?, ?)',
                (session['user_id'], bot_name, 'stopped', extract_path, working_dir, main_file))
    conn.commit()
    conn.close()
    
    flash("✅ Bot uploaded successfully! Click START to run.", "success")
    return redirect(url_for('dashboard'))

@app.route('/action/<action>/<int:bot_id>')
def bot_action(action, bot_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    bot = conn.execute('SELECT * FROM bots WHERE id = ?', (bot_id,)).fetchone()
    
    if not bot or bot['user_id'] != session['user_id']:
        return "Unauthorized"
    
    if action == 'start':
        if bot['status'] != 'running':
            log_path = os.path.join(bot['working_dir'], "bot.log")
            log_file = open(log_path, "w")
            
            cmd = [sys.executable, "-u", bot['main_file']]
            proc = subprocess.Popen(cmd, cwd=bot['working_dir'], stdout=log_file, stderr=subprocess.STDOUT)
            running_processes[bot_id] = proc
            
            conn.execute('UPDATE bots SET status=?, pid=? WHERE id=?',
                        ('running', proc.pid, bot_id))
            flash(f"✅ Bot '{bot['bot_name']}' started", "success")
    
    elif action == 'stop':
        if bot['status'] == 'running':
            if bot_id in running_processes:
                running_processes[bot_id].terminate()
                del running_processes[bot_id]
            if bot['pid']:
                try:
                    os.kill(bot['pid'], signal.SIGTERM)
                except:
                    pass
            conn.execute('UPDATE bots SET status=?, pid=NULL WHERE id=?', ('stopped', bot_id))
            flash(f"⏸️ Bot '{bot['bot_name']}' stopped", "warning")
    
    elif action == 'delete':
        bot_action('stop', bot_id)
        try:
            shutil.rmtree(bot['extract_path'], ignore_errors=True)
        except:
            pass
        conn.execute('DELETE FROM bots WHERE id=?', (bot_id,))
        flash(f"🗑️ Bot '{bot['bot_name']}' deleted", "error")
    
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

# --- BOT MONITORING ---
@app.route('/get_logs/<int:bot_id>')
def get_logs(bot_id):
    if 'user_id' not in session:
        return "Login required"
    
    conn = get_db()
    bot = conn.execute('SELECT * FROM bots WHERE id = ?', (bot_id,)).fetchone()
    conn.close()
    
    if not bot or bot['user_id'] != session['user_id']:
        return "Unauthorized"
    
    log_path = os.path.join(bot['working_dir'], "bot.log")
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            return f.read()
    return "No logs yet..."

# --- ADMIN ROUTES ---
@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['admin_logged_in'] = True
            session['admin_user'] = username
            return redirect(url_for('admin_panel'))
        else:
            return render_template('admin_login.html', error="Wrong credentials!")
    
    return render_template('admin_login.html')

@app.route('/admin')
def admin_panel():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    conn = get_db()
    all_users = conn.execute('SELECT * FROM users').fetchall()
    all_bots = conn.execute('SELECT * FROM bots').fetchall()
    
    settings = conn.execute('SELECT * FROM settings').fetchall()
    settings_dict = {row['key']: row['value'] for row in settings}
    
    conn.close()
    
    return render_template('admin_panel.html',
                          all_users=all_users,
                          all_bots=all_bots,
                          settings=settings_dict)

@app.route('/admin/update_settings', methods=['POST'])
def update_settings():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    conn = get_db()
    conn.execute("UPDATE settings SET value=? WHERE key='vip_price'", 
                (request.form.get('vip_price'),))
    conn.execute("UPDATE settings SET value=? WHERE key='premium_price'", 
                (request.form.get('premium_price'),))
    conn.execute("UPDATE settings SET value=? WHERE key='upi_id'", 
                (request.form.get('upi_id'),))
    conn.commit()
    conn.close()
    
    flash("✅ Settings updated", "success")
    return redirect(url_for('admin_panel'))

@app.route('/admin/user_action', methods=['POST'])
def user_action():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    user_id = request.form.get('user_id')
    action = request.form.get('action')
    
    conn = get_db()
    
    if action == 'ban':
        conn.execute('UPDATE users SET is_banned=1 WHERE id=?', (user_id,))
        flash("User banned", "warning")
    elif action == 'unban':
        conn.execute('UPDATE users SET is_banned=0 WHERE id=?', (user_id,))
        flash("User unbanned", "success")
    elif action == 'delete':
        conn.execute('DELETE FROM users WHERE id=?', (user_id,))
        conn.execute('DELETE FROM bots WHERE user_id=?', (user_id,))
        flash("User deleted", "error")
    elif action == 'update_plan':
        plan = request.form.get('plan')
        limit = request.form.get('limit')
        conn.execute('UPDATE users SET plan_type=?, bot_limit=? WHERE id=?', 
                    (plan, limit, user_id))
        flash("Plan updated", "success")
    
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/admin/bot_action/<action>/<int:bot_id>')
def admin_bot_action(action, bot_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    # Reuse bot_action function
    return bot_action(action, bot_id)

# --- MAIN ---
if __name__ == "__main__":
    print("\n" + "="*60)
    print("ZENITSU BOT HOSTING SYSTEM")
    print("="*60)
    print(f"Admin: {ADMIN_USER} / {ADMIN_PASS}")
    print(f"URL: http://localhost:19149")
    print("="*60)
    
    # Send startup notification
    startup_msg = f"""🚀 ZENIHOST Started
Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
URL: http://localhost:19149
Admin: {ADMIN_USER}"""
    
    try:
        send_telegram_notification(startup_msg)
        print("[✓] Telegram notification sent")
    except:
        print("[!] Telegram notification failed")
    
    app.run(host='0.0.0.0', port=19149, debug=True)
