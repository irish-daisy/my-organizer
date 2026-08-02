import os
os.environ['TZ'] = 'Europe/Moscow'
from flask import Flask, request, render_template_string, redirect, session, url_for, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
from datetime import datetime, timedelta
import json
import re

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
            password TEXT NOT NULL,
            email TEXT,
            phone TEXT
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            category TEXT DEFAULT 'later',
            date TEXT,
            duration TEXT,
            repeat_type TEXT DEFAULT 'none',
            repeat_day INTEGER,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            quarter TEXT,
            sphere TEXT,
            later_group TEXT,
            sphere_id INTEGER,
            completed_at TIMESTAMP,
            future BOOLEAN DEFAULT FALSE,
            comment TEXT
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

def get_weekday_ru(date_str):
    """Возвращает день недели на русском (пн, вт, ср...)"""
    if not date_str:
        return ''
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        weekdays = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
        return weekdays[d.weekday()]
    except:
        return ''

def format_date_ru(date_str):
    """Форматирует дату в формате '3 августа'"""
    if not date_str or date_str == '':
        return ''
    months = {
        '01': 'января', '02': 'февраля', '03': 'марта', '04': 'апреля',
        '05': 'мая', '06': 'июня', '07': 'июля', '08': 'августа',
        '09': 'сентября', '10': 'октября', '11': 'ноября', '12': 'декабря'
    }
    parts = date_str.split('-')
    if len(parts) == 3:
        day = str(int(parts[2]))
        month = months.get(parts[1], parts[1])
        return f"{day} {month}"
    return date_str

def format_date_with_weekday(date_str):
    """Форматирует дату в формате '3 августа, пн'"""
    if not date_str or date_str == '':
        return ''
    weekday = get_weekday_ru(date_str)
    date_formatted = format_date_ru(date_str)
    if weekday:
        return f"{date_formatted}, {weekday}"
    return date_formatted

