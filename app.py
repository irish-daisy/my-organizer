from flask import Flask, request, render_template_string, redirect, session, url_for, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
from datetime import datetime, timedelta
import json
import os

app = Flask(__name__)
app.secret_key = 'секретный_ключ_для_сессий_12345'

# --- ПОДКЛЮЧЕНИЕ К POSTGRESQL ---
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        database_url = "postgresql://organizer_user:пароль@localhost:5432/organizer"
    conn = psycopg2.connect(database_url)
    return conn

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ---
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Таблица пользователей
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    # Таблица задач
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            category TEXT DEFAULT 'later',
            date TEXT,
            repeat_type TEXT DEFAULT 'none',
            repeat_day INTEGER,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            quarter TEXT,
            sphere TEXT
        )
    ''')
    
    # Таблица сфер для кварталов
    cur.execute('''
        CREATE TABLE IF NOT EXISTS spheres (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            quarter TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user_id():
    return session.get('user_id')

def get_current_quarter():
    """Возвращает текущий квартал в формате Q1, Q2, Q3, Q4"""
    now = datetime.now()
    month = now.month
    if month in [1, 2, 3]:
        return 'Q1'
    elif month in [4, 5, 6]:
        return 'Q2'
    elif month in [7, 8, 9]:
        return 'Q3'
    else:
        return 'Q4'

def get_quarter_name(quarter):
    names = {
        'Q1': 'Январь – Март',
        'Q2': 'Апрель – Июнь',
        'Q3': 'Июль – Сентябрь',
        'Q4': 'Октябрь – Декабрь'
    }
    return names.get(quarter, quarter)

def get_quarter_year(quarter):
    """Возвращает год для квартала"""
    now = datetime.now()
    year = now.year
    # Если квартал Q4 и сейчас Q1, то год -1
    if quarter == 'Q4' and now.month in [1, 2, 3]:
        return year - 1
    return year

# --- ГЛАВНАЯ СТРАНИЦА ---
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Получаем задачи, которые НЕ привязаны к кварталам (или привязаны к пустому)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND (quarter IS NULL OR quarter = '') 
        ORDER BY date ASC
    ''', (user_id, 'active'))
    tasks = cur.fetchall()
    conn.close()
    
    categories = {
        'work': [],
        'home': [],
        'personal': [],
        'later': []
    }
    
    for task in tasks:
        cat = task['category'] if task['category'] in categories else 'later'
        categories[cat].append(dict(task))
    
    return render_template_string(MAIN_PAGE, 
                                   work_tasks=categories['work'],
                                   home_tasks=categories['home'],
                                   personal_tasks=categories['personal'],
                                   later_tasks=categories['later'],
                                   username=session.get('username', 'Пользователь'))

# --- КВАРТАЛЫ (3 МЕСЯЦА) ---
@app.route('/quarter/<quarter>')
def quarter_page(quarter):
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Получаем сферы для этого квартала
    cur.execute('SELECT * FROM spheres WHERE user_id = %s AND quarter = %s ORDER BY created_at ASC', (user_id, quarter))
    spheres = cur.fetchall()
    
    # Для каждой сферы получаем задачи
    for sphere in spheres:
        cur.execute('SELECT * FROM tasks WHERE user_id = %s AND sphere = %s AND quarter = %s AND status = %s ORDER BY date ASC', 
                   (user_id, sphere['name'], quarter, 'active'))
        sphere['tasks'] = cur.fetchall()
    
    conn.close()
    
    # Получаем все кварталы для навигации
    quarters = ['Q1', 'Q2', 'Q3', 'Q4']
    current_year = datetime.now().year
    current_q = get_current_quarter()
    
    quarter_data = []
    for q in quarters:
        q_year = get_quarter_year(q)
        q_name = get_quarter_name(q)
        is_current = (q == current_q)
        quarter_data.append({
            'id': q,
            'name': q_name,
            'year': q_year,
            'current': is_current
        })
    
    return render_template_string(QUARTER_PAGE, 
                                   quarter=quarter,
                                   quarter_name=get_quarter_name(quarter),
                                   quarter_year=get_quarter_year(quarter),
                                   quarters=quarter_data,
                                   spheres=spheres,
                                   username=session.get('username', 'Пользователь'),
                                   current_quarter=current_q)

# --- СТРАНИЦА "ПОЗЖЕ" ---
@app.route('/later')
def later_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Получаем все задачи со статусом "later" (позже)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND category = %s AND status = %s
        ORDER BY created_at DESC
    ''', (user_id, 'later', 'active'))
    tasks = cur.fetchall()
    conn.close()
    
    return render_template_string(LATER_PAGE, 
                                   tasks=tasks,
                                   username=session.get('username', 'Пользователь'))

# --- API: Добавить задачу в "Позже" ---
@app.route('/api/task/later', methods=['POST'])
def add_later_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    title = data.get('title', '').strip()
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', 'none', None, 'active'))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added to later'})

# --- API: Добавить сферу в квартал ---
@app.route('/api/sphere', methods=['POST'])
def add_sphere():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    name = data.get('name', '').strip()
    quarter = data.get('quarter', '')
    
    if not name or not quarter:
        return jsonify({'error': 'Name and quarter are required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO spheres (user_id, name, quarter) VALUES (%s, %s, %s)', 
               (session['user_id'], name, quarter))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Sphere added'})

# --- API: Добавить задачу в сферу (квартал) ---
@app.route('/api/task/quarter', methods=['POST'])
def add_quarter_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    title = data.get('title', '').strip()
    sphere = data.get('sphere', '')
    quarter = data.get('quarter', '')
    date = data.get('date', '')
    
    if not title or not sphere or not quarter:
        return jsonify({'error': 'Title, sphere and quarter are required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, status, quarter, sphere)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', date, 'active', quarter, sphere))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added to quarter'})

# --- API: Добавить задачу напрямую в категорию (с главной страницы) ---
@app.route('/api/task/direct', methods=['POST'])
def add_direct_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    title = data.get('title', '').strip()
    category = data.get('category', 'later')
    date = data.get('date', '')
    repeat_type = data.get('repeat_type', 'none')
    repeat_day = data.get('repeat_day')
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, category, date, repeat_type, repeat_day, 'active'))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added'})

