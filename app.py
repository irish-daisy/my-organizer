{\rtf1\ansi\ansicpg1252\cocoartf2867
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 from flask import Flask, request, render_template_string, redirect, session, url_for, jsonify\
import sqlite3\
import hashlib\
from datetime import datetime, timedelta\
import json\
import os\
\
app = Flask(__name__)\
app.secret_key = '\uc0\u1089 \u1077 \u1082 \u1088 \u1077 \u1090 \u1085 \u1099 \u1081 _\u1082 \u1083 \u1102 \u1095 _\u1076 \u1083 \u1103 _\u1089 \u1077 \u1089 \u1089 \u1080 \u1081 _12345'\
\
# --- \uc0\u1041 \u1040 \u1047 \u1040  \u1044 \u1040 \u1053 \u1053 \u1067 \u1061  ---\
def init_db():\
    conn = sqlite3.connect('organizer.db')\
    cur = conn.cursor()\
    \
    cur.execute('''CREATE TABLE IF NOT EXISTS users (\
        id INTEGER PRIMARY KEY AUTOINCREMENT,\
        username TEXT UNIQUE,\
        password TEXT\
    )''')\
    \
    cur.execute('''CREATE TABLE IF NOT EXISTS tasks (\
        id INTEGER PRIMARY KEY AUTOINCREMENT,\
        user_id INTEGER,\
        title TEXT,\
        category TEXT,\
        date TEXT,\
        repeat_type TEXT,\
        repeat_day INTEGER,\
        status TEXT DEFAULT 'active',\
        created_at TEXT,\
        FOREIGN KEY (user_id) REFERENCES users (id)\
    )''')\
    \
    conn.commit()\
    conn.close()\
\
init_db()\
\
def get_user_id():\
    return session.get('user_id')\
\
def get_db():\
    conn = sqlite3.connect('organizer.db')\
    conn.row_factory = sqlite3.Row\
    return conn\
\
# --- \uc0\u1043 \u1051 \u1040 \u1042 \u1053 \u1040 \u1071  \u1057 \u1058 \u1056 \u1040 \u1053 \u1048 \u1062 \u1040  ---\
@app.route('/')\
def index():\
    if 'user_id' not in session:\
        return redirect('/login')\
    \
    user_id = session['user_id']\
    conn = get_db()\
    cur = conn.cursor()\
    \
    tasks = cur.execute('SELECT * FROM tasks WHERE user_id = ? AND status = "active" ORDER BY date ASC', (user_id,)).fetchall()\
    \
    categories = \{\
        'focus': [],\
        'important': [],\
        'medium': [],\
        'later': []\
    \}\
    \
    for task in tasks:\
        cat = task['category'] if task['category'] in categories else 'later'\
        categories[cat].append(dict(task))\
    \
    conn.close()\
    \
    return render_template_string(MAIN_PAGE, \
                                   focus_tasks=categories['focus'],\
                                   important_tasks=categories['important'],\
                                   medium_tasks=categories['medium'],\
                                   later_tasks=categories['later'],\
                                   username=session.get('username', '\uc0\u1055 \u1086 \u1083 \u1100 \u1079 \u1086 \u1074 \u1072 \u1090 \u1077 \u1083 \u1100 '))\
\
# --- API: \uc0\u1044 \u1086 \u1073 \u1072 \u1074 \u1083 \u1077 \u1085 \u1080 \u1077  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080  ---\
@app.route('/api/task', methods=['POST'])\
def add_task():\
    if 'user_id' not in session:\
        return jsonify(\{'error': 'Unauthorized'\}), 401\
    \
    data = request.json\
    title = data.get('title', '').strip()\
    \
    if not title:\
        return jsonify(\{'error': 'Title is required'\}), 400\
    \
    conn = get_db()\
    cur = conn.cursor()\
    cur.execute('''INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status, created_at)\
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime("now"))''',\
                   (session['user_id'], title, 'later', '', 'none', None, 'active'))\
    conn.commit()\
    conn.close()\
    \
    return jsonify(\{'success': True, 'message': 'Task added'\})\
\
# --- API: \uc0\u1054 \u1073 \u1085 \u1086 \u1074 \u1083 \u1077 \u1085 \u1080 \u1077  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080  ---\
@app.route('/api/task/<int:task_id>', methods=['PUT'])\
def update_task(task_id):\
    if 'user_id' not in session:\
        return jsonify(\{'error': 'Unauthorized'\}), 401\
    \
    data = request.json\
    title = data.get('title', '').strip()\
    category = data.get('category', 'later')\
    date = data.get('date', '')\
    repeat_type = data.get('repeat_type', 'none')\
    repeat_day = data.get('repeat_day')\
    \
    if not title:\
        return jsonify(\{'error': 'Title is required'\}), 400\
    \
    conn = get_db()\
    cur = conn.cursor()\
    cur.execute('''UPDATE tasks SET \
                   title = ?, category = ?, date = ?, repeat_type = ?, repeat_day = ?\
                   WHERE id = ? AND user_id = ?''',\
                   (title, category, date, repeat_type, repeat_day, task_id, session['user_id']))\
    conn.commit()\
    conn.close()\
    \
    return jsonify(\{'success': True, 'message': 'Task updated'\})\
\
# --- API: \uc0\u1059 \u1076 \u1072 \u1083 \u1077 \u1085 \u1080 \u1077  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080  ---\
@app.route('/api/task/<int:task_id>', methods=['DELETE'])\
def delete_task(task_id):\
    if 'user_id' not in session:\
        return jsonify(\{'error': 'Unauthorized'\}), 401\
    \
    conn = get_db()\
    cur = conn.cursor()\
    cur.execute('DELETE FROM tasks WHERE id = ? AND user_id = ?', (task_id, session['user_id']))\
    conn.commit()\
    conn.close()\
    \
    return jsonify(\{'success': True, 'message': 'Task deleted'\})\
\
# --- API: \uc0\u1042 \u1099 \u1087 \u1086 \u1083 \u1085 \u1077 \u1085 \u1080 \u1077  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080  ---\
@app.route('/api/task/<int:task_id>/done', methods=['POST'])\
def done_task(task_id):\
    if 'user_id' not in session:\
        return jsonify(\{'error': 'Unauthorized'\}), 401\
    \
    conn = get_db()\
    cur = conn.cursor()\
    \
    task = cur.execute('SELECT * FROM tasks WHERE id = ? AND user_id = ?', (task_id, session['user_id'])).fetchone()\
    if not task:\
        return jsonify(\{'error': 'Task not found'\}), 404\
    \
    # \uc0\u1045 \u1089 \u1083 \u1080  \u1087 \u1086 \u1074 \u1090 \u1086 \u1088 \u1103 \u1102 \u1097 \u1072 \u1103 \u1089 \u1103  \'97 \u1089 \u1086 \u1079 \u1076 \u1072 \u1105 \u1084  \u1085 \u1086 \u1074 \u1091 \u1102 \
    if task['repeat_type'] != 'none':\
        new_date = None\
        if task['repeat_type'] == 'daily':\
            new_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')\
        elif task['repeat_type'] == 'weekly' and task['repeat_day'] is not None:\
            today = datetime.now()\
            days_ahead = task['repeat_day'] - today.weekday()\
            if days_ahead <= 0:\
                days_ahead += 7\
            new_date = (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')\
        \
        cur.execute('''INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status, created_at)\
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime("now"))''',\
                       (task['user_id'], task['title'], task['category'], new_date, task['repeat_type'], task['repeat_day'], 'active'))\
    \
    cur.execute('UPDATE tasks SET status = "done" WHERE id = ?', (task_id,))\
    conn.commit()\
    conn.close()\
    \
    return jsonify(\{'success': True, 'message': 'Task done'\})\