def move_overdue_tasks_to_backlog(user_id):
    """
    Переносит просроченные задачи на сегодня, СОХРАНЯЯ их категорию.
    Задачи с датой < сегодня переносятся на сегодня.
    Категория (urgent, work, home, personal, waiting) НЕ МЕНЯЕТСЯ.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now().date()
    today_str = today.strftime('%Y-%m-%d')
    
    # Переносим ТОЛЬКО просроченные задачи (дата < сегодня)
    # и сохраняем их категорию (не сбрасываем в 'later')
    cur.execute('''
        UPDATE tasks 
        SET date = %s
        WHERE user_id = %s AND status = 'active' AND quarter IS NULL 
        AND date IS NOT NULL AND date != '' AND date::date < %s
    ''', (today_str, user_id, today_str))
    
    conn.commit()
    conn.close()

# Функция move_tasks_to_next_day полностью удалена
# Задачи на сегодня НЕ ПЕРЕНОСЯТСЯ на завтра автоматически

# --- ГЛАВНАЯ СТРАНИЦА ---
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    
    # Переносим просроченные задачи на сегодня (сохраняя категории)
    move_overdue_tasks_to_backlog(user_id)
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    view_date_str = datetime.now().strftime('%Y-%m-%d')
    view_date = datetime.now().date()
    
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND quarter IS NULL 
        AND (date = %s OR date IS NULL OR date = '')
        ORDER BY id ASC
    ''', (user_id, 'active', view_date_str))
    tasks = cur.fetchall()
    conn.close()
    
    categories = {
        'focus': [],
        'urgent': [],
        'work': [],
        'home': [],
        'personal': [],
        'waiting': []
    }
    
    category_order = ['urgent', 'work', 'home', 'personal']
    
    for task in tasks:
        cat = task['category'] if task['category'] in categories else 'later'
        if cat != 'later':
            categories[cat].append(dict(task))
    
    current_quarter = get_current_quarter()
    
    today = datetime.now().date()
    is_today = view_date == today
    is_tomorrow = view_date == today + timedelta(days=1)
    
    date_label = format_date_with_weekday(view_date.strftime('%Y-%m-%d'))
    
    return render_template_string(MAIN_PAGE, 
                                   categories=categories,
                                   category_order=category_order,
                                   username=session.get('username', 'Пользователь'),
                                   current_quarter=current_quarter,
                                   view_date=view_date_str,
                                   date_label=date_label,
                                   is_today=is_today,
                                   is_tomorrow=is_tomorrow,
                                   prev_date=(view_date - timedelta(days=1)).strftime('%Y-%m-%d'),
                                   next_date=(view_date + timedelta(days=1)).strftime('%Y-%m-%d'),
                                   format_date_with_weekday=format_date_with_weekday)

# --- СТРАНИЦА "БУДУЩИЕ" ---
@app.route('/future')
def future_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    today = datetime.now().date()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND quarter IS NULL 
        AND date IS NOT NULL AND date != '' AND date::date > %s
        ORDER BY date ASC
    ''', (user_id, 'active', today))
    tasks = cur.fetchall()
    conn.close()
    
    tasks_by_date = {}
    for task in tasks:
        if task['date'] and task['date'] != '':
            date_key = task['date']
            if date_key not in tasks_by_date:
                tasks_by_date[date_key] = []
            tasks_by_date[date_key].append(dict(task))
    
    sorted_dates = sorted(tasks_by_date.keys())
    
    return render_template_string(FUTURE_PAGE, 
                                   tasks_by_date=tasks_by_date,
                                   sorted_dates=sorted_dates,
                                   format_date_with_weekday=format_date_with_weekday,
                                   username=session.get('username', 'Пользователь'))

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
        cur.execute('SELECT * FROM tasks WHERE user_id = %s AND sphere_id = %s AND quarter = %s AND status = %s ORDER BY created_at ASC', 
                   (user_id, sphere['id'], quarter, 'active'))
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
        WHERE user_id = %s AND category = %s AND status = %s AND quarter IS NULL AND later_group IS NULL
        ORDER BY created_at DESC
    ''', (user_id, 'later', 'active'))
    tasks = cur.fetchall()
    
    cur.execute('SELECT * FROM later_groups WHERE user_id = %s ORDER BY created_at ASC', (user_id,))
    groups = cur.fetchall()
    
    for group in groups:
        cur.execute('''
            SELECT * FROM tasks 
            WHERE user_id = %s AND later_group = %s AND status = %s AND quarter IS NULL
            ORDER BY created_at DESC
        ''', (user_id, group['name'], 'active'))
        group['tasks'] = cur.fetchall()
    
    conn.close()
    
    return render_template_string(LATER_PAGE, 
                                   tasks=tasks,
                                   groups=groups,
                                   username=session.get('username', 'Пользователь'))

# --- СТРАНИЦА "ГОТОВО" ---
@app.route('/done')
def done_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cutoff = datetime.now() - timedelta(hours=36)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND completed_at >= %s
        ORDER BY completed_at DESC
    ''', (user_id, 'done', cutoff))
    tasks = cur.fetchall()
    conn.close()
    
    tasks_by_date = {}
    for task in tasks:
        if task['completed_at']:
            date_key = task['completed_at'].strftime('%Y-%m-%d')
            if date_key not in tasks_by_date:
                tasks_by_date[date_key] = []
            tasks_by_date[date_key].append(dict(task))
    
    sorted_dates = sorted(tasks_by_date.keys(), reverse=True)
    
    return render_template_string(DONE_PAGE, 
                                   tasks_by_date=tasks_by_date,
                                   sorted_dates=sorted_dates,
                                   format_date_with_weekday=format_date_with_weekday,
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
        INSERT INTO tasks (user_id, title, category, date, duration, repeat_type, repeat_day, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', '', 'none', None, 'active'))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task added to later'})

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
        INSERT INTO tasks (user_id, title, category, date, duration, repeat_type, repeat_day, status, later_group)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', '', 'none', None, 'active', group))
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
    cur.execute('SELECT name FROM spheres WHERE id = %s AND user_id = %s', (sphere_id, session['user_id']))
    sphere = cur.fetchone()
    if sphere:
        cur.execute('UPDATE tasks SET category = %s, sphere = NULL, quarter = NULL, sphere_id = NULL WHERE user_id = %s AND sphere = %s', ('later', session['user_id'], sphere[0]))
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
    
    if not title or not sphere or not quarter:
        return jsonify({'error': 'Title, sphere and quarter are required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id FROM spheres WHERE user_id = %s AND name = %s AND quarter = %s', (session['user_id'], sphere, quarter))
    sphere_result = cur.fetchone()
    sphere_id = sphere_result[0] if sphere_result else None
    
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, duration, status, quarter, sphere, sphere_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', '', 'active', quarter, sphere, sphere_id))
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
    duration = data.get('duration', '')
    repeat_type = data.get('repeat_type', 'none')
    repeat_day = data.get('repeat_day')
    comment = data.get('comment', '')
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, date, duration, repeat_type, repeat_day, status, comment)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, category, date, duration, repeat_type, repeat_day, 'active', comment))
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
        INSERT INTO tasks (user_id, title, category, date, duration, repeat_type, repeat_day, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, 'later', '', '', 'none', None, 'active'))
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
    duration = data.get('duration', '')
    repeat_type = data.get('repeat_type', 'none')
    repeat_day = data.get('repeat_day')
    comment = data.get('comment', '')
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        UPDATE tasks SET 
            title = %s, category = %s, date = %s, duration = %s, repeat_type = %s, repeat_day = %s, comment = %s
        WHERE id = %s AND user_id = %s
    ''', (title, category, date, duration, repeat_type, repeat_day, comment, task_id, session['user_id']))
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

# --- API: Получить задачу по ID ---
@app.route('/api/task/<int:task_id>')
def get_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM tasks WHERE id = %s AND user_id = %s', (task_id, session['user_id']))
    task = cur.fetchone()
    conn.close()
    
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    return jsonify(dict(task))

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
    
    cur.execute('UPDATE tasks SET status = %s, completed_at = %s WHERE id = %s', ('done', datetime.now(), task_id))
    
    if task['repeat_type'] == 'daily':
        if task['date'] and task['date'] != '':
            try:
                current_date = datetime.strptime(task['date'], '%Y-%m-%d').date()
                new_date = current_date + timedelta(days=1)
            except:
                new_date = datetime.now().date() + timedelta(days=1)
        else:
            new_date = datetime.now().date() + timedelta(days=1)
        
        cur.execute('''
            INSERT INTO tasks (user_id, title, category, date, duration, repeat_type, repeat_day, status, quarter, sphere, later_group, sphere_id, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (task['user_id'], task['title'], task['category'], new_date.strftime('%Y-%m-%d'), task.get('duration', ''), task['repeat_type'], task['repeat_day'], 'active', task.get('quarter'), task.get('sphere'), task.get('later_group'), task.get('sphere_id'), task.get('comment', '')))
    elif task['repeat_type'] == 'weekly' and task['repeat_day'] is not None:
        if task['date'] and task['date'] != '':
            try:
                current_date = datetime.strptime(task['date'], '%Y-%m-%d').date()
            except:
                current_date = datetime.now().date()
        else:
            current_date = datetime.now().date()
        
        days_ahead = task['repeat_day'] - current_date.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        new_date = current_date + timedelta(days=days_ahead)
        
        cur.execute('''
            INSERT INTO tasks (user_id, title, category, date, duration, repeat_type, repeat_day, status, quarter, sphere, later_group, sphere_id, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (task['user_id'], task['title'], task['category'], new_date.strftime('%Y-%m-%d'), task.get('duration', ''), task['repeat_type'], task['repeat_day'], 'active', task.get('quarter'), task.get('sphere'), task.get('later_group'), task.get('sphere_id'), task.get('comment', '')))
    
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

# --- API: Получить выполненные задачи (за 36 часов) ---
@app.route('/api/tasks/done')
def get_done_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cutoff = datetime.now() - timedelta(hours=36)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND completed_at >= %s
        ORDER BY completed_at DESC
    ''', (session['user_id'], 'done', cutoff))
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

# --- API: Получить задачи на сегодня ---
@app.route('/api/tasks')
def get_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND quarter IS NULL 
        AND (date = %s OR date IS NULL OR date = '')
        ORDER BY id ASC
    ''', (session['user_id'], 'active', today))
    tasks = cur.fetchall()
    conn.close()
    
    result = []
    for task in tasks:
        result.append(dict(task))
    
    return jsonify(result)

# --- API: Получить задачи на конкретную дату ---
@app.route('/api/tasks/date/<date_str>')
def get_tasks_by_date(date_str):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND quarter IS NULL 
        AND (date = %s OR date IS NULL OR date = '')
        ORDER BY id ASC
    ''', (session['user_id'], 'active', date_str))
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
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        
        if not username or not password:
            return render_template_string(REGISTER_PAGE, error='Заполните все обязательные поля')
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO users (username, password, email, phone) VALUES (%s, %s, %s, %s)', 
                       (username, password, email, phone))
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
# (ВСЕ ШАБЛОНЫ ИЗ ВАШЕГО ФАЙЛА - MAIN_PAGE, FUTURE_PAGE, QUARTER_PAGE, LATER_PAGE, DONE_PAGE, REGISTER_PAGE, LOGIN_PAGE)
# Я не включаю их сюда повторно, чтобы не занимать место, но они должны быть здесь

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)