# --- API: Добавить задачу в бэклог (Распределить) ---
@app.route('/api/task', methods=['POST'])
def add_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    title = data.get('title', '').strip()
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', 'none', None, 'active'))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added'})

# --- API: Обновление задачи ---
@app.route('/api/task/<int:task_id>', methods=['PUT'])
def update_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    title = data.get('title', '').strip()
    category = data.get('category', 'later')
    date = data.get('date', '')
    repeat_type = data.get('repeat_type', 'none')
    repeat_day = data.get('repeat_day')
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        UPDATE tasks SET 
            title = %s, category = %s, date = %s, repeat_type = %s, repeat_day = %s
        WHERE id = %s AND user_id = %s
    ''', (title, category, date, repeat_type, repeat_day, task_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task updated'})

# --- API: Удаление задачи ---
@app.route('/api/task/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE id = %s AND user_id = %s', (task_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task deleted'})

# --- API: Выполнение задачи ---
@app.route('/api/task/<int:task_id>/done', methods=['POST'])
def done_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute('SELECT * FROM tasks WHERE id = %s AND user_id = %s', (task_id, session['user_id']))
    task = cur.fetchone()
    
    if not task:
        conn.close()
        return jsonify({'error': 'Task not found'}), 404
    
    if task['repeat_type'] != 'none':
        new_date = None
        if task['repeat_type'] == 'daily':
            new_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        elif task['repeat_type'] == 'weekly' and task['repeat_day'] is not None:
            today = datetime.now()
            days_ahead = task['repeat_day'] - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            new_date = (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        
        cur.execute('''
            INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status, quarter, sphere)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (task['user_id'], task['title'], task['category'], new_date, task['repeat_type'], task['repeat_day'], 'active', task.get('quarter'), task.get('sphere')))
    
    cur.execute('UPDATE tasks SET status = %s WHERE id = %s', ('done', task_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task done'})

# --- API: Получить все задачи ---
@app.route('/api/tasks')
def get_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND (quarter IS NULL OR quarter = '')
        ORDER BY date ASC
    ''', (session['user_id'], 'active'))
    tasks = cur.fetchall()
    conn.close()
    
    result = []
    for task in tasks:
        result.append(dict(task))
    
    return jsonify(result)

# --- РЕГИСТРАЦИЯ ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = hashlib.md5(request.form['password'].encode()).hexdigest()
        
        if not username or not password:
            return render_template_string(REGISTER_PAGE, error='Заполните все поля')
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO users (username, password) VALUES (%s, %s)', (username, password))
            conn.commit()
            conn.close()
            return redirect('/login')
        except psycopg2.IntegrityError:
            conn.close()
            return render_template_string(REGISTER_PAGE, error='Пользователь уже существует')
    
    return render_template_string(REGISTER_PAGE, error=None)

# --- ВХОД ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = hashlib.md5(request.form['password'].encode()).hexdigest()
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT id, username FROM users WHERE username = %s AND password = %s', (username, password))
        user = cur.fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect('/')
        else:
            return render_template_string(LOGIN_PAGE, error='Неверный логин или пароль')
    
    return render_template_string(LOGIN_PAGE, error=None)

# --- ВЫХОД ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- HTML ШАБЛОНЫ ---
MAIN_PAGE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Мой органайзер</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f4f6f9;
            padding: 16px;
            min-height: 100vh;
        }
        .app-container {
            display: flex;
            gap: 16px;
            max-width: 1400px;
            margin: 0 auto;
            align-items: flex-start;
            flex-wrap: wrap;
        }
        
        /* Левая колонка */
        .left-column {
            flex: 0 0 240px;
            background: #f0f2f5;
            border-radius: 14px;
            padding: 18px 14px;
            min-height: 400px;
        }
        .left-column h2 { font-size: 15px; color: #6b7280; margin-bottom: 12px; }
        .backlog-add {
            display: flex;
            gap: 6px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }
        .backlog-add input {
            flex: 1;
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 13px;
            min-width: 100px;
        }
        .backlog-add button {
            background: #4361ee;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            cursor: pointer;
        }
        .backlog-item {
            background: white;
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
            border-left: 4px solid #9ca3af;
        }
        .backlog-item .move-btn {
            background: none;
            border: none;
            color: #9ca3af;
            cursor: pointer;
            font-size: 16px;
        }
        .backlog-item .move-btn:hover { color: #4361ee; }
        .backlog-hint {
            font-size: 11px;
            color: #bbb;
            margin-top: 8px;
        }
        
        /* Центр */
        .center-column { flex: 1; min-width: 280px; }
        .header {
            background: white;
            border-radius: 12px;
            padding: 12px 20px;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .header h1 { font-size: 20px; color: #1a1a2e; }
        .header .user { color: #6b7280; font-size: 14px; }
        .header .btn-exit {
            background: #e74c3c;
            color: white;
            border: none;
            padding: 6px 14px;
            border-radius: 8px;
            cursor: pointer;
        }
        
        /* Фокус */
        .focus-block {
            background: white;
            border-radius: 14px;
            padding: 18px 20px;
            margin-bottom: 20px;
            border: 2px solid #4361ee;
            box-shadow: 0 2px 12px rgba(67,97,238,0.08);
        }
        .focus-block .block-header {
            font-size: 18px;
            font-weight: 700;
            color: #1a1a2e;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .focus-block .block-header .count {
            font-size: 13px;
            font-weight: 400;
            color: #6b7280;
            background: #f0f2f5;
            padding: 2px 14px;
            border-radius: 20px;
        }
        .task-card {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 10px 14px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
            border-left: 4px solid #ddd;
            cursor: pointer;
            transition: 0.2s;
        }
        .task-card:hover { background: #f0f2f5; }
        .task-card .task-info { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .task-card .task-meta { font-size: 11px; color: #6b7280; display: flex; align-items: center; gap: 6px; }
        .task-card .task-meta .repeat-icon { color: #f39c12; }
        .task-card .task-actions button {
            background: none;
            border: none;
            color: #9ca3af;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-card .task-actions button:hover { color: #4361ee; }
        .task-card.tag-work { border-left-color: #4361ee; }
        .task-card.tag-home { border-left-color: #2ecc71; }
        .task-card.tag-personal { border-left-color: #e74c3c; }
        .task-card.tag-later { border-left-color: #9ca3af; }
        
        .empty-block { color: #bbb; font-size: 13px; text-align: center; padding: 16px; }
        .add-task-btn {
            display: inline-block;
            background: #4361ee;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 4px 12px;
            font-size: 13px;
            cursor: pointer;
            margin-top: 6px;
        }
        .add-task-btn:hover { background: #3a56d4; }
        
        /* Блоки столбиком */
        .block-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
        .block {
            background: white;
            border-radius: 12px;
            padding: 14px 16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            min-height: 180px;
        }
        .block .block-header {
            font-size: 14px;
            font-weight: 600;
            color: #1a1a2e;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 2px solid #f0f2f5;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .block .block-header .count {
            font-size: 11px;
            font-weight: 400;
            color: #9ca3af;
            background: #f0f2f5;
            padding: 2px 10px;
            border-radius: 12px;
        }
        .block-work .block-header { border-bottom-color: #4361ee; }
        .block-home .block-header { border-bottom-color: #2ecc71; }
        .block-personal .block-header { border-bottom-color: #e74c3c; }
        
        /* Правая колонка */
        .right-column { flex: 0 0 160px; display: flex; flex-direction: column; gap: 12px; }
        .sidebar-card {
            background: white;
            border-radius: 12px;
            padding: 14px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .sidebar-card .big-btn {
            background: #4361ee;
            color: white;
            border: none;
            border-radius: 10px;
            padding: 12px;
            font-size: 15px;
            font-weight: 600;
            width: 100%;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .sidebar-card .big-btn:hover { background: #3a56d4; }
        .sidebar-card .big-btn-secondary {
            background: #6b7280;
            color: white;
            border: none;
            border-radius: 10px;
            padding: 12px;
            font-size: 15px;
            font-weight: 600;
            width: 100%;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            margin-top: 8px;
        }
        .sidebar-card .big-btn-secondary:hover { background: #4b5563; }
        
        /* Модалка */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.3);
            backdrop-filter: blur(4px);
            z-index: 999;
            justify-content: center;
            align-items: center;
        }
        .modal-overlay.open { display: flex; }
        .modal {
            background: white;
            border-radius: 18px;
            padding: 24px 28px;
            max-width: 420px;
            width: 90%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.2);
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal h3 { font-size: 18px; margin-bottom: 4px; }
        .modal .sub { font-size: 13px; color: #6b7280; margin-bottom: 16px; }
        .modal label { font-size: 12px; font-weight: 600; color: #1a1a2e; display: block; margin-top: 12px; margin-bottom: 4px; }
        .modal input, .modal select {
            width: 100%;
            padding: 8px 12px;
            border: 1.5px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
        }
        .modal input:focus, .modal select:focus { outline: none; border-color: #4361ee; }
        .modal .checkbox-group {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 12px;
        }
        .modal .checkbox-group input[type="checkbox"] { width: 18px; height: 18px; accent-color: #4361ee; }
        .modal .checkbox-group label { margin: 0; font-weight: 400; font-size: 14px; }
        .modal .repeat-options {
            display: none;
            margin-top: 8px;
            padding: 12px;
            background: #f8f9fa;
            border-radius: 8px;
        }
        .modal .repeat-options.visible { display: block; }
        .modal .modal-actions { display: flex; gap: 10px; margin-top: 18px; }
        .modal .modal-actions button { flex: 1; padding: 10px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }
        .modal .btn-save { background: #4361ee; color: white; }
        .modal .btn-save:hover { background: #3a56d4; }
        .modal .btn-cancel { background: #f0f2f5; color: #6b7280; }
        .modal .btn-cancel:hover { background: #e5e7eb; }
        
        @media (max-width: 1024px) {
            .left-column { flex: 1 1 100%; }
            .right-column { flex: 1 1 100%; flex-direction: row; }
            .right-column .sidebar-card { flex: 1; }
            .block-row { grid-template-columns: 1fr 1fr; }
        }
        @media (max-width: 600px) {
            .block-row { grid-template-columns: 1fr; }
            .header { flex-direction: column; text-align: center; }
        }
    </style>
</head>
<body>
<div class="app-container">

    <!-- Левая колонка -->
    <div class="left-column">
        <h2>📥 Распределить</h2>
        <div class="backlog-add">
            <input type="text" id="newTaskInput" placeholder="Новая задача...">
            <button id="addBacklogBtn">+</button>
        </div>
        <div id="backlogList"></div>
        <div class="backlog-hint">⬅️ Нажмите → чтобы распределить</div>
    </div>

    <!-- Центр -->
    <div class="center-column">
        <div class="header">
            <h1>📋 Мои задачи</h1>
            <div>
                <span class="user">👤 {{ username }}</span>
                <a href="/logout" class="btn-exit" style="text-decoration:none; display:inline-block; margin-left:10px;">Выйти</a>
            </div>
        </div>

        <!-- Фокус -->
        <div class="focus-block" id="focusBlock">
            <div class="block-header">
                🎯 Фокус
                <span class="count" id="focusCount">0</span>
            </div>
            <div id="focusTasks"></div>
            <div class="empty-block" id="focusEmpty">
                Нет задач в фокусе
                <button class="add-task-btn" data-category="focus">➕ Добавить задачу</button>
            </div>
        </div>

        <!-- Блоки столбиком -->
        <div class="block-row">
            <div class="block block-work" id="workBlock">
                <div class="block-header">💼 Работа <span class="count" id="workCount">0</span></div>
                <div id="workTasks"></div>
                <div class="empty-block" id="workEmpty">
                    Нет задач
                    <button class="add-task-btn" data-category="work">➕ Добавить задачу</button>
                </div>
            </div>
            <div class="block block-home" id="homeBlock">
                <div class="block-header">🏠 Дом <span class="count" id="homeCount">0</span></div>
                <div id="homeTasks"></div>
                <div class="empty-block" id="homeEmpty">
                    Нет задач
                    <button class="add-task-btn" data-category="home">➕ Добавить задачу</button>
                </div>
            </div>
            <div class="block block-personal" id="personalBlock">
                <div class="block-header">❤️ Личное <span class="count" id="personalCount">0</span></div>
                <div id="personalTasks"></div>
                <div class="empty-block" id="personalEmpty">
                    Нет задач
                    <button class="add-task-btn" data-category="personal">➕ Добавить задачу</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Правая колонка -->
    <div class="right-column">
        <div class="sidebar-card">
            <a href="/quarter/{{ current_quarter }}" class="big-btn">🗓️ 3 месяца</a>
            <a href="/later" class="big-btn-secondary">🕰️ Позже</a>
        </div>
    </div>
</div>

<!-- Модалка для добавления задачи в категорию -->
<div class="modal-overlay" id="addTaskModal">
    <div class="modal">
        <h3>➕ Новая задача</h3>
        <p class="sub" id="addTaskModalSub">Добавьте задачу в категорию</p>
        <input type="hidden" id="addTaskCategory">
        <label for="addTaskTitle">Название задачи</label>
        <input type="text" id="addTaskTitle" placeholder="Что нужно сделать?">
        <label for="addTaskDate">Дата выполнения</label>
        <input type="date" id="addTaskDate">
        <div class="checkbox-group">
            <input type="checkbox" id="addTaskRepeat">
            <label for="addTaskRepeat">🔄 Повторяющаяся задача</label>
        </div>
        <div class="repeat-options" id="addRepeatOptions">
            <label for="addRepeatType">Тип повторения</label>
            <select id="addRepeatType">
                <option value="daily">📆 Ежедневно</option>
                <option value="weekly">📅 Еженедельно</option>
            </select>
            <div id="addWeeklyDayGroup" style="margin-top:8px; display:none;">
                <label for="addRepeatDay">День недели</label>
                <select id="addRepeatDay">
                    <option value="0">Воскресенье</option>
                    <option value="1">Понедельник</option>
                    <option value="2">Вторник</option>
                    <option value="3">Среда</option>
                    <option value="4">Четверг</option>
                    <option value="5">Пятница</option>
                    <option value="6">Суббота</option>
                </select>
            </div>
        </div>
        <div class="modal-actions">
            <button class="btn-save" id="saveAddTaskBtn">💾 Сохранить</button>
            <button class="btn-cancel" id="cancelAddTaskBtn">Отмена</button>
        </div>
    </div>
</div>

<!-- Модалка для редактирования -->
<div class="modal-overlay" id="editModal">
    <div class="modal">
        <h3>✏️ Редактировать задачу</h3>
        <p class="sub">Измените название, дату или категорию</p>
        <input type="hidden" id="editTaskId">
        <label for="editTaskTitle">Название</label>
        <input type="text" id="editTaskTitle">
        <label for="editTaskDate">Дата выполнения</label>
        <input type="date" id="editTaskDate">
        <label for="editTaskCategory">Категория</label>
        <select id="editTaskCategory">
            <option value="focus">🎯 Фокус</option>
            <option value="work">💼 Работа</option>
            <option value="home">🏠 Дом</option>
            <option value="personal">❤️ Личное</option>
            <option value="later">🕰️ Позже</option>
        </select>
        <div class="checkbox-group">
            <input type="checkbox" id="editTaskRepeat">
            <label for="editTaskRepeat">🔄 Повторяющаяся задача</label>
        </div>
        <div class="repeat-options" id="editRepeatOptions">
            <label for="editRepeatType">Тип повторения</label>
            <select id="editRepeatType">
                <option value="daily">📆 Ежедневно</option>
                <option value="weekly">📅 Еженедельно</option>
            </select>
            <div id="editWeeklyDayGroup" style="margin-top:8px; display:none;">
                <label for="editRepeatDay">День недели</label>
                <select id="editRepeatDay">
                    <option value="0">Воскресенье</option>
                    <option value="1">Понедельник</option>
                    <option value="2">Вторник</option>
                    <option value="3">Среда</option>
                    <option value="4">Четверг</option>
                    <option value="5">Пятница</option>
                    <option value="6">Суббота</option>
                </select>
            </div>
        </div>
        <div class="modal-actions">
            <button class="btn-save" id="saveEditBtn">💾 Сохранить</button>
            <button class="btn-cancel" id="cancelEditBtn">Отмена</button>
        </div>
    </div>
</div>

<script>
    let currentTaskId = null;
    
    // --- Загрузка задач ---
    function loadTasks() {
        fetch('/api/tasks')
            .then(res => res.json())
            .then(tasks => {
                const categories = { focus: [], work: [], home: [], personal: [], later: [] };
                tasks.forEach(t => {
                    if (categories[t.category]) categories[t.category].push(t);
                    else categories.later.push(t);
                });
                renderTasks(categories);
            });
    }
    
    function renderTasks(categories) {
        const containerMap = {
            focus: { tasks: 'focusTasks', count: 'focusCount', empty: 'focusEmpty' },
            work: { tasks: 'workTasks', count: 'workCount', empty: 'workEmpty' },
            home: { tasks: 'homeTasks', count: 'homeCount', empty: 'homeEmpty' },
            personal: { tasks: 'personalTasks', count: 'personalCount', empty: 'personalEmpty' }
        };
        
        for (const [cat, data] of Object.entries(containerMap)) {
            const tasks = categories[cat] || [];
            const container = document.getElementById(data.tasks);
            const countEl = document.getElementById(data.count);
            const emptyEl = document.getElementById(data.empty);
            
            container.innerHTML = '';
            tasks.forEach(task => {
                const card = createTaskCard(task);
                container.appendChild(card);
            });
            
            countEl.textContent = tasks.length;
            if (emptyEl) {
                if (tasks.length === 0) {
                    emptyEl.style.display = 'block';
                } else {
                    emptyEl.style.display = 'none';
                }
            }
        }
    }
    
    function createTaskCard(task) {
        const div = document.createElement('div');
        div.className = `task-card tag-${task.category || 'later'}`;
        div.dataset.taskId = task.id;
        
        let metaHTML = '';
        if (task.date) {
            const d = new Date(task.date + 'T00:00:00');
            metaHTML += `📅 ${d.toLocaleDateString('ru-RU')}`;
        }
        if (task.repeat_type && task.repeat_type !== 'none') {
            let label = '';
            if (task.repeat_type === 'daily') label = '🔄 ежедневно';
            else if (task.repeat_type === 'weekly') {
                const days = ['вс','пн','вт','ср','чт','пт','сб'];
                label = `🔄 еженедельно (${days[task.repeat_day || 0]})`;
            }
            if (metaHTML) metaHTML += ' ';
            metaHTML += `<span class="repeat-icon">${label}</span>`;
        }
        
        div.innerHTML = `
            <div class="task-info">
                <span>${task.title}</span>
                ${metaHTML ? `<span class="task-meta">${metaHTML}</span>` : ''}
            </div>
            <div class="task-actions">
                <button class="edit-btn" title="Редактировать">✏️</button>
                <button class="done-btn" title="Выполнено">✅</button>
                <button class="delete-btn" title="Удалить">🗑️</button>
            </div>
        `;
        
        div.querySelector('.edit-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            openEditModal(task);
        });
        
        div.querySelector('.done-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            fetch(`/api/task/${task.id}/done`, { method: 'POST' })
                .then(() => loadTasks());
        });
        
        div.querySelector('.delete-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            if (confirm('Удалить задачу?')) {
                fetch(`/api/task/${task.id}`, { method: 'DELETE' })
                    .then(() => loadTasks());
            }
        });
        
        return div;
    }
    
    // --- Добавление задачи в категорию (модалка) ---
    document.querySelectorAll('.add-task-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const category = this.dataset.category;
            document.getElementById('addTaskCategory').value = category;
            document.getElementById('addTaskModalSub').textContent = `Добавьте задачу в категорию: ${getCategoryName(category)}`;
            document.getElementById('addTaskTitle').value = '';
            document.getElementById('addTaskDate').value = '';
            document.getElementById('addTaskRepeat').checked = false;
            document.getElementById('addRepeatOptions').classList.remove('visible');
            document.getElementById('addWeeklyDayGroup').style.display = 'none';
            document.getElementById('addTaskModal').classList.add('open');
        });
    });
    
    function getCategoryName(cat) {
        const names = {
            'focus': '🎯 Фокус',
            'work': '💼 Работа',
            'home': '🏠 Дом',
            'personal': '❤️ Личное',
            'later': '🕰️ Позже'
        };
        return names[cat] || cat;
    }
    
    document.getElementById('cancelAddTaskBtn').addEventListener('click', () => {
        document.getElementById('addTaskModal').classList.remove('open');
    });
    
    document.getElementById('saveAddTaskBtn').addEventListener('click', () => {
        const category = document.getElementById('addTaskCategory').value;
        const title = document.getElementById('addTaskTitle').value.trim();
        const date = document.getElementById('addTaskDate').value;
        const isRepeating = document.getElementById('addTaskRepeat').checked;
        let repeatType = 'none';
        let repeatDay = null;
        
        if (isRepeating) {
            repeatType = document.getElementById('addRepeatType').value;
            if (repeatType === 'weekly') {
                repeatDay = parseInt(document.getElementById('addRepeatDay').value);
            }
        }
        
        if (!title) { alert('Введите название'); return; }
        
        fetch('/api/task/direct', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, category, date, repeat_type: repeatType, repeat_day: repeatDay })
        })
        .then(res => res.json())
        .then(() => {
            document.getElementById('addTaskModal').classList.remove('open');
            loadTasks();
        });
    });
    
    // Показ/скрытие опций повторения в модалке добавления
    document.getElementById('addTaskRepeat').addEventListener('change', function() {
        const options = document.getElementById('addRepeatOptions');
        if (this.checked) {
            options.classList.add('visible');
            if (document.getElementById('addRepeatType').value === 'weekly') {
                document.getElementById('addWeeklyDayGroup').style.display = 'block';
            }
        } else {
            options.classList.remove('visible');
            document.getElementById('addWeeklyDayGroup').style.display = 'none';
        }
    });
    
    document.getElementById('addRepeatType').addEventListener('change', function() {
        document.getElementById('addWeeklyDayGroup').style.display = this.value === 'weekly' ? 'block' : 'none';
    });
    
    // --- Редактирование ---
    function openEditModal(task) {
        currentTaskId = task.id;
        document.getElementById('editTaskId').value = task.id;
        document.getElementById('editTaskTitle').value = task.title;
        document.getElementById('editTaskDate').value = task.date || '';
        document.getElementById('editTaskCategory').value = task.category || 'later';
        
        const isRepeating = task.repeat_type && task.repeat_type !== 'none';
        document.getElementById('editTaskRepeat').checked = isRepeating;
        
        const repeatOptions = document.getElementById('editRepeatOptions');
        if (isRepeating) {
            repeatOptions.classList.add('visible');
            document.getElementById('editRepeatType').value = task.repeat_type || 'daily';
            if (task.repeat_type === 'weekly') {
                document.getElementById('editWeeklyDayGroup').style.display = 'block';
                document.getElementById('editRepeatDay').value = task.repeat_day || 0;
            } else {
                document.getElementById('editWeeklyDayGroup').style.display = 'none';
            }
        } else {
            repeatOptions.classList.remove('visible');
            document.getElementById('editWeeklyDayGroup').style.display = 'none';
        }
        
        document.getElementById('editModal').classList.add('open');
    }
    
    document.getElementById('cancelEditBtn').addEventListener('click', () => {
        document.getElementById('editModal').classList.remove('open');
    });
    
    document.getElementById('saveEditBtn').addEventListener('click', () => {
        const taskId = document.getElementById('editTaskId').value;
        const title = document.getElementById('editTaskTitle').value.trim();
        const date = document.getElementById('editTaskDate').value;
        const category = document.getElementById('editTaskCategory').value;
        const isRepeating = document.getElementById('editTaskRepeat').checked;
        let repeatType = 'none';
        let repeatDay = null;
        
        if (isRepeating) {
            repeatType = document.getElementById('editRepeatType').value;
            if (repeatType === 'weekly') {
                repeatDay = parseInt(document.getElementById('editRepeatDay').value);
            }
        }
        
        if (!title) { alert('Введите название'); return; }
        
        fetch(`/api/task/${taskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, date, category, repeat_type: repeatType, repeat_day: repeatDay })
        })
        .then(res => res.json())
        .then(() => {
            document.getElementById('editModal').classList.remove('open');
            loadTasks();
        });
    });
    
    // Показ/скрытие опций повторения в модалке редактирования
    document.getElementById('editTaskRepeat').addEventListener('change', function() {
        const options = document.getElementById('editRepeatOptions');
        if (this.checked) {
            options.classList.add('visible');
            if (document.getElementById('editRepeatType').value === 'weekly') {
                document.getElementById('editWeeklyDayGroup').style.display = 'block';
            }
        } else {
            options.classList.remove('visible');
            document.getElementById('editWeeklyDayGroup').style.display = 'none';
        }
    });
    
    document.getElementById('editRepeatType').addEventListener('change', function() {
        document.getElementById('editWeeklyDayGroup').style.display = this.value === 'weekly' ? 'block' : 'none';
    });
    
    // --- Бэклог ---
    document.getElementById('addBacklogBtn').addEventListener('click', () => {
        const input = document.getElementById('newTaskInput');
        const title = input.value.trim();
        if (!title) return;
        
        fetch('/api/task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        })
        .then(res => res.json())
        .then(() => {
            input.value = '';
            loadTasks();
            loadBacklog();
        });
    });
    
    document.getElementById('newTaskInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('addBacklogBtn').click();
        }
    });
    
    function loadBacklog() {
        fetch('/api/tasks')
            .then(res => res.json())
            .then(tasks => {
                // Задачи из "Распределить" — те, у которых category = 'later' и нет привязки к кварталам
                const backlogTasks = tasks.filter(t => t.category === 'later' && (!t.quarter || t.quarter === ''));
                const container = document.getElementById('backlogList');
                container.innerHTML = '';
                backlogTasks.forEach(task => {
                    const item = document.createElement('div');
                    item.className = 'backlog-item';
                    item.innerHTML = `
                        <span>${task.title}</span>
                        <button class="move-btn" data-task-id="${task.id}" title="Переместить">→</button>
                    `;
                    container.appendChild(item);
                });
                
                document.querySelectorAll('.move-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const taskId = this.dataset.taskId;
                        alert('⬅️ Переместить задачу (будет реализовано позже)');
                    });
                });
            });
    }
    
    // --- Загрузка при старте ---
    loadTasks();
    loadBacklog();
</script>
</body>
</html>
'''

QUARTER_PAGE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ quarter_name }} {{ quarter_year }} — Мой органайзер</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f4f6f9;
            padding: 16px;
            min-height: 100vh;
        }
        .container { max-width: 900px; margin: 0 auto; }
        .header {
            background: white;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .header h1 { font-size: 22px; color: #1a1a2e; }
        .header .user { color: #6b7280; font-size: 14px; }
        .header .btn-back {
            background: #6b7280;
            color: white;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
        }
        .header .btn-back:hover { background: #4b5563; }
        
        .quarter-nav {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            justify-content: center;
        }
        .quarter-nav .q-link {
            padding: 8px 16px;
            border-radius: 8px;
            text-decoration: none;
            background: white;
            color: #1a1a2e;
            border: 1.5px solid #ddd;
            font-size: 14px;
            transition: 0.2s;
        }
        .quarter-nav .q-link:hover { border-color: #4361ee; background: #f8f9ff; }
        .quarter-nav .q-link.current {
            background: #4361ee;
            color: white;
            border-color: #4361ee;
        }
        .quarter-nav .q-link.past { opacity: 0.6; }
        
        .add-sphere {
            background: white;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        .add-sphere input {
            flex: 1;
            padding: 10px 14px;
            border: 1.5px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            min-width: 150px;
        }
        .add-sphere input:focus { outline: none; border-color: #4361ee; }
        .add-sphere button {
            background: #4361ee;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 24px;
            cursor: pointer;
            font-size: 14px;
        }
        .add-sphere button:hover { background: #3a56d4; }
        
        .sphere {
            background: white;
            border-radius: 12px;
            padding: 18px 20px;
            margin-bottom: 16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            border-left: 5px solid #4361ee;
        }
        .sphere-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 8px;
        }
        .sphere-header h3 { font-size: 18px; color: #1a1a2e; }
        
        .task-item {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }
        .task-item .task-info { display: flex; align-items: center; gap: 10px; }
        .task-item .task-meta { font-size: 12px; color: #6b7280; }
        .task-item .task-actions button {
            background: none;
            border: none;
            color: #9ca3af;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-item .task-actions button:hover { color: #4361ee; }
        
        .add-task-form {
            display: flex;
            gap: 8px;
            margin-top: 12px;
            flex-wrap: wrap;
        }
        .add-task-form input {
            flex: 1;
            padding: 8px 12px;
            border: 1.5px solid #ddd;
            border-radius: 8px;
            font-size: 13px;
            min-width: 120px;
        }
        .add-task-form input:focus { outline: none; border-color: #4361ee; }
        .add-task-form button {
            background: #2ecc71;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            cursor: pointer;
            font-size: 13px;
        }
        .add-task-form button:hover { background: #27ae60; }
        
        .empty-sphere { color: #bbb; font-style: italic; padding: 10px 0; }
        
        @media (max-width: 600px) {
            .header { flex-direction: column; text-align: center; }
            .add-sphere { flex-direction: column; }
            .add-sphere input { width: 100%; }
            .quarter-nav { justify-content: center; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🗓️ {{ quarter_name }} {{ quarter_year }}</h1>
        <div>
            <span class="user">👤 {{ username }}</span>
            <a href="/" class="btn-back" style="margin-left:12px;">← Назад</a>
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#e74c3c;">Выйти</a>
        </div>
    </div>
    
    <!-- Навигация по кварталам -->
    <div class="quarter-nav">
        {% for q in quarters %}
        <a href="/quarter/{{ q.id }}" class="q-link 
            {% if q.id == quarter %}current{% endif %}
            {% if q.id != quarter and q.id < current_quarter %}past{% endif %}
        ">
            {{ q.name }} {{ q.year }}
            {% if q.current %}⭐{% endif %}
        </a>
        {% endfor %}
    </div>
    
    <div class="add-sphere">
        <input type="text" id="sphereName" placeholder="Название сферы (например: Работа, Здоровье...)" />
        <button id="addSphereBtn">➕ Добавить сферу</button>
    </div>
    
    <div id="spheresContainer">
        {% for sphere in spheres %}
        <div class="sphere" data-sphere="{{ sphere.name }}">
            <div class="sphere-header">
                <h3>📂 {{ sphere.name }}</h3>
            </div>
            <div id="tasks-{{ loop.index }}">
                {% for task in sphere.tasks %}
                <div class="task-item" data-task-id="{{ task.id }}">
                    <div class="task-info">
                        <span>{{ task.title }}</span>
                        {% if task.date %}
                        <span class="task-meta">📅 {{ task.date }}</span>
                        {% endif %}
                    </div>
                    <div class="task-actions">
                        <button class="done-btn" data-task-id="{{ task.id }}">✅</button>
                        <button class="delete-btn" data-task-id="{{ task.id }}">🗑️</button>
                    </div>
                </div>
                {% else %}
                <div class="empty-sphere">Нет задач в этой сфере</div>
                {% endfor %}
            </div>
            <div class="add-task-form">
                <input type="text" class="taskInput" placeholder="Новая задача..." />
                <input type="date" class="taskDate" />
                <button class="addTaskBtn" data-sphere="{{ sphere.name }}">➕ Добавить задачу</button>
            </div>
        </div>
        {% else %}
        <div style="text-align:center; padding:40px; color:#aaa; background:white; border-radius:12px;">
            <p style="font-size:18px;">📭 Нет сфер</p>
            <p style="font-size:14px;">Добавьте первую сферу выше</p>
        </div>
        {% endfor %}
    </div>
</div>

<script>
    const quarter = '{{ quarter }}';
    
    document.getElementById('addSphereBtn').addEventListener('click', function() {
        const name = document.getElementById('sphereName').value.trim();
        if (!name) { alert('Введите название сферы'); return; }
        
        fetch('/api/sphere', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, quarter })
        })
        .then(res => res.json())
        .then(() => location.reload());
    });
    
    document.getElementById('sphereName').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') document.getElementById('addSphereBtn').click();
    });
    
    document.querySelectorAll('.addTaskBtn').forEach(btn => {
        btn.addEventListener('click', function() {
            const sphere = this.dataset.sphere;
            const container = this.closest('.sphere');
            const input = container.querySelector('.taskInput');
            const dateInput = container.querySelector('.taskDate');
            const title = input.value.trim();
            const date = dateInput.value;
            
            if (!title) { alert('Введите название задачи'); return; }
            
            fetch('/api/task/quarter', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, sphere, quarter, date })
            })
            .then(res => res.json())
            .then(() => location.reload());
        });
    });
    
    document.querySelectorAll('.taskInput').forEach(input => {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                this.closest('.add-task-form').querySelector('.addTaskBtn').click();
            }
        });
    });
    
    document.querySelectorAll('.done-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = this.dataset.taskId;
            fetch(`/api/task/${taskId}/done`, { method: 'POST' })
                .then(() => location.reload());
        });
    });
    
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = this.dataset.taskId;
            if (confirm('Удалить задачу?')) {
                fetch(`/api/task/${taskId}`, { method: 'DELETE' })
                    .then(() => location.reload());
            }
        });
    });