\
# --- API: \uc0\u1055 \u1086 \u1083 \u1091 \u1095 \u1080 \u1090 \u1100  \u1074 \u1089 \u1077  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080  ---\
@app.route('/api/tasks')\
def get_tasks():\
    if 'user_id' not in session:\
        return jsonify(\{'error': 'Unauthorized'\}), 401\
    \
    conn = get_db()\
    cur = conn.cursor()\
    tasks = cur.execute('SELECT * FROM tasks WHERE user_id = ? AND status = "active" ORDER BY date ASC', (session['user_id'],)).fetchall()\
    conn.close()\
    \
    result = []\
    for task in tasks:\
        result.append(dict(task))\
    \
    return jsonify(result)\
\
# --- \uc0\u1056 \u1045 \u1043 \u1048 \u1057 \u1058 \u1056 \u1040 \u1062 \u1048 \u1071  ---\
@app.route('/register', methods=['GET', 'POST'])\
def register():\
    if request.method == 'POST':\
        username = request.form['username'].strip()\
        password = hashlib.md5(request.form['password'].encode()).hexdigest()\
        \
        if not username or not password:\
            return render_template_string(REGISTER_PAGE, error='\uc0\u1047 \u1072 \u1087 \u1086 \u1083 \u1085 \u1080 \u1090 \u1077  \u1074 \u1089 \u1077  \u1087 \u1086 \u1083 \u1103 ')\
        \
        conn = get_db()\
        try:\
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))\
            conn.commit()\
            conn.close()\
            return redirect('/login')\
        except sqlite3.IntegrityError:\
            return render_template_string(REGISTER_PAGE, error='\uc0\u1055 \u1086 \u1083 \u1100 \u1079 \u1086 \u1074 \u1072 \u1090 \u1077 \u1083 \u1100  \u1091 \u1078 \u1077  \u1089 \u1091 \u1097 \u1077 \u1089 \u1090 \u1074 \u1091 \u1077 \u1090 ')\
    \
    return render_template_string(REGISTER_PAGE, error=None)\
