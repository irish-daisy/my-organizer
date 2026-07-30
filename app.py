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
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
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
            sphere TEXT,
            later_group TEXT,
            completed_at TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS spheres (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            quarter TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS later_groups (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
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
    now = datetime.now()
    year = now.year
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
    
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND (quarter IS NULL OR quarter = '') 
        ORDER BY date ASC
    ''', (user_id, 'active'))
    tasks = cur.fetchall()
    conn.close()
    
    categories = {
        'focus': [],
        'work': [],
        'home': [],
        'personal': []
    }
    
    for task in tasks:
        cat = task['category'] if task['category'] in categories else None
        if cat and cat != 'later':
            categories[cat].append(dict(task))
    
    current_quarter = get_current_quarter()
    
    return render_template_string(MAIN_PAGE, 
                                   focus_tasks=categories['focus'],
                                   work_tasks=categories['work'],
                                   home_tasks=categories['home'],
                                   personal_tasks=categories['personal'],
                                   username=session.get('username', 'Пользователь'),
                                   current_quarter=current_quarter)

# --- СТРАНИЦА КВАРТАЛОВ (3 МЕСЯЦА) ---
@app.route('/quarter/<quarter>')
def quarter_page(quarter):
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute('SELECT * FROM spheres WHERE user_id = %s AND quarter = %s ORDER BY created_at ASC', (user_id, quarter))
    spheres = cur.fetchall()
    
    for sphere in spheres:
        cur.execute('SELECT * FROM tasks WHERE user_id = %s AND sphere = %s AND quarter = %s AND status = %s ORDER BY created_at ASC', 
                   (user_id, sphere['name'], quarter, 'active'))
        sphere['tasks'] = cur.fetchall()
    
    conn.close()
    
    quarters = ['Q1', 'Q2', 'Q3', 'Q4']
    current_q = get_current_quarter()
    quarter_data = []
    for q in quarters:
        quarter_data.append({
            'id': q,
            'name': get_quarter_name(q),
            'year': get_quarter_year(q),
            'current': (q == current_q)
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
    
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND category = %s AND status = %s AND (later_group IS NULL OR later_group = '')
        ORDER BY created_at DESC
    ''', (user_id, 'later', 'active'))
    tasks = cur.fetchall()
    
    cur.execute('SELECT * FROM later_groups WHERE user_id = %s ORDER BY created_at ASC', (user_id,))
    groups = cur.fetchall()
    
    for group in groups:
        cur.execute('SELECT * FROM tasks WHERE user_id = %s AND later_group = %s AND status = %s ORDER BY created_at DESC', 
                   (user_id, group['name'], 'active'))
        group['tasks'] = cur.fetchall()
    
    conn.close()
    
    return render_template_string(LATER_PAGE, 
                                   tasks=tasks,
                                   groups=groups,
                                   username=session.get('username', 'Пользователь'))

# --- API: Добавить группу в "Позже" ---
@app.route('/api/later/group', methods=['POST'])
def add_later_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO later_groups (user_id, name) VALUES (%s, %s)', (session['user_id'], name))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Group added'})

# --- API: Добавить задачу в группу "Позже" ---
@app.route('/api/task/later/group', methods=['POST'])
def add_task_to_later_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    title = data.get('title', '').strip()
    group = data.get('group', '')
    
    if not title or not group:
        return jsonify({'error': 'Title and group are required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status, later_group)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', 'none', None, 'active', group))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added to later group'})

# --- API: Переместить задачу в группу "Позже" ---
@app.route('/api/task/<int:task_id>/move_to_later_group', methods=['PUT'])
def move_to_later_group(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    group = data.get('group', '')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE tasks SET later_group = %s WHERE id = %s AND user_id = %s', (group, task_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task moved to later group'})

# --- API: Удалить группу "Позже" ---
@app.route('/api/later/group/<int:group_id>', methods=['DELETE'])
def delete_later_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT name FROM later_groups WHERE id = %s AND user_id = %s', (group_id, session['user_id']))
    group = cur.fetchone()
    if group:
        cur.execute('UPDATE tasks SET later_group = NULL WHERE user_id = %s AND later_group = %s', (session['user_id'], group[0]))
    cur.execute('DELETE FROM later_groups WHERE id = %s AND user_id = %s', (group_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Group deleted'})

# --- API: Добавить задачу в "Позже" (в общий список) ---
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

# --- API: Обновить сферу в квартале ---
@app.route('/api/sphere/<int:sphere_id>', methods=['PUT'])
def update_sphere(sphere_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE spheres SET name = %s WHERE id = %s AND user_id = %s', (name, sphere_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Sphere updated'})

# --- API: Удалить сферу в квартале ---
@app.route('/api/sphere/<int:sphere_id>', methods=['DELETE'])
def delete_sphere(sphere_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    # Удаляем задачи, связанные с этой сферой
    cur.execute('DELETE FROM tasks WHERE sphere_id = %s AND user_id = %s', (sphere_id, session['user_id']))
    cur.execute('DELETE FROM spheres WHERE id = %s AND user_id = %s', (sphere_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Sphere deleted'})

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
    # Получаем id сферы
    cur.execute('SELECT id FROM spheres WHERE user_id = %s AND name = %s AND quarter = %s', (session['user_id'], sphere, quarter))
    sphere_result = cur.fetchone()
    sphere_id = sphere_result[0] if sphere_result else None
    
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, status, quarter, sphere, sphere_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', date, 'active', quarter, sphere, sphere_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added to quarter'})

# --- API: Добавить задачу напрямую в категорию ---
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

# --- API: Добавить задачу в бэклог ---
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
    
    # Помечаем задачу как выполненную с временем завершения
    cur.execute('UPDATE tasks SET status = %s, completed_at = %s WHERE id = %s', ('done', datetime.now(), task_id))
    
    # Если повторяющаяся — создаём новую
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
            INSERT INTO tasks (user_id, title, category, date, repeat_type, repeat_day, status, quarter, sphere, later_group, sphere_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (task['user_id'], task['title'], task['category'], new_date, task['repeat_type'], task['repeat_day'], 'active', task.get('quarter'), task.get('sphere'), task.get('later_group'), task.get('sphere_id')))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task done'})

# --- API: Восстановить задачу из "Готово" ---
@app.route('/api/task/<int:task_id>/restore', methods=['POST'])
def restore_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE tasks SET status = %s, completed_at = NULL WHERE id = %s AND user_id = %s', ('active', task_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task restored'})