</script>
</body>
</html>
'''

LATER_PAGE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🕰️ Позже — Мой органайзер</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f4f6f9;
            padding: 16px;
            min-height: 100vh;
        }
        .container { max-width: 800px; margin: 0 auto; }
        .header {
            background: white;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .header h1 { font-size: 22px; color: #1a1a2e; }
        .header .user { color: #6b7280; font-size: 14px; }
        .header .btn-back {
            background: #6b7280;
            color: white;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
        }
        .header .btn-back:hover { background: #4b5563; }
        
        .add-task {
            background: white;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        .add-task input {
            flex: 1;
            padding: 10px 14px;
            border: 1.5px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            min-width: 150px;
        }
        .add-task input:focus { outline: none; border-color: #4361ee; }
        .add-task button {
            background: #4361ee;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 24px;
            cursor: pointer;
            font-size: 14px;
        }
        .add-task button:hover { background: #3a56d4; }
        
        .task-list {
            background: white;
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .task-list .task-item {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }
        .task-list .task-item .task-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .task-list .task-item .task-actions button {
            background: none;
            border: none;
            color: #9ca3af;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-list .task-item .task-actions button:hover { color: #4361ee; }
        
        .empty-list { color: #bbb; text-align: center; padding: 30px; }
        
        @media (max-width: 600px) {
            .header { flex-direction: column; text-align: center; }
            .add-task { flex-direction: column; }
            .add-task input { width: 100%; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🕰️ Позже</h1>
        <div>
            <span class="user">👤 {{ username }}</span>
            <a href="/" class="btn-back" style="margin-left:12px;">← Назад</a>
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#e74c3c;">Выйти</a>
        </div>
    </div>
    
    <div class="add-task">
        <input type="text" id="laterTaskInput" placeholder="Что хотите отложить?" />
        <button id="addLaterBtn">➕ Добавить</button>
    </div>
    
    <div class="task-list" id="laterTasks">
        {% for task in tasks %}
        <div class="task-item" data-task-id="{{ task.id }}">
            <div class="task-info">
                <span>{{ task.title }}</span>
            </div>
            <div class="task-actions">
                <button class="done-btn" data-task-id="{{ task.id }}">✅</button>
                <button class="delete-btn" data-task-id="{{ task.id }}">🗑️</button>
            </div>
        </div>
        {% else %}
        <div class="empty-list">📭 Здесь пока пусто. Добавьте задачи, которые хотите отложить на потом.</div>
        {% endfor %}
    </div>
</div>

<script>
    // --- Добавление задачи в "Позже" ---
    document.getElementById('addLaterBtn').addEventListener('click', function() {
        const input = document.getElementById('laterTaskInput');
        const title = input.value.trim();
        if (!title) { alert('Введите название задачи'); return; }
        
        fetch('/api/task/later', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        })
        .then(res => res.json())
        .then(() => location.reload());
    });
    
    document.getElementById('laterTaskInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            document.getElementById('addLaterBtn').click();
        }
    });
    
    // --- Выполнение задачи ---
    document.querySelectorAll('.done-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = this.dataset.taskId;
            fetch(`/api/task/${taskId}/done`, { method: 'POST' })
                .then(() => location.reload());
        });
    });
    
    // --- Удаление задачи ---
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = this.dataset.taskId;
            if (confirm('Удалить задачу?')) {
                fetch(`/api/task/${taskId}`, { method: 'DELETE' })
                    .then(() => location.reload());
            }
        });
    });
</script>
</body>
</html>
'''