\
# --- \uc0\u1042 \u1061 \u1054 \u1044  ---\
@app.route('/login', methods=['GET', 'POST'])\
def login():\
    if request.method == 'POST':\
        username = request.form['username'].strip()\
        password = hashlib.md5(request.form['password'].encode()).hexdigest()\
        \
        conn = get_db()\
        user = conn.execute('SELECT id, username FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()\
        conn.close()\
        \
        if user:\
            session['user_id'] = user['id']\
            session['username'] = user['username']\
            return redirect('/')\
        else:\
            return render_template_string(LOGIN_PAGE, error='\uc0\u1053 \u1077 \u1074 \u1077 \u1088 \u1085 \u1099 \u1081  \u1083 \u1086 \u1075 \u1080 \u1085  \u1080 \u1083 \u1080  \u1087 \u1072 \u1088 \u1086 \u1083 \u1100 ')\
    \
    return render_template_string(LOGIN_PAGE, error=None)\
\
# --- \uc0\u1042 \u1067 \u1061 \u1054 \u1044  ---\
@app.route('/logout')\
def logout():\
    session.clear()\
    return redirect('/login')\
\
# --- HTML \uc0\u1064 \u1040 \u1041 \u1051 \u1054 \u1053 \u1067  ---\
MAIN_PAGE = '''\
<!DOCTYPE html>\
<html lang="ru">\
<head>\
    <meta charset="UTF-8">\
    <meta name="viewport" content="width=device-width, initial-scale=1.0">\
    <title>\uc0\u1052 \u1086 \u1081  \u1086 \u1088 \u1075 \u1072 \u1085 \u1072 \u1081 \u1079 \u1077 \u1088 </title>\
    <style>\
        * \{ margin: 0; padding: 0; box-sizing: border-box; \}\
        body \{\
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;\
            background: #f4f6f9;\
            padding: 16px;\
            min-height: 100vh;\
        \}\
        .app-container \{\
            display: flex;\
            gap: 16px;\
            max-width: 1400px;\
            margin: 0 auto;\
            align-items: flex-start;\
            flex-wrap: wrap;\
        \}\
        \
        /* \uc0\u1051 \u1077 \u1074 \u1072 \u1103  \u1082 \u1086 \u1083 \u1086 \u1085 \u1082 \u1072  */\
        .left-column \{\
            flex: 0 0 240px;\
            background: #f0f2f5;\
            border-radius: 14px;\
            padding: 18px 14px;\
            min-height: 400px;\
        \}\
        .left-column h2 \{ font-size: 15px; color: #6b7280; margin-bottom: 12px; \}\
        .backlog-item \{\
            background: white;\
            border-radius: 8px;\
            padding: 10px 12px;\
            margin-bottom: 8px;\
            display: flex;\
            justify-content: space-between;\
            align-items: center;\
            font-size: 14px;\
            border-left: 4px solid #9ca3af;\
        \}\
        .backlog-item .move-btn \{\
            background: none;\
            border: none;\
            color: #9ca3af;\
            cursor: pointer;\
            font-size: 16px;\
        \}\
        .backlog-item .move-btn:hover \{ color: #4361ee; \}\
        .backlog-add \{\
            display: flex;\
            gap: 6px;\
            margin-top: 10px;\
            flex-wrap: wrap;\
        \}\
        .backlog-add input \{\
            flex: 1;\
            padding: 8px 12px;\
            border: 1px solid #ddd;\
            border-radius: 8px;\
            font-size: 13px;\
            min-width: 100px;\
        \}\
        .backlog-add button \{\
            background: #4361ee;\
            color: white;\
            border: none;\
            border-radius: 8px;\
            padding: 8px 14px;\
            cursor: pointer;\
        \}\
        \
        /* \uc0\u1062 \u1077 \u1085 \u1090 \u1088  */\
        .center-column \{ flex: 1; min-width: 280px; \}\
        .header \{\
            background: white;\
            border-radius: 12px;\
            padding: 12px 20px;\
            margin-bottom: 16px;\
            display: flex;\
            justify-content: space-between;\
            align-items: center;\
            flex-wrap: wrap;\
            gap: 8px;\
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);\
        \}\
        .header h1 \{ font-size: 20px; color: #1a1a2e; \}\
        .header .user \{ color: #6b7280; font-size: 14px; \}\
        .header .btn-exit \{\
            background: #e74c3c;\
            color: white;\
            border: none;\
            padding: 6px 14px;\
            border-radius: 8px;\
            cursor: pointer;\
        \}\
        \
        /* \uc0\u1060 \u1086 \u1082 \u1091 \u1089  */\
        .focus-block \{\
            background: white;\
            border-radius: 14px;\
            padding: 18px 20px;\
            margin-bottom: 20px;\
            border: 2px solid #4361ee;\
            box-shadow: 0 2px 12px rgba(67,97,238,0.08);\
        \}\
        .focus-block .block-header \{\
            font-size: 18px;\
            font-weight: 700;\
            color: #1a1a2e;\
            margin-bottom: 12px;\
            display: flex;\
            justify-content: space-between;\
            align-items: center;\
        \}\
        .focus-block .block-header .count \{\
            font-size: 13px;\
            font-weight: 400;\
            color: #6b7280;\
            background: #f0f2f5;\
            padding: 2px 14px;\
            border-radius: 20px;\
        \}\
        .task-card \{\
            background: #f8f9fa;\
            border-radius: 10px;\
            padding: 10px 14px;\
            margin-bottom: 8px;\
            display: flex;\
            justify-content: space-between;\
            align-items: center;\
            flex-wrap: wrap;\
            gap: 6px;\
            border-left: 4px solid #ddd;\
            cursor: pointer;\
            transition: 0.2s;\
        \}\
        .task-card:hover \{ background: #f0f2f5; \}\
        .task-card .task-info \{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; \}\
        .task-card .task-meta \{ font-size: 11px; color: #6b7280; display: flex; align-items: center; gap: 6px; \}\
        .task-card .task-meta .repeat-icon \{ color: #f39c12; \}\
        .task-card .task-actions button \{\
            background: none;\
            border: none;\
            color: #9ca3af;\
            cursor: pointer;\
            font-size: 14px;\
            padding: 0 4px;\
        \}\
        .task-card .task-actions button:hover \{ color: #4361ee; \}\
        .task-card.tag-focus \{ border-left-color: #4361ee; \}\
        .task-card.tag-important \{ border-left-color: #e74c3c; \}\
        .task-card.tag-medium \{ border-left-color: #f39c12; \}\
        .task-card.tag-later \{ border-left-color: #9ca3af; \}\
        \
        .empty-block \{ color: #bbb; font-size: 13px; text-align: center; padding: 16px; \}\
        \
        /* \uc0\u1041 \u1083 \u1086 \u1082 \u1080  \u1089 \u1090 \u1086 \u1083 \u1073 \u1080 \u1082 \u1086 \u1084  */\
        .block-row \{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; \}\
        .block \{\
            background: white;\
            border-radius: 12px;\
            padding: 14px 16px;\
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);\
            min-height: 180px;\
        \}\
        .block .block-header \{\
            font-size: 14px;\
            font-weight: 600;\
            color: #1a1a2e;\
            margin-bottom: 10px;\
            padding-bottom: 8px;\
            border-bottom: 2px solid #f0f2f5;\
            display: flex;\
            justify-content: space-between;\
            align-items: center;\
        \}\
        .block .block-header .count \{\
            font-size: 11px;\
            font-weight: 400;\
            color: #9ca3af;\
            background: #f0f2f5;\
            padding: 2px 10px;\
            border-radius: 12px;\
        \}\
        .block-important .block-header \{ border-bottom-color: #e74c3c; \}\
        .block-medium .block-header \{ border-bottom-color: #f39c12; \}\
        .block-later .block-header \{ border-bottom-color: #9ca3af; \}\
        \
        /* \uc0\u1055 \u1088 \u1072 \u1074 \u1072 \u1103  \u1082 \u1086 \u1083 \u1086 \u1085 \u1082 \u1072  */\
        .right-column \{ flex: 0 0 160px; display: flex; flex-direction: column; gap: 12px; \}\
        .sidebar-card \{\
            background: white;\
            border-radius: 12px;\
            padding: 14px;\
            text-align: center;\
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);\
        \}\
        .sidebar-card .big-btn \{\
            background: #4361ee;\
            color: white;\
            border: none;\
            border-radius: 10px;\
            padding: 12px;\
            font-size: 15px;\
            font-weight: 600;\
            width: 100%;\
            cursor: pointer;\
        \}\
        .sidebar-card .big-btn:hover \{ background: #3a56d4; \}\
        \
        .quarter-popup \{\
            display: none;\
            background: white;\
            border-radius: 12px;\
            padding: 14px;\
            box-shadow: 0 8px 30px rgba(0,0,0,0.15);\
            margin-top: 10px;\
            text-align: left;\
        \}\
        .quarter-popup.open \{ display: block; animation: fade 0.3s ease; \}\
        @keyframes fade \{ from \{ opacity: 0; transform: translateY(-6px); \} to \{ opacity: 1; transform: translateY(0); \} \}\
        .quarter-popup h4 \{ font-size: 13px; margin-bottom: 10px; \}\
        .quarter-popup .q-btn \{\
            display: block;\
            width: 100%;\
            padding: 8px;\
            margin-bottom: 6px;\
            border: 1px solid #e9ecef;\
            border-radius: 8px;\
            background: white;\
            font-size: 13px;\
            cursor: pointer;\
            text-align: left;\
        \}\
        .quarter-popup .q-btn:hover \{ border-color: #4361ee; background: #f8f9ff; \}\
        .quarter-popup .close-popup \{\
            background: none;\
            border: none;\
            color: #9ca3af;\
            font-size: 18px;\
            float: right;\
            cursor: pointer;\
        \}\
        \
        /* \uc0\u1052 \u1086 \u1076 \u1072 \u1083 \u1082 \u1072  */\
        .modal-overlay \{\
            display: none;\
            position: fixed;\
            top: 0; left: 0; width: 100%; height: 100%;\
            background: rgba(0,0,0,0.3);\
            backdrop-filter: blur(4px);\
            z-index: 999;\
            justify-content: center;\
            align-items: center;\
        \}\
        .modal-overlay.open \{ display: flex; \}\
        .modal \{\
            background: white;\
            border-radius: 18px;\
            padding: 24px 28px;\
            max-width: 420px;\
            width: 90%;\
            box-shadow: 0 20px 60px rgba(0,0,0,0.2);\
        \}\
        .modal h3 \{ font-size: 18px; margin-bottom: 4px; \}\
        .modal .sub \{ font-size: 13px; color: #6b7280; margin-bottom: 16px; \}\
        .modal label \{ font-size: 12px; font-weight: 600; color: #1a1a2e; display: block; margin-top: 12px; margin-bottom: 4px; \}\
        .modal input, .modal select \{\
            width: 100%;\
            padding: 8px 12px;\
            border: 1.5px solid #ddd;\
            border-radius: 8px;\
            font-size: 14px;\
        \}\
        .modal input:focus, .modal select:focus \{ outline: none; border-color: #4361ee; \}\
        .modal .checkbox-group \{\
            display: flex;\
            align-items: center;\
            gap: 8px;\
            margin-top: 12px;\
        \}\
        .modal .checkbox-group input[type="checkbox"] \{ width: 18px; height: 18px; accent-color: #4361ee; \}\
        .modal .checkbox-group label \{ margin: 0; font-weight: 400; font-size: 14px; \}\
        .modal .repeat-options \{\
            display: none;\
            margin-top: 8px;\
            padding: 12px;\
            background: #f8f9fa;\
            border-radius: 8px;\
        \}\
        .modal .repeat-options.visible \{ display: block; \}\
        .modal .modal-actions \{ display: flex; gap: 10px; margin-top: 18px; \}\
        .modal .modal-actions button \{ flex: 1; padding: 10px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; \}\
        .modal .btn-save \{ background: #4361ee; color: white; \}\
        .modal .btn-cancel \{ background: #f0f2f5; color: #6b7280; \}\
        \
        @media (max-width: 1024px) \{\
            .left-column \{ flex: 1 1 100%; \}\
            .right-column \{ flex: 1 1 100%; flex-direction: row; \}\
            .right-column .sidebar-card \{ flex: 1; \}\
            .block-row \{ grid-template-columns: 1fr 1fr; \}\
        \}\
        @media (max-width: 600px) \{\
            .block-row \{ grid-template-columns: 1fr; \}\
            .header \{ flex-direction: column; text-align: center; \}\
        \}\
    </style>\
</head>\
<body>\
<div class="app-container">\
\
    <!-- \uc0\u1051 \u1077 \u1074 \u1072 \u1103  \u1082 \u1086 \u1083 \u1086 \u1085 \u1082 \u1072  -->\
    <div class="left-column">\
        <h2>\uc0\u55357 \u56549  \u1056 \u1072 \u1089 \u1087 \u1088 \u1077 \u1076 \u1077 \u1083 \u1080 \u1090 \u1100 </h2>\
        <div id="backlogList"></div>\
        <div class="backlog-add">\
            <input type="text" id="newTaskInput" placeholder="\uc0\u1053 \u1086 \u1074 \u1072 \u1103  \u1079 \u1072 \u1076 \u1072 \u1095 \u1072 ...">\
            <button id="addBacklogBtn">+</button>\
        </div>\
        <p style="font-size:11px; color:#bbb; margin-top:8px;">\uc0\u11013 \u65039  \u1053 \u1072 \u1078 \u1084 \u1080 \u1090 \u1077  \u8594  \u1095 \u1090 \u1086 \u1073 \u1099  \u1088 \u1072 \u1089 \u1087 \u1088 \u1077 \u1076 \u1077 \u1083 \u1080 \u1090 \u1100 </p>\
    </div>\
\
    <!-- \uc0\u1062 \u1077 \u1085 \u1090 \u1088  -->\
    <div class="center-column">\
        <div class="header">\
            <h1>\uc0\u55357 \u56523  \u1052 \u1086 \u1080  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080 </h1>\
            <div>\
                <span class="user">\uc0\u55357 \u56420  \{\{ username \}\}</span>\
                <a href="/logout" class="btn-exit" style="text-decoration:none; display:inline-block; margin-left:10px;">\uc0\u1042 \u1099 \u1081 \u1090 \u1080 </a>\
            </div>\
        </div>\
\
        <!-- \uc0\u1060 \u1086 \u1082 \u1091 \u1089  -->\
        <div class="focus-block" id="focusBlock">\
            <div class="block-header">\
                \uc0\u55356 \u57263  \u1060 \u1086 \u1082 \u1091 \u1089 \
                <span class="count" id="focusCount">0</span>\
            </div>\
            <div id="focusTasks"></div>\
            <div class="empty-block" id="focusEmpty">+ \uc0\u1044 \u1086 \u1073 \u1072 \u1074 \u1080 \u1090 \u1100  \u1079 \u1072 \u1076 \u1072 \u1095 \u1091  \u1074  \u1092 \u1086 \u1082 \u1091 \u1089 </div>\
        </div>\
\
        <!-- \uc0\u1041 \u1083 \u1086 \u1082 \u1080  \u1089 \u1090 \u1086 \u1083 \u1073 \u1080 \u1082 \u1086 \u1084  -->\
        <div class="block-row">\
            <div class="block block-important" id="importantBlock">\
                <div class="block-header">\uc0\u55357 \u56613  \u1042 \u1072 \u1078 \u1085 \u1086 \u1077  <span class="count" id="importantCount">0</span></div>\
                <div id="importantTasks"></div>\
                <div class="empty-block" id="importantEmpty">+ \uc0\u1044 \u1086 \u1073 \u1072 \u1074 \u1080 \u1090 \u1100  \u1079 \u1072 \u1076 \u1072 \u1095 \u1091 </div>\
            </div>\
            <div class="block block-medium" id="mediumBlock">\
                <div class="block-header">\uc0\u9878 \u65039  \u1057 \u1088 \u1077 \u1076 \u1085 \u1077 \u1077  <span class="count" id="mediumCount">0</span></div>\
                <div id="mediumTasks"></div>\
                <div class="empty-block" id="mediumEmpty">+ \uc0\u1044 \u1086 \u1073 \u1072 \u1074 \u1080 \u1090 \u1100  \u1079 \u1072 \u1076 \u1072 \u1095 \u1091 </div>\
            </div>\
            <div class="block block-later" id="laterBlock">\
                <div class="block-header">\uc0\u55357 \u56688 \u65039  \u1055 \u1086 \u1079 \u1078 \u1077  <span class="count" id="laterCount">0</span></div>\
                <div id="laterTasks"></div>\
                <div class="empty-block" id="laterEmpty">+ \uc0\u1044 \u1086 \u1073 \u1072 \u1074 \u1080 \u1090 \u1100  \u1079 \u1072 \u1076 \u1072 \u1095 \u1091 </div>\
            </div>\
        </div>\
    </div>\
\
    <!-- \uc0\u1055 \u1088 \u1072 \u1074 \u1072 \u1103  \u1082 \u1086 \u1083 \u1086 \u1085 \u1082 \u1072  -->\
    <div class="right-column">\
        <div class="sidebar-card">\
            <button class="big-btn" id="toggleQuartersBtn">\uc0\u55357 \u56787 \u65039  3 \u1084 \u1077 \u1089 \u1103 \u1094 \u1072 </button>\
            <div class="quarter-popup" id="quarterPopup">\
                <button class="close-popup" id="closePopupBtn">\uc0\u10005 </button>\
                <h4>\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1082 \u1074 \u1072 \u1088 \u1090 \u1072 \u1083 </h4>\
                <button class="q-btn">\uc0\u10052 \u65039  \u1071 \u1085 \u1074 \u1072 \u1088 \u1100  \'96 \u1052 \u1072 \u1088 \u1090  <span class="q-sub" style="font-size:11px;color:#888;display:block;">2026</span></button>\
                <button class="q-btn">\uc0\u55356 \u57144  \u1040 \u1087 \u1088 \u1077 \u1083 \u1100  \'96 \u1048 \u1102 \u1085 \u1100  <span class="q-sub" style="font-size:11px;color:#888;display:block;">2026</span></button>\
                <button class="q-btn">\uc0\u9728 \u65039  \u1048 \u1102 \u1083 \u1100  \'96 \u1057 \u1077 \u1085 \u1090 \u1103 \u1073 \u1088 \u1100  <span class="q-sub" style="font-size:11px;color:#888;display:block;">2026</span></button>\
                <button class="q-btn">\uc0\u55356 \u57154  \u1054 \u1082 \u1090 \u1103 \u1073 \u1088 \u1100  \'96 \u1044 \u1077 \u1082 \u1072 \u1073 \u1088 \u1100  <span class="q-sub" style="font-size:11px;color:#888;display:block;">2026</span></button>\
            </div>\
        </div>\
    </div>\
</div>\
\
<!-- \uc0\u1052 \u1086 \u1076 \u1072 \u1083 \u1082 \u1072  -->\
<div class="modal-overlay" id="editModal">\
    <div class="modal">\
        <h3>\uc0\u9999 \u65039  \u1056 \u1077 \u1076 \u1072 \u1082 \u1090 \u1080 \u1088 \u1086 \u1074 \u1072 \u1090 \u1100  \u1079 \u1072 \u1076 \u1072 \u1095 \u1091 </h3>\
        <p class="sub">\uc0\u1048 \u1079 \u1084 \u1077 \u1085 \u1080 \u1090 \u1077  \u1085 \u1072 \u1079 \u1074 \u1072 \u1085 \u1080 \u1077 , \u1076 \u1072 \u1090 \u1091  \u1080 \u1083 \u1080  \u1082 \u1072 \u1090 \u1077 \u1075 \u1086 \u1088 \u1080 \u1102 </p>\
        <input type="hidden" id="editTaskId">\
        <label for="editTaskTitle">\uc0\u1053 \u1072 \u1079 \u1074 \u1072 \u1085 \u1080 \u1077 </label>\
        <input type="text" id="editTaskTitle">\
        <label for="editTaskDate">\uc0\u1044 \u1072 \u1090 \u1072  \u1074 \u1099 \u1087 \u1086 \u1083 \u1085 \u1077 \u1085 \u1080 \u1103 </label>\
        <input type="date" id="editTaskDate">\
        <label for="editTaskCategory">\uc0\u1050 \u1072 \u1090 \u1077 \u1075 \u1086 \u1088 \u1080 \u1103 </label>\
        <select id="editTaskCategory">\
            <option value="focus">\uc0\u55356 \u57263  \u1060 \u1086 \u1082 \u1091 \u1089 </option>\
            <option value="important">\uc0\u55357 \u56613  \u1042 \u1072 \u1078 \u1085 \u1086 \u1077 </option>\
            <option value="medium">\uc0\u9878 \u65039  \u1057 \u1088 \u1077 \u1076 \u1085 \u1077 \u1077 </option>\
            <option value="later">\uc0\u55357 \u56688 \u65039  \u1055 \u1086 \u1079 \u1078 \u1077 </option>\
        </select>\
        <div class="checkbox-group">\
            <input type="checkbox" id="editTaskRepeat">\
            <label for="editTaskRepeat">\uc0\u55357 \u56580  \u1055 \u1086 \u1074 \u1090 \u1086 \u1088 \u1103 \u1102 \u1097 \u1072 \u1103 \u1089 \u1103  \u1079 \u1072 \u1076 \u1072 \u1095 \u1072 </label>\
        </div>\
        <div class="repeat-options" id="repeatOptions">\
            <label for="editRepeatType">\uc0\u1058 \u1080 \u1087  \u1087 \u1086 \u1074 \u1090 \u1086 \u1088 \u1077 \u1085 \u1080 \u1103 </label>\
            <select id="editRepeatType">\
                <option value="daily">\uc0\u55357 \u56518  \u1045 \u1078 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 </option>\
                <option value="weekly">\uc0\u55357 \u56517  \u1045 \u1078 \u1077 \u1085 \u1077 \u1076 \u1077 \u1083 \u1100 \u1085 \u1086 </option>\
            </select>\
            <div id="weeklyDayGroup" style="margin-top:8px; display:none;">\
                <label for="editRepeatDay">\uc0\u1044 \u1077 \u1085 \u1100  \u1085 \u1077 \u1076 \u1077 \u1083 \u1080 </label>\
                <select id="editRepeatDay">\
                    <option value="0">\uc0\u1042 \u1086 \u1089 \u1082 \u1088 \u1077 \u1089 \u1077 \u1085 \u1100 \u1077 </option>\
                    <option value="1">\uc0\u1055 \u1086 \u1085 \u1077 \u1076 \u1077 \u1083 \u1100 \u1085 \u1080 \u1082 </option>\
                    <option value="2">\uc0\u1042 \u1090 \u1086 \u1088 \u1085 \u1080 \u1082 </option>\
                    <option value="3">\uc0\u1057 \u1088 \u1077 \u1076 \u1072 </option>\
                    <option value="4">\uc0\u1063 \u1077 \u1090 \u1074 \u1077 \u1088 \u1075 </option>\
                    <option value="5">\uc0\u1055 \u1103 \u1090 \u1085 \u1080 \u1094 \u1072 </option>\
                    <option value="6">\uc0\u1057 \u1091 \u1073 \u1073 \u1086 \u1090 \u1072 </option>\
                </select>\
            </div>\
        </div>\
        <div class="modal-actions">\
            <button class="btn-save" id="saveEditBtn">\uc0\u55357 \u56510  \u1057 \u1086 \u1093 \u1088 \u1072 \u1085 \u1080 \u1090 \u1100 </button>\
            <button class="btn-cancel" id="cancelEditBtn">\uc0\u1054 \u1090 \u1084 \u1077 \u1085 \u1072 </button>\
        </div>\
    </div>\
</div>\
\
<script>\
    let currentTaskId = null;\
    \
    // --- \uc0\u1047 \u1072 \u1075 \u1088 \u1091 \u1079 \u1082 \u1072  \u1079 \u1072 \u1076 \u1072 \u1095  ---\
    function loadTasks() \{\
        fetch('/api/tasks')\
            .then(res => res.json())\
            .then(tasks => \{\
                const categories = \{ focus: [], important: [], medium: [], later: [] \};\
                tasks.forEach(t => \{\
                    if (categories[t.category]) categories[t.category].push(t);\
                    else categories.later.push(t);\
                \});\
                renderTasks(categories);\
            \});\
    \}\
    \
    function renderTasks(categories) \{\
        const containerMap = \{\
            focus: \{ tasks: 'focusTasks', count: 'focusCount', empty: 'focusEmpty' \},\
            important: \{ tasks: 'importantTasks', count: 'importantCount', empty: 'importantEmpty' \},\
            medium: \{ tasks: 'mediumTasks', count: 'mediumCount', empty: 'mediumEmpty' \},\
            later: \{ tasks: 'laterTasks', count: 'laterCount', empty: 'laterEmpty' \}\
        \};\
        \
        for (const [cat, data] of Object.entries(containerMap)) \{\
            const tasks = categories[cat] || [];\
            const container = document.getElementById(data.tasks);\
            const countEl = document.getElementById(data.count);\
            const emptyEl = document.getElementById(data.empty);\
            \
            container.innerHTML = '';\
            tasks.forEach(task => \{\
                const card = createTaskCard(task);\
                container.appendChild(card);\
            \});\
            \
            countEl.textContent = tasks.length;\
            if (emptyEl) \{\
                emptyEl.style.display = tasks.length === 0 ? 'block' : 'none';\
            \}\
        \}\
    \}\
    \
    function createTaskCard(task) \{\
        const div = document.createElement('div');\
        div.className = `task-card tag-$\{task.category || 'later'\}`;\
        div.dataset.taskId = task.id;\
        \
        let metaHTML = '';\
        if (task.date) \{\
            const d = new Date(task.date + 'T00:00:00');\
            metaHTML += `\uc0\u55357 \u56517  $\{d.toLocaleDateString('ru-RU')\}`;\
        \}\
        if (task.repeat_type && task.repeat_type !== 'none') \{\
            let label = '';\
            if (task.repeat_type === 'daily') label = '\uc0\u55357 \u56580  \u1077 \u1078 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 ';\
            else if (task.repeat_type === 'weekly') \{\
                const days = ['\uc0\u1074 \u1089 ','\u1087 \u1085 ','\u1074 \u1090 ','\u1089 \u1088 ','\u1095 \u1090 ','\u1087 \u1090 ','\u1089 \u1073 '];\
                label = `\uc0\u55357 \u56580  \u1077 \u1078 \u1077 \u1085 \u1077 \u1076 \u1077 \u1083 \u1100 \u1085 \u1086  ($\{days[task.repeat_day || 0]\})`;\
            \}\
            if (metaHTML) metaHTML += ' ';\
            metaHTML += `<span class="repeat-icon">$\{label\}</span>`;\
        \}\
        \
        div.innerHTML = `\
            <div class="task-info">\
                <span>$\{task.title\}</span>\
                $\{metaHTML ? `<span class="task-meta">$\{metaHTML\}</span>` : ''\}\
            </div>\
            <div class="task-actions">\
                <button class="edit-btn" title="\uc0\u1056 \u1077 \u1076 \u1072 \u1082 \u1090 \u1080 \u1088 \u1086 \u1074 \u1072 \u1090 \u1100 ">\u9999 \u65039 </button>\
                <button class="done-btn" title="\uc0\u1042 \u1099 \u1087 \u1086 \u1083 \u1085 \u1077 \u1085 \u1086 ">\u9989 </button>\
                <button class="delete-btn" title="\uc0\u1059 \u1076 \u1072 \u1083 \u1080 \u1090 \u1100 ">\u55357 \u56785 \u65039 </button>\
            </div>\
        `;\
        \
        div.querySelector('.edit-btn').addEventListener('click', (e) => \{\
            e.stopPropagation();\
            openEditModal(task);\
        \});\
        \
        div.querySelector('.done-btn').addEventListener('click', (e) => \{\
            e.stopPropagation();\
            fetch(`/api/task/$\{task.id\}/done`, \{ method: 'POST' \})\
                .then(() => loadTasks());\
        \});\
        \
        div.querySelector('.delete-btn').addEventListener('click', (e) => \{\
            e.stopPropagation();\
            if (confirm('\uc0\u1059 \u1076 \u1072 \u1083 \u1080 \u1090 \u1100  \u1079 \u1072 \u1076 \u1072 \u1095 \u1091 ?')) \{\
                fetch(`/api/task/$\{task.id\}`, \{ method: 'DELETE' \})\
                    .then(() => loadTasks());\
            \}\
        \});\
        \
        return div;\
    \}\
    \
    // --- \uc0\u1052 \u1086 \u1076 \u1072 \u1083 \u1082 \u1072  \u1088 \u1077 \u1076 \u1072 \u1082 \u1090 \u1080 \u1088 \u1086 \u1074 \u1072 \u1085 \u1080 \u1103  ---\
    function openEditModal(task) \{\
        currentTaskId = task.id;\
        document.getElementById('editTaskId').value = task.id;\
        document.getElementById('editTaskTitle').value = task.title;\
        document.getElementById('editTaskDate').value = task.date || '';\
        document.getElementById('editTaskCategory').value = task.category || 'later';\
        \
        const isRepeating = task.repeat_type && task.repeat_type !== 'none';\
        document.getElementById('editTaskRepeat').checked = isRepeating;\
        \
        const repeatOptions = document.getElementById('repeatOptions');\
        if (isRepeating) \{\
            repeatOptions.classList.add('visible');\
            document.getElementById('editRepeatType').value = task.repeat_type || 'daily';\
            if (task.repeat_type === 'weekly') \{\
                document.getElementById('weeklyDayGroup').style.display = 'block';\
                document.getElementById('editRepeatDay').value = task.repeat_day || 0;\
            \} else \{\
                document.getElementById('weeklyDayGroup').style.display = 'none';\
            \}\
        \} else \{\
            repeatOptions.classList.remove('visible');\
            document.getElementById('weeklyDayGroup').style.display = 'none';\
        \}\
        \
        document.getElementById('editModal').classList.add('open');\
    \}\
    \
    document.getElementById('saveEditBtn').addEventListener('click', () => \{\
        const taskId = document.getElementById('editTaskId').value;\
        const title = document.getElementById('editTaskTitle').value.trim();\
        const date = document.getElementById('editTaskDate').value;\
        const category = document.getElementById('editTaskCategory').value;\
        const isRepeating = document.getElementById('editTaskRepeat').checked;\
        let repeatType = 'none';\
        let repeatDay = null;\
        \
        if (isRepeating) \{\
            repeatType = document.getElementById('editRepeatType').value;\
            if (repeatType === 'weekly') \{\
                repeatDay = parseInt(document.getElementById('editRepeatDay').value);\
            \}\
        \}\
        \
        if (!title) \{ alert('\uc0\u1042 \u1074 \u1077 \u1076 \u1080 \u1090 \u1077  \u1085 \u1072 \u1079 \u1074 \u1072 \u1085 \u1080 \u1077 '); return; \}\
        \
        fetch(`/api/task/$\{taskId\}`, \{\
            method: 'PUT',\
            headers: \{ 'Content-Type': 'application/json' \},\
            body: JSON.stringify(\{ title, date, category, repeat_type: repeatType, repeat_day: repeatDay \})\
        \})\
        .then(res => res.json())\
        .then(() => \{\
            document.getElementById('editModal').classList.remove('open');\
            loadTasks();\
        \});\
    \});\
    \
    document.getElementById('cancelEditBtn').addEventListener('click', () => \{\
        document.getElementById('editModal').classList.remove('open');\
    \});\
    \
    // --- \uc0\u1044 \u1086 \u1073 \u1072 \u1074 \u1083 \u1077 \u1085 \u1080 \u1077  \u1079 \u1072 \u1076 \u1072 \u1095 \u1080  \u1074  \u1073 \u1101 \u1082 \u1083 \u1086 \u1075  ---\
    document.getElementById('addBacklogBtn').addEventListener('click', () => \{\
        const input = document.getElementById('newTaskInput');\
        const title = input.value.trim();\
        if (!title) return;\
        \
        fetch('/api/task', \{\
            method: 'POST',\
            headers: \{ 'Content-Type': 'application/json' \},\
            body: JSON.stringify(\{ title \})\
        \})\
        .then(res => res.json())\
        .then(() => \{\
            input.value = '';\
            loadTasks();\
        \});\
    \});\
    \
    document.getElementById('newTaskInput').addEventListener('keypress', (e) => \{\
        if (e.key === 'Enter') \{\
            document.getElementById('addBacklogBtn').click();\
        \}\
    \});\
    \
    // --- \uc0\u1050 \u1074 \u1072 \u1088 \u1090 \u1072 \u1083 \u1099  ---\
    document.getElementById('toggleQuartersBtn').addEventListener('click', () => \{\
        document.getElementById('quarterPopup').classList.toggle('open');\
    \});\
    \
    document.getElementById('closePopupBtn').addEventListener('click', () => \{\
        document.getElementById('quarterPopup').classList.remove('open');\
    \});\
    \
    document.querySelectorAll('.q-btn').forEach(btn => \{\
        btn.addEventListener('click', function() \{\
            alert('\uc0\u55357 \u56517  \u1042 \u1099  \u1074 \u1099 \u1073 \u1088 \u1072 \u1083 \u1080 : ' + this.textContent.trim());\
            document.getElementById('quarterPopup').classList.remove('open');\
        \});\
    \});\
    \
    // --- \uc0\u1047 \u1072 \u1075 \u1088 \u1091 \u1079 \u1082 \u1072  \u1087 \u1088 \u1080  \u1089 \u1090 \u1072 \u1088 \u1090 \u1077  ---\
    loadTasks();\
</script>\
</body>\
</html>\
'''\
\
REGISTER_PAGE = '''\
<!DOCTYPE html>\
<html>\
<head>\
    <meta charset="UTF-8">\
    <meta name="viewport" content="width=device-width, initial-scale=1.0">\
    <title>\uc0\u1056 \u1077 \u1075 \u1080 \u1089 \u1090 \u1088 \u1072 \u1094 \u1080 \u1103 </title>\
    <style>\
        body \{ font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f4f6f9; margin: 0; \}\
        .card \{ background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); width: 100%; max-width: 360px; \}\
        h2 \{ margin-bottom: 20px; color: #1a1a2e; \}\
        input \{ width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; \}\
        input:focus \{ outline: none; border-color: #4361ee; \}\
        button \{ width: 100%; padding: 12px; background: #4361ee; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; \}\
        button:hover \{ background: #3a56d4; \}\
        .error \{ color: #e74c3c; font-size: 14px; margin-bottom: 10px; \}\
        .link \{ text-align: center; margin-top: 16px; font-size: 14px; color: #6b7280; \}\
        .link a \{ color: #4361ee; text-decoration: none; \}\
    </style>\
</head>\
<body>\
    <div class="card">\
        <h2>\uc0\u55357 \u56541  \u1056 \u1077 \u1075 \u1080 \u1089 \u1090 \u1088 \u1072 \u1094 \u1080 \u1103 </h2>\
        \{% if error %\}\
            <div class="error">\{\{ error \}\}</div>\
        \{% endif %\}\
        <form method="POST">\
            <input type="text" name="username" placeholder="\uc0\u1051 \u1086 \u1075 \u1080 \u1085 " required>\
            <input type="password" name="password" placeholder="\uc0\u1055 \u1072 \u1088 \u1086 \u1083 \u1100 " required>\
            <button type="submit">\uc0\u1047 \u1072 \u1088 \u1077 \u1075 \u1080 \u1089 \u1090 \u1088 \u1080 \u1088 \u1086 \u1074 \u1072 \u1090 \u1100 \u1089 \u1103 </button>\
        </form>\
        <div class="link">\uc0\u1059 \u1078 \u1077  \u1077 \u1089 \u1090 \u1100  \u1072 \u1082 \u1082 \u1072 \u1091 \u1085 \u1090 ? <a href="/login">\u1042 \u1086 \u1081 \u1090 \u1080 </a></div>\
    </div>\
</body>\
</html>\
'''\
\
LOGIN_PAGE = '''\
<!DOCTYPE html>\
<html>\
<head>\
    <meta charset="UTF-8">\
    <meta name="viewport" content="width=device-width, initial-scale=1.0">\
    <title>\uc0\u1042 \u1093 \u1086 \u1076 </title>\
    <style>\
        body \{ font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f4f6f9; margin: 0; \}\
        .card \{ background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); width: 100%; max-width: 360px; \}\
        h2 \{ margin-bottom: 20px; color: #1a1a2e; \}\
        input \{ width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; \}\
        input:focus \{ outline: none; border-color: #4361ee; \}\
        button \{ width: 100%; padding: 12px; background: #4361ee; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; \}\
        button:hover \{ background: #3a56d4; \}\
        .error \{ color: #e74c3c; font-size: 14px; margin-bottom: 10px; \}\
        .link \{ text-align: center; margin-top: 16px; font-size: 14px; color: #6b7280; \}\
        .link a \{ color: #4361ee; text-decoration: none; \}\
    </style>\
</head>\
<body>\
    <div class="card">\
        <h2>\uc0\u55357 \u56593  \u1042 \u1093 \u1086 \u1076 </h2>\
        \{% if error %\}\
            <div class="error">\{\{ error \}\}</div>\
        \{% endif %\}\
        <form method="POST">\
            <input type="text" name="username" placeholder="\uc0\u1051 \u1086 \u1075 \u1080 \u1085 " required>\
            <input type="password" name="password" placeholder="\uc0\u1055 \u1072 \u1088 \u1086 \u1083 \u1100 " required>\
            <button type="submit">\uc0\u1042 \u1086 \u1081 \u1090 \u1080 </button>\
        </form>\
        <div class="link">\uc0\u1053 \u1077 \u1090  \u1072 \u1082 \u1082 \u1072 \u1091 \u1085 \u1090 \u1072 ? <a href="/register">\u1047 \u1072 \u1088 \u1077 \u1075 \u1080 \u1089 \u1090 \u1088 \u1080 \u1088 \u1086 \u1074 \u1072 \u1090 \u1100 \u1089 \u1103 </a></div>\
    </div>\
</body>\
</html>\
'''\
\
# --- \uc0\u1047 \u1040 \u1055 \u1059 \u1057 \u1050  \u1044 \u1051 \u1071  RENDER ---\
if __name__ == '__main__':\
    port = int(os.environ.get('PORT', 5000))\
    app.run(host='0.0.0.0', port=port)}