# --- API: Получить выполненные задачи (за 24 часа) ---
@app.route('/api/tasks/done')
def get_done_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Задачи, выполненные за последние 24 часа
    yesterday = datetime.now() - timedelta(days=1)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND completed_at >= %s
        ORDER BY completed_at DESC
    ''', (session['user_id'], 'done', yesterday))
    tasks = cur.fetchall()
    conn.close()
    
    result = []
    for task in tasks:
        result.append(dict(task))
    
    return jsonify(result)

# --- API: Переместить задачу из бэклога в категорию ---
@app.route('/api/task/<int:task_id>/move', methods=['PUT'])
def move_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    category = data.get('category', 'later')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE tasks SET category = %s WHERE id = %s AND user_id = %s', (category, task_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task moved'})

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

# ====== HTML ШАБЛОНЫ ======

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
            background: #f6f2fd;
            padding: 16px;
            min-height: 100vh;
            color: #4a3f5e;
        }
        .app-container {
            display: flex;
            gap: 16px;
            max-width: 1400px;
            margin: 0 auto;
            align-items: flex-start;
            flex-wrap: wrap;
        }
        .left-column {
            flex: 0 0 240px;
            background: #fcfaff;
            border-radius: 14px;
            padding: 18px 14px;
            min-height: 400px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .left-column h2 { font-size: 15px; color: #8b7bb5; margin-bottom: 12px; }
        .backlog-add {
            display: flex;
            gap: 6px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }
        .backlog-add input {
            flex: 1;
            padding: 8px 12px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 13px;
            min-width: 100px;
            background: white;
            color: #4a3f5e;
        }
        .backlog-add input:focus { outline: none; border-color: #8b7bb5; }
        .backlog-add button {
            background: #8b7bb5;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            cursor: pointer;
        }
        .backlog-add button:hover { background: #7a69a4; }
        .backlog-item {
            background: white;
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
            border-left: 4px solid #d5c8e6;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.06);
        }
        .backlog-item .move-btn {
            background: none;
            border: none;
            color: #b5a7cc;
            cursor: pointer;
            font-size: 16px;
            padding: 4px 8px;
        }
        .backlog-item .move-btn:hover { color: #8b7bb5; }
        .backlog-hint { font-size: 11px; color: #c5b8d8; margin-top: 8px; }
        
        .center-column { flex: 1; min-width: 280px; }
        .header {
            background: #fcfaff;
            border-radius: 12px;
            padding: 12px 20px;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .header h1 { font-size: 20px; color: #4a3f5e; }
        .header .user { color: #8b7bb5; font-size: 14px; }
        .header .btn-exit {
            background: #d5c8e6;
            color: #4a3f5e;
            border: none;
            padding: 6px 14px;
            border-radius: 8px;
            cursor: pointer;
        }
        .header .btn-exit:hover { background: #c5b8d8; }
        
        .focus-block {
            background: #fcfaff;
            border-radius: 14px;
            padding: 18px 20px;
            margin-bottom: 20px;
            border: 2px solid #d5c8e6;
            box-shadow: 0 2px 12px rgba(139, 123, 181, 0.06);
        }
        .focus-block .block-header {
            font-size: 18px;
            font-weight: 700;
            color: #4a3f5e;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .focus-block .block-header .count {
            font-size: 13px;
            font-weight: 400;
            color: #8b7bb5;
            background: #f0e8fa;
            padding: 2px 14px;
            border-radius: 20px;
        }
        .focus-block .empty-block { color: #c5b8d8; font-size: 13px; text-align: center; padding: 16px; }
        
        .block-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
        .block {
            background: #fcfaff;
            border-radius: 12px;
            padding: 14px 16px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            min-height: 180px;
        }
        .block .block-header {
            font-size: 14px;
            font-weight: 600;
            color: #4a3f5e;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 2px solid #ede5f5;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .block .block-header .count {
            font-size: 11px;
            font-weight: 400;
            color: #8b7bb5;
            background: #f0e8fa;
            padding: 2px 10px;
            border-radius: 12px;
        }
        .block-work .block-header { border-bottom-color: #ede5f5; }
        .block-home .block-header { border-bottom-color: #ede5f5; }
        .block-personal .block-header { border-bottom-color: #ede5f5; }
        
        .task-card {
            background: #faf5ff;
            border-radius: 10px;
            padding: 10px 14px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
            border-left: 4px solid #d5c8e6;
            cursor: pointer;
            transition: 0.2s;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.04);
        }
        .task-card:hover { background: #f5eefa; }
        .task-card .task-info { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .task-card .task-meta { font-size: 11px; color: #b5a7cc; display: flex; align-items: center; gap: 6px; }
        .task-card .task-meta .repeat-icon { color: #b5a7cc; }
        .task-card .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-card .task-actions button:hover { color: #8b7bb5; }
        .task-card.tag-focus { border-left-color: #8b7bb5; }
        .task-card.tag-work { border-left-color: #8b7bb5; }
        .task-card.tag-home { border-left-color: #8b7bb5; }
        .task-card.tag-personal { border-left-color: #8b7bb5; }
        
        .empty-block { color: #c5b8d8; font-size: 13px; text-align: center; padding: 16px; }
        .add-task-btn {
            background: none;
            border: none;
            color: #8b7bb5;
            cursor: pointer;
            font-size: 20px;
            padding: 4px 8px;
            display: block;
            margin: 4px auto 0;
            transition: 0.2s;
        }
        .add-task-btn:hover { color: #7a69a4; transform: scale(1.1); }
        
        .right-column { flex: 0 0 160px; display: flex; flex-direction: column; gap: 12px; }
        .sidebar-card {
            background: #fcfaff;
            border-radius: 12px;
            padding: 14px;
            text-align: center;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .sidebar-card .big-btn {
            background: #8b7bb5;
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
        .sidebar-card .big-btn:hover { background: #7a69a4; }
        .sidebar-card .big-btn-secondary {
            background: #d5c8e6;
            color: #4a3f5e;
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
        .sidebar-card .big-btn-secondary:hover { background: #c5b8d8; }
        .sidebar-card .big-btn-done {
            background: #27ae60;
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
        .sidebar-card .big-btn-done:hover { background: #2ecc71; }
        
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(74, 63, 94, 0.3);
            backdrop-filter: blur(4px);
            z-index: 999;
            justify-content: center;
            align-items: center;
        }
        .modal-overlay.open { display: flex; }
        .modal {
            background: #fcfaff;
            border-radius: 18px;
            padding: 24px 28px;
            max-width: 420px;
            width: 90%;
            box-shadow: 0 20px 60px rgba(74, 63, 94, 0.15);
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal h3 { font-size: 18px; margin-bottom: 4px; color: #4a3f5e; }
        .modal .sub { font-size: 13px; color: #8b7bb5; margin-bottom: 16px; }
        .modal label { font-size: 12px; font-weight: 600; color: #4a3f5e; display: block; margin-top: 12px; margin-bottom: 4px; }
        .modal input, .modal select {
            width: 100%;
            padding: 8px 12px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 14px;
            background: white;
            color: #4a3f5e;
        }
        .modal input:focus, .modal select:focus { outline: none; border-color: #8b7bb5; }
        .modal .checkbox-group {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 12px;
        }
        .modal .checkbox-group input[type="checkbox"] { width: 18px; height: 18px; accent-color: #8b7bb5; }
        .modal .checkbox-group label { margin: 0; font-weight: 400; font-size: 14px; }
        .modal .repeat-options {
            display: none;
            margin-top: 8px;
            padding: 12px;
            background: #f8f2fd;
            border-radius: 8px;
        }
        .modal .repeat-options.visible { display: block; }
        .modal .modal-actions { display: flex; gap: 10px; margin-top: 18px; }
        .modal .modal-actions button { flex: 1; padding: 10px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }
        .modal .btn-save { background: #8b7bb5; color: white; }
        .modal .btn-save:hover { background: #7a69a4; }
        .modal .btn-cancel { background: #ede5f5; color: #4a3f5e; }
        .modal .btn-cancel:hover { background: #e0d5ec; }
        
        .move-options {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin-top: 12px;
        }
        .move-options button {
            padding: 10px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            background: white;
            cursor: pointer;
            font-size: 14px;
            transition: 0.2s;
            color: #4a3f5e;
        }
        .move-options button:hover { border-color: #8b7bb5; background: #f8f2fd; }
        .move-options button .cat-icon { display: block; font-size: 20px; }
        
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
            <input type="text" id="newTaskInput" placeholder="Новая задача..." autofocus>
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

        <!-- Фокус (без кнопки добавления) -->
        <div class="focus-block" id="focusBlock">
            <div class="block-header">
                🎯 Фокус
                <span class="count" id="focusCount">0</span>
            </div>
            <div id="focusTasks"></div>
            <div class="empty-block" id="focusEmpty">Нет задач в фокусе</div>
        </div>

        <!-- Блоки столбиком -->
        <div class="block-row">
            <div class="block block-work" id="workBlock">
                <div class="block-header">💼 Работа <span class="count" id="workCount">0</span></div>
                <div id="workTasks"></div>
                <div class="empty-block" id="workEmpty">
                    Нет задач
                    <button class="add-task-btn" data-category="work">➕</button>
                </div>
            </div>
            <div class="block block-home" id="homeBlock">
                <div class="block-header">🏠 Дом <span class="count" id="homeCount">0</span></div>
                <div id="homeTasks"></div>
                <div class="empty-block" id="homeEmpty">
                    Нет задач
                    <button class="add-task-btn" data-category="home">➕</button>
                </div>
            </div>
            <div class="block block-personal" id="personalBlock">
                <div class="block-header">❤️ Личное <span class="count" id="personalCount">0</span></div>
                <div id="personalTasks"></div>
                <div class="empty-block" id="personalEmpty">
                    Нет задач
                    <button class="add-task-btn" data-category="personal">➕</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Правая колонка -->
    <div class="right-column">
        <div class="sidebar-card">
            <a href="/quarter/{{ current_quarter }}" class="big-btn">🗓️ 3 месяца</a>
            <a href="/later" class="big-btn-secondary">🕰️ Позже</a>
            <a href="/done" class="big-btn-done">✅ Готово</a>
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
        <input type="text" id="addTaskTitle" placeholder="Что нужно сделать?" autofocus>
        <label for="addTaskDate">Дата выполнения</label>
        <div style="display:flex; gap:8px; align-items:center;">
            <input type="text" id="addTaskDateDisplay" placeholder="Сегодня" style="flex:1;">
            <input type="date" id="addTaskDateHidden" style="display:none;">
            <button id="addTaskDateToggle" style="padding:8px 12px; border:1.5px solid #ede5f5; border-radius:8px; background:white; cursor:pointer; color:#4a3f5e;">📅</button>
        </div>
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

<!-- Модалка для перемещения задачи из бэклога -->
<div class="modal-overlay" id="moveModal">
    <div class="modal">
        <h3>➡️ Переместить задачу</h3>
        <p class="sub" id="moveTaskTitle">Выберите категорию</p>
        <input type="hidden" id="moveTaskId">
        <div class="move-options">
            <button class="move-cat-btn" data-category="focus"><span class="cat-icon">🎯</span> Фокус</button>
            <button class="move-cat-btn" data-category="work"><span class="cat-icon">💼</span> Работа</button>
            <button class="move-cat-btn" data-category="home"><span class="cat-icon">🏠</span> Дом</button>
            <button class="move-cat-btn" data-category="personal"><span class="cat-icon">❤️</span> Личное</button>
            <button class="move-cat-btn" data-category="later" style="grid-column: span 2;"><span class="cat-icon">🕰️</span> Позже</button>
        </div>
        <div class="modal-actions">
            <button class="btn-cancel" id="cancelMoveBtn">Отмена</button>
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
    let moveTaskId = null;
    
    function loadTasks() {
        fetch('/api/tasks')
            .then(res => res.json())
            .then(tasks => {
                const categories = { focus: [], work: [], home: [], personal: [] };
                tasks.forEach(t => {
                    if (categories[t.category]) categories[t.category].push(t);
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
                emptyEl.style.display = tasks.length === 0 ? 'block' : 'none';
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
        } else {
            metaHTML = '📅 Сегодня';
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
                <span class="task-meta">${metaHTML}</span>
            </div>
            <div class="task-actions">
                <button class="edit-btn" title="Редактировать">✏️</button>
                <button class="done-btn" title="Выполнено">✅</button>
                <button class="delete-btn" title="Удалить">🗑️</button>
                <button class="move-to-focus-btn" title="В фокус" data-task-id="${task.id}" style="display:${task.category !== 'focus' ? 'inline' : 'none'}">⭐</button>
            </div>
        `;
        
        div.querySelector('.edit-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            openEditModal(task);
        });
        
        div.querySelector('.done-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            fetch(`/api/task/${task.id}/done`, { method: 'POST' })
                .then(() => { loadTasks(); loadBacklog(); });
        });
        
        div.querySelector('.delete-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            if (confirm('Удалить задачу?')) {
                fetch(`/api/task/${task.id}`, { method: 'DELETE' })
                    .then(() => { loadTasks(); loadBacklog(); });
            }
        });
        
        const focusBtn = div.querySelector('.move-to-focus-btn');
        if (focusBtn) {
            focusBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                const taskId = this.dataset.taskId;
                fetch(`/api/task/${taskId}/move`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: 'focus' })
                })
                .then(() => { loadTasks(); loadBacklog(); });
            });
        }
        
        return div;
    }
    
    document.querySelectorAll('.add-task-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const category = this.dataset.category;
            document.getElementById('addTaskCategory').value = category;
            document.getElementById('addTaskModalSub').textContent = `Добавьте задачу в категорию: ${getCategoryName(category)}`;
            document.getElementById('addTaskTitle').value = '';
            document.getElementById('addTaskDateDisplay').value = 'Сегодня';
            document.getElementById('addTaskDateHidden').value = '';
            document.getElementById('addTaskRepeat').checked = false;
            document.getElementById('addRepeatOptions').classList.remove('visible');
            document.getElementById('addWeeklyDayGroup').style.display = 'none';
            document.getElementById('addTaskModal').classList.add('open');
            setTimeout(() => document.getElementById('addTaskTitle').focus(), 100);
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
    
    document.getElementById('addTaskDateDisplay').addEventListener('focus', function() {
        const hidden = document.getElementById('addTaskDateHidden');
        hidden.style.display = 'inline-block';
        hidden.focus();
    });
    
    document.getElementById('addTaskDateHidden').addEventListener('change', function() {
        if (this.value) {
            const d = new Date(this.value + 'T00:00:00');
            document.getElementById('addTaskDateDisplay').value = d.toLocaleDateString('ru-RU');
        } else {
            document.getElementById('addTaskDateDisplay').value = 'Сегодня';
        }
        this.style.display = 'none';
    });
    
    document.getElementById('addTaskDateDisplay').addEventListener('blur', function() {
        setTimeout(() => {
            document.getElementById('addTaskDateHidden').style.display = 'none';
        }, 200);
    });
    
    document.getElementById('addTaskDateToggle').addEventListener('click', function() {
        const hidden = document.getElementById('addTaskDateHidden');
        if (hidden.style.display === 'none') {
            hidden.style.display = 'inline-block';
            hidden.focus();
        } else {
            hidden.style.display = 'none';
        }
    });
    
    document.getElementById('cancelAddTaskBtn').addEventListener('click', () => {
        document.getElementById('addTaskModal').classList.remove('open');
    });
    
    document.getElementById('saveAddTaskBtn').addEventListener('click', () => {
        const category = document.getElementById('addTaskCategory').value;
        const title = document.getElementById('addTaskTitle').value.trim();
        const dateDisplay = document.getElementById('addTaskDateDisplay').value;
        let date = document.getElementById('addTaskDateHidden').value;
        if (!date && dateDisplay !== 'Сегодня' && dateDisplay !== '') {
            const parts = dateDisplay.split('.');
            if (parts.length === 3) {
                date = `${parts[2]}-${parts[1]}-${parts[0]}`;
            }
        }
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
            loadBacklog();
        });
    });
    
    document.getElementById('addTaskTitle').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') document.getElementById('saveAddTaskBtn').click();
    });
    
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
        setTimeout(() => document.getElementById('editTaskTitle').focus(), 100);
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
    
    document.getElementById('editTaskTitle').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') document.getElementById('saveEditBtn').click();
    });
    
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
                    btn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        const taskId = this.dataset.taskId;
                        const task = backlogTasks.find(t => t.id == taskId);
                        if (task) {
                            document.getElementById('moveTaskId').value = taskId;
                            document.getElementById('moveTaskTitle').textContent = `"${task.title}" → куда?`;
                            document.getElementById('moveModal').classList.add('open');
                        }
                    });
                });
            });
    }
    
    document.querySelectorAll('.move-cat-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = document.getElementById('moveTaskId').value;
            const category = this.dataset.category;
            
            fetch(`/api/task/${taskId}/move`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category })
            })
            .then(res => res.json())
            .then(() => {
                document.getElementById('moveModal').classList.remove('open');
                loadTasks();
                loadBacklog();
            });
        });
    });
    
    document.getElementById('cancelMoveBtn').addEventListener('click', () => {
        document.getElementById('moveModal').classList.remove('open');
    });
    
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
            background: #f6f2fd;
            padding: 16px;
            min-height: 100vh;
            color: #4a3f5e;
        }
        .container { max-width: 900px; margin: 0 auto; }
        .header {
            background: #fcfaff;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .header h1 { font-size: 22px; color: #4a3f5e; }
        .header .user { color: #8b7bb5; font-size: 14px; }
        .header .btn-back {
            background: #ede5f5;
            color: #4a3f5e;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
        }
        .header .btn-back:hover { background: #e0d5ec; }
        
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
            background: #fcfaff;
            color: #4a3f5e;
            border: 1.5px solid #ede5f5;
            font-size: 14px;
            transition: 0.2s;
        }
        .quarter-nav .q-link:hover { border-color: #8b7bb5; background: #f8f2fd; }
        .quarter-nav .q-link.current {
            background: #8b7bb5;
            color: white;
            border-color: #8b7bb5;
        }
        .quarter-nav .q-link.past { opacity: 0.6; }
        
        .add-sphere {
            background: #fcfaff;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        .add-sphere input {
            flex: 1;
            padding: 10px 14px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 14px;
            min-width: 150px;
            background: white;
            color: #4a3f5e;
        }
        .add-sphere input:focus { outline: none; border-color: #8b7bb5; }
        .add-sphere button {
            background: #8b7bb5;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 24px;
            cursor: pointer;
            font-size: 14px;
        }
        .add-sphere button:hover { background: #7a69a4; }
        
        .sphere {
            background: #fcfaff;
            border-radius: 12px;
            padding: 18px 20px;
            margin-bottom: 16px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            border-left: 5px solid #d5c8e6;
        }
        .sphere-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 8px;
        }
        .sphere-header h3 { font-size: 18px; color: #4a3f5e; }
        .sphere-header .sphere-actions {
            display: flex;
            gap: 6px;
        }
        .sphere-header .sphere-actions button {
            background: none;
            border: none;
            color: #b5a7cc;
            cursor: pointer;
            font-size: 14px;
            padding: 4px 8px;
            border-radius: 6px;
            transition: 0.2s;
        }
        .sphere-header .sphere-actions button:hover { background: #ede5f5; color: #8b7bb5; }
        
        .task-item {
            background: #faf5ff;
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.04);
        }
        .task-item .task-info { display: flex; align-items: center; gap: 10px; }
        .task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-item .task-actions button:hover { color: #8b7bb5; }
        
        .add-task-form {
            display: flex;
            gap: 8px;
            margin-top: 12px;
            flex-wrap: wrap;
        }
        .add-task-form input {
            flex: 1;
            padding: 8px 12px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 13px;
            min-width: 120px;
            background: white;
            color: #4a3f5e;
        }
        .add-task-form input:focus { outline: none; border-color: #8b7bb5; }
        .add-task-form button {
            background: #8b7bb5;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            cursor: pointer;
            font-size: 13px;
        }
        .add-task-form button:hover { background: #7a69a4; }
        
        .empty-sphere { color: #c5b8d8; font-style: italic; padding: 10px 0; }
        
        .edit-sphere-modal .modal { max-width: 380px; }
        
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
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#d5c8e6; color:#4a3f5e;">Выйти</a>
        </div>
    </div>
    
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
        <input type="text" id="sphereName" placeholder="Название сферы (например: Работа, Здоровье...)" autofocus>
        <button id="addSphereBtn">➕ Добавить сферу</button>
    </div>
    
    <div id="spheresContainer">
        {% for sphere in spheres %}
        <div class="sphere" data-sphere-id="{{ sphere.id }}" data-sphere-name="{{ sphere.name }}">
            <div class="sphere-header">
                <h3>📂 {{ sphere.name }}</h3>
                <div class="sphere-actions">
                    <button class="edit-sphere-btn" data-sphere-id="{{ sphere.id }}" data-sphere-name="{{ sphere.name }}" title="Переименовать">✏️</button>
                    <button class="delete-sphere-btn" data-sphere-id="{{ sphere.id }}" data-sphere-name="{{ sphere.name }}" title="Удалить">🗑️</button>
                </div>
            </div>
            <div id="tasks-{{ loop.index }}">
                {% for task in sphere.tasks %}
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
                <div class="empty-sphere">Нет задач в этой сфере</div>
                {% endfor %}
            </div>
            <div class="add-task-form">
                <input type="text" class="taskInput" placeholder="Новая задача..." autofocus>
                <button class="addTaskBtn" data-sphere="{{ sphere.name }}">➕ Добавить задачу</button>
            </div>
        </div>
        {% else %}
        <div style="text-align:center; padding:40px; color:#c5b8d8; background:#fcfaff; border-radius:12px;">
            <p style="font-size:18px;">📭 Нет сфер</p>
            <p style="font-size:14px;">Добавьте первую сферу выше</p>
        </div>
        {% endfor %}
    </div>
</div>

<!-- Модалка для переименования сферы -->
<div class="modal-overlay edit-sphere-modal" id="editSphereModal">
    <div class="modal">
        <h3>✏️ Переименовать сферу</h3>
        <p class="sub">Введите новое название</p>
        <input type="hidden" id="editSphereId">
        <label for="editSphereName">Новое название</label>
        <input type="text" id="editSphereName" placeholder="Название сферы...">
        <div class="modal-actions">
            <button class="btn-save" id="saveSphereEditBtn">💾 Сохранить</button>
            <button class="btn-cancel" id="cancelSphereEditBtn">Отмена</button>
        </div>
    </div>
</div>

<script>
    const quarter = '{{ quarter }}';
    
    // --- Добавление сферы ---
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
    
    // --- Редактирование сферы ---
    document.querySelectorAll('.edit-sphere-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const sphereId = this.dataset.sphereId;
            const sphereName = this.dataset.sphereName;
            document.getElementById('editSphereId').value = sphereId;
            document.getElementById('editSphereName').value = sphereName;
            document.getElementById('editSphereModal').classList.add('open');
            setTimeout(() => document.getElementById('editSphereName').focus(), 100);
        });
    });
    
    document.getElementById('saveSphereEditBtn').addEventListener('click', function() {
        const sphereId = document.getElementById('editSphereId').value;
        const name = document.getElementById('editSphereName').value.trim();
        if (!name) { alert('Введите название'); return; }
        
        fetch(`/api/sphere/${sphereId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        })
        .then(res => res.json())
        .then(() => location.reload());
    });
    
    document.getElementById('cancelSphereEditBtn').addEventListener('click', function() {
        document.getElementById('editSphereModal').classList.remove('open');
    });
    
    document.getElementById('editSphereName').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') document.getElementById('saveSphereEditBtn').click();
    });
    
    // --- Удаление сферы ---
    document.querySelectorAll('.delete-sphere-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const sphereId = this.dataset.sphereId;
            const sphereName = this.dataset.sphereName;
            if (confirm(`Удалить сферу "${sphereName}" и все её задачи?`)) {
                fetch(`/api/sphere/${sphereId}`, { method: 'DELETE' })
                    .then(() => location.reload());
            }
        });
    });
    
    // --- Добавление задачи в сферу ---
    document.querySelectorAll('.addTaskBtn').forEach(btn => {
        btn.addEventListener('click', function() {
            const sphere = this.dataset.sphere;
            const container = this.closest('.sphere');
            const input = container.querySelector('.taskInput');
            const title = input.value.trim();
            
            if (!title) { alert('Введите название задачи'); return; }
            
            fetch('/api/task/quarter', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, sphere, quarter, date: '' })
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
    
    // --- Выполнение задачи в квартале ---
    document.querySelectorAll('.done-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = this.dataset.taskId;
            fetch(`/api/task/${taskId}/done`, { method: 'POST' })
                .then(() => location.reload());
        });
    });
    
    // --- Удаление задачи в квартале ---
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
            background: #f6f2fd;
            padding: 16px;
            min-height: 100vh;
            color: #4a3f5e;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            background: #fcfaff;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .header h1 { font-size: 22px; color: #4a3f5e; }
        .header .user { color: #8b7bb5; font-size: 14px; }
        .header .btn-back {
            background: #ede5f5;
            color: #4a3f5e;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
        }
        .header .btn-back:hover { background: #e0d5ec; }
        
        .later-layout {
            display: flex;
            gap: 20px;
            align-items: flex-start;
        }
        
        .left-panel {
            flex: 1;
            min-width: 280px;
        }
        .right-panel {
            flex: 1;
            min-width: 280px;
        }
        
        .add-task {
            background: #fcfaff;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        .add-task input {
            flex: 1;
            padding: 10px 14px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 14px;
            min-width: 150px;
            background: white;
            color: #4a3f5e;
        }
        .add-task input:focus { outline: none; border-color: #8b7bb5; }
        .add-task button {
            background: #8b7bb5;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 24px;
            cursor: pointer;
            font-size: 14px;
        }
        .add-task button:hover { background: #7a69a4; }
        
        .task-list {
            background: #fcfaff;
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .task-list .task-item {
            background: #faf5ff;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.04);
        }
        .task-list .task-item .task-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .task-list .task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-list .task-item .task-actions button:hover { color: #8b7bb5; }
        
        .empty-list { color: #c5b8d8; text-align: center; padding: 30px; }
        
        .group-section {
            background: #fcfaff;
            border-radius: 12px;
            padding: 18px 20px;
            margin-bottom: 16px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .group-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }
        .group-header h3 { font-size: 16px; color: #4a3f5e; }
        .group-header .delete-group {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
        }
        .group-header .delete-group:hover { color: #e74c3c; }
        
        .add-group {
            background: #fcfaff;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
            margin-bottom: 16px;
        }
        .add-group input {
            flex: 1;
            padding: 10px 14px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 14px;
            min-width: 150px;
            background: white;
            color: #4a3f5e;
        }
        .add-group input:focus { outline: none; border-color: #8b7bb5; }
        .add-group button {
            background: #8b7bb5;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 24px;
            cursor: pointer;
            font-size: 14px;
        }
        .add-group button:hover { background: #7a69a4; }
        
        .group-task-item {
            background: #faf5ff;
            border-radius: 8px;
            padding: 8px 14px;
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.04);
            font-size: 14px;
        }
        .group-task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .group-task-item .task-actions button:hover { color: #8b7bb5; }
        
        .add-group-task {
            display: flex;
            gap: 6px;
            margin-top: 8px;
            flex-wrap: wrap;
        }
        .add-group-task input {
            flex: 1;
            padding: 6px 10px;
            border: 1.5px solid #ede5f5;
            border-radius: 6px;
            font-size: 13px;
            background: white;
            color: #4a3f5e;
            min-width: 100px;
        }
        .add-group-task input:focus { outline: none; border-color: #8b7bb5; }
        .add-group-task button {
            background: #8b7bb5;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 6px 14px;
            cursor: pointer;
            font-size: 13px;
        }
        .add-group-task button:hover { background: #7a69a4; }
        
        .move-to-group-btn {
            background: none;
            border: none;
            color: #b5a7cc;
            cursor: pointer;
            font-size: 16px;
            padding: 0 6px;
        }
        .move-to-group-btn:hover { color: #8b7bb5; }
        
        .group-task-list {
            margin-top: 6px;
        }
        
        @media (max-width: 900px) {
            .later-layout { flex-direction: column; }
            .left-panel, .right-panel { flex: 1 1 100%; }
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
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#d5c8e6; color:#4a3f5e;">Выйти</a>
        </div>
    </div>
    
    <div class="later-layout">
        <div class="left-panel">
            <div class="add-task">
                <input type="text" id="laterTaskInput" placeholder="Новая задача в общий список..." autofocus>
                <button id="addLaterBtn">➕ Добавить</button>
            </div>
            <div class="task-list" id="laterTasks">
                {% for task in tasks %}
                <div class="task-item" data-task-id="{{ task.id }}">
                    <div class="task-info">
                        <span>{{ task.title }}</span>
                    </div>
                    <div class="task-actions">
                        <button class="move-to-group-btn" data-task-id="{{ task.id }}" title="Переместить в группу">📂</button>
                        <button class="done-btn" data-task-id="{{ task.id }}">✅</button>
                        <button class="delete-btn" data-task-id="{{ task.id }}">🗑️</button>
                    </div>
                </div>
                {% else %}
                <div class="empty-list">📭 Здесь пока пусто. Добавьте задачи в общий список.</div>
                {% endfor %}
            </div>
        </div>
        
        <div class="right-panel">
            <div class="add-group">
                <input type="text" id="newGroupInput" placeholder="Название группы (например: Идеи, Проекты...)">
                <button id="addGroupBtn">➕ Создать группу</button>
            </div>
            
            {% for group in groups %}
            <div class="group-section" data-group="{{ group.name }}">
                <div class="group-header">
                    <h3>📂 {{ group.name }}</h3>
                    <button class="delete-group" data-group-id="{{ group.id }}" data-group-name="{{ group.name }}">✕</button>
                </div>
                <div class="group-task-list">
                    {% for task in group.tasks %}
                    <div class="group-task-item" data-task-id="{{ task.id }}">
                        <span>{{ task.title }}</span>
                        <div class="task-actions">
                            <button class="done-btn" data-task-id="{{ task.id }}">✅</button>
                            <button class="delete-btn" data-task-id="{{ task.id }}">🗑️</button>
                        </div>
                    </div>
                    {% else %}
                    <div class="empty-list" style="padding:10px; font-size:13px;">Нет задач в этой группе</div>
                    {% endfor %}
                </div>
                <div class="add-group-task">
                    <input type="text" class="group-task-input" placeholder="Новая задача в группу...">
                    <button class="add-group-task-btn" data-group="{{ group.name }}">➕</button>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</div>

<script>
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
        if (e.key === 'Enter') document.getElementById('addLaterBtn').click();
    });
    
    document.getElementById('addGroupBtn').addEventListener('click', function() {
        const input = document.getElementById('newGroupInput');
        const name = input.value.trim();
        if (!name) { alert('Введите название группы'); return; }
        
        fetch('/api/later/group', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        })
        .then(res => res.json())
        .then(() => location.reload());
    });
    
    document.getElementById('newGroupInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') document.getElementById('addGroupBtn').click();
    });
    
    document.querySelectorAll('.move-to-group-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = this.dataset.taskId;
            const groupName = prompt('Введите название группы, куда переместить задачу:');
            if (!groupName) return;
            
            fetch(`/api/task/${taskId}/move_to_later_group`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ group: groupName })
            })
            .then(res => res.json())
            .then(() => location.reload());
        });
    });
    
    document.querySelectorAll('.add-group-task-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const group = this.dataset.group;
            const container = this.closest('.group-section');
            const input = container.querySelector('.group-task-input');
            const title = input.value.trim();
            if (!title) { alert('Введите название задачи'); return; }
            
            fetch('/api/task/later/group', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, group })
            })
            .then(res => res.json())
            .then(() => location.reload());
        });
    });
    
    document.querySelectorAll('.group-task-input').forEach(input => {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                this.closest('.group-section').querySelector('.add-group-task-btn').click();
            }
        });
    });
    
    document.querySelectorAll('.delete-group').forEach(btn => {
        btn.addEventListener('click', function() {
            const groupId = this.dataset.groupId;
            const groupName = this.dataset.groupName;
            if (confirm(`Удалить группу "${groupName}"? Задачи вернутся в общий список.`)) {
                fetch(`/api/later/group/${groupId}`, { method: 'DELETE' })
                    .then(() => location.reload());
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

DONE_PAGE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>✅ Готово — Мой органайзер</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f6f2fd;
            padding: 16px;
            min-height: 100vh;
            color: #4a3f5e;
        }
        .container { max-width: 800px; margin: 0 auto; }
        .header {
            background: #fcfaff;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .header h1 { font-size: 22px; color: #4a3f5e; }
        .header .user { color: #8b7bb5; font-size: 14px; }
        .header .btn-back {
            background: #ede5f5;
            color: #4a3f5e;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
        }
        .header .btn-back:hover { background: #e0d5ec; }
        
        .task-list {
            background: #fcfaff;
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .task-item {
            background: #faf5ff;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.04);
            border-left: 4px solid #27ae60;
        }
        .task-item .task-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .task-item .task-info .completed-time {
            font-size: 12px;
            color: #b5a7cc;
        }
        .task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
        }
        .task-item .task-actions button:hover { color: #8b7bb5; }
        .task-item .task-actions .restore-btn:hover { color: #27ae60; }
        
        .empty-list { color: #c5b8d8; text-align: center; padding: 30px; }
        
        .info-note {
            margin-top: 12px;
            padding: 12px 16px;
            background: #f0e8fa;
            border-radius: 8px;
            font-size: 13px;
            color: #8b7bb5;
            text-align: center;
        }
        
        @media (max-width: 600px) {
            .header { flex-direction: column; text-align: center; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>✅ Готово</h1>
        <div>
            <span class="user">👤 {{ username }}</span>
            <a href="/" class="btn-back" style="margin-left:12px;">← Назад</a>
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#d5c8e6; color:#4a3f5e;">Выйти</a>
        </div>
    </div>
    
    <div class="task-list" id="doneTasks">
        {% for task in tasks %}
        <div class="task-item" data-task-id="{{ task.id }}">
            <div class="task-info">
                <span>{{ task.title }}</span>
                <span class="completed-time">✅ {{ task.completed_at }}</span>
            </div>
            <div class="task-actions">
                <button class="restore-btn" data-task-id="{{ task.id }}" title="Восстановить">↩️</button>
                <button class="delete-btn" data-task-id="{{ task.id }}" title="Удалить навсегда">🗑️</button>
            </div>
        </div>
        {% else %}
        <div class="empty-list">📭 Здесь пока пусто. Выполненные задачи появятся здесь на 24 часа.</div>
        {% endfor %}
    </div>
    <div class="info-note">⏳ Задачи хранятся 24 часа, затем удаляются автоматически</div>
</div>

<script>
    function loadDoneTasks() {
        fetch('/api/tasks/done')
            .then(res => res.json())
            .then(tasks => {
                const container = document.getElementById('doneTasks');
                if (tasks.length === 0) {
                    container.innerHTML = '<div class="empty-list">📭 Здесь пока пусто. Выполненные задачи появятся здесь на 24 часа.</div>';
                    return;
                }
                container.innerHTML = '';
                tasks.forEach(task => {
                    const item = document.createElement('div');
                    item.className = 'task-item';
                    item.dataset.taskId = task.id;
                    const completedTime = task.completed_at ? new Date(task.completed_at).toLocaleString('ru-RU') : 'только что';
                    item.innerHTML = `
                        <div class="task-info">
                            <span>${task.title}</span>
                            <span class="completed-time">✅ ${completedTime}</span>
                        </div>
                        <div class="task-actions">
                            <button class="restore-btn" data-task-id="${task.id}" title="Восстановить">↩️</button>
                            <button class="delete-btn" data-task-id="${task.id}" title="Удалить навсегда">🗑️</button>
                        </div>
                    `;
                    container.appendChild(item);
                });
                
                document.querySelectorAll('.restore-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const taskId = this.dataset.taskId;
                        fetch(`/api/task/${taskId}/restore`, { method: 'POST' })
                            .then(() => loadDoneTasks());
                    });
                });
                
                document.querySelectorAll('.delete-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const taskId = this.dataset.taskId;
                        if (confirm('Удалить задачу навсегда?')) {
                            fetch(`/api/task/${taskId}`, { method: 'DELETE' })
                                .then(() => loadDoneTasks());
                        }
                    });
                });
            });
    }
    
    loadDoneTasks();
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
        body { font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f6f2fd; margin: 0; color: #4a3f5e; }
        .card { background: #fcfaff; padding: 40px; border-radius: 16px; box-shadow: 0 4px 30px rgba(139, 123, 181, 0.10); width: 100%; max-width: 360px; }
        h2 { margin-bottom: 20px; color: #4a3f5e; }
        input { width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ede5f5; border-radius: 8px; font-size: 14px; box-sizing: border-box; background: white; color: #4a3f5e; }
        input:focus { outline: none; border-color: #8b7bb5; }
        button { width: 100%; padding: 12px; background: #8b7bb5; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; }
        button:hover { background: #7a69a4; }
        .error { color: #d5a0a0; font-size: 14px; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 16px; font-size: 14px; color: #b5a7cc; }
        .link a { color: #8b7bb5; text-decoration: none; }
        .link a:hover { text-decoration: underline; }
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
        body { font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f6f2fd; margin: 0; color: #4a3f5e; }
        .card { background: #fcfaff; padding: 40px; border-radius: 16px; box-shadow: 0 4px 30px rgba(139, 123, 181, 0.10); width: 100%; max-width: 360px; }
        h2 { margin-bottom: 20px; color: #4a3f5e; }
        input { width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ede5f5; border-radius: 8px; font-size: 14px; box-sizing: border-box; background: white; color: #4a3f5e; }
        input:focus { outline: none; border-color: #8b7bb5; }
        button { width: 100%; padding: 12px; background: #8b7bb5; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; }
        button:hover { background: #7a69a4; }
        .error { color: #d5a0a0; font-size: 14px; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 16px; font-size: 14px; color: #b5a7cc; }
        .link a { color: #8b7bb5; text-decoration: none; }
        .link a:hover { text-decoration: underline; }
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

# --- СТРАНИЦА "ГОТОВО" ---
@app.route('/done')
def done_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    yesterday = datetime.now() - timedelta(days=1)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND completed_at >= %s
        ORDER BY completed_at DESC
    ''', (user_id, 'done', yesterday))
    tasks = cur.fetchall()
    conn.close()
    
    return render_template_string(DONE_PAGE, 
                                   tasks=tasks,
                                   username=session.get('username', 'Пользователь'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)