REGISTER_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Регистрация</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f4f6f9; margin: 0; }
        .card { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); width: 100%; max-width: 360px; }
        h2 { margin-bottom: 20px; color: #1a1a2e; }
        input { width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; }
        input:focus { outline: none; border-color: #4361ee; }
        button { width: 100%; padding: 12px; background: #4361ee; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; }
        button:hover { background: #3a56d4; }
        .error { color: #e74c3c; font-size: 14px; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 16px; font-size: 14px; color: #6b7280; }
        .link a { color: #4361ee; text-decoration: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📝 Регистрация</h2>
        {% if error %}
            <div class="error">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <input type="text" name="username" placeholder="Логин" required>
            <input type="password" name="password" placeholder="Пароль" required>
            <button type="submit">Зарегистрироваться</button>
        </form>
        <div class="link">Уже есть аккаунт? <a href="/login">Войти</a></div>
    </div>
</body>
</html>
'''

LOGIN_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вход</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f4f6f9; margin: 0; }
        .card { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); width: 100%; max-width: 360px; }
        h2 { margin-bottom: 20px; color: #1a1a2e; }
        input { width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; }
        input:focus { outline: none; border-color: #4361ee; }
        button { width: 100%; padding: 12px; background: #4361ee; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; }
        button:hover { background: #3a56d4; }
        .error { color: #e74c3c; font-size: 14px; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 16px; font-size: 14px; color: #6b7280; }
        .link a { color: #4361ee; text-decoration: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🔑 Вход</h2>
        {% if error %}
            <div class="error">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <input type="text" name="username" placeholder="Логин" required>
            <input type="password" name="password" placeholder="Пароль" required>
            <button type="submit">Войти</button>
        </form>
        <div class="link">Нет аккаунта? <a href="/register">Зарегистрироваться</a></div>
    </div>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)