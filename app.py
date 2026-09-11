# -*- coding: utf-8 -*-
import os
os.environ['TZ'] = 'Europe/Moscow'
from flask import Flask, request, render_template_string, redirect, session, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from calendar import monthrange

app = Flask(__name__)
# В продакшене обязательно задайте SECRET_KEY в переменных окружения.
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=90)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# --- ПОДКЛЮЧЕНИЕ К POSTGRESQL ---
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError(
            'Не задана переменная окружения DATABASE_URL. '             'Укажите строку подключения к PostgreSQL.'
        )
    return psycopg2.connect(database_url)

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (ДОБАВЛЕНЫ deadline_date И deadline_time) ---
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
            default_category TEXT DEFAULT 'personal',
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
            comment TEXT,
            position INTEGER DEFAULT 0
        )
    ''')
    
    # Мягкая миграция старых баз: добавляем новые поля без потери данных.
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deadline_date TEXT")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deadline_time TEXT")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at_epoch BIGINT")

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
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS subtasks (
            id SERIAL PRIMARY KEY,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            is_done BOOLEAN DEFAULT FALSE,
            position INTEGER DEFAULT 0,
            comment TEXT DEFAULT '',
            deadline_date TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute("ALTER TABLE subtasks ADD COLUMN IF NOT EXISTS comment TEXT DEFAULT ''")
    cur.execute("ALTER TABLE subtasks ADD COLUMN IF NOT EXISTS deadline_date TEXT DEFAULT ''")

    conn.commit()
    conn.close()

init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_now_utc():
    """Current UTC time stored as a naive timestamp for PostgreSQL TIMESTAMP columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def get_now_epoch():
    """Unix timestamp in seconds; timezone-independent."""
    return int(datetime.now(timezone.utc).timestamp())

def next_weekday_after(base_date, target_day):
    """Nearest strictly-next weekday. repeat_day uses 0=Sun, 1=Mon ... 6=Sat."""
    current = (base_date.weekday() + 1) % 7
    days_ahead = (int(target_day) - current) % 7
    if days_ahead == 0:
        days_ahead = 7
    return base_date + timedelta(days=days_ahead)

def next_biweekly_date(task_date_str, completion_date, target_day):
    """Keep a 14-day cadence and always land on the selected weekday."""
    try:
        scheduled = datetime.strptime(task_date_str or '', '%Y-%m-%d').date()
    except (ValueError, TypeError):
        # Для старой задачи без даты сначала находим выбранный день недели.
        return next_weekday_after(completion_date, target_day)
    candidate = scheduled + timedelta(days=14)
    candidate_weekday = (candidate.weekday() + 1) % 7
    shift = (int(target_day) - candidate_weekday) % 7
    candidate += timedelta(days=shift)
    while candidate <= completion_date:
        candidate += timedelta(days=14)
    return candidate

def _month_add(year, month, delta=1):
    month0 = (month - 1) + delta
    return year + month0 // 12, month0 % 12 + 1

def monthly_occurrence(year, month, requested_day):
    day = max(1, min(int(requested_day), 31))
    return datetime(year, month, min(day, monthrange(year, month)[1])).date()

def next_monthly_date(task_date_str, completion_date, requested_day):
    """Next monthly occurrence; 29/30/31 use the month's last day when needed."""
    try:
        scheduled = datetime.strptime(task_date_str or '', '%Y-%m-%d').date()
        year, month = _month_add(scheduled.year, scheduled.month, 1)
    except (ValueError, TypeError):
        year, month = _month_add(completion_date.year, completion_date.month, 1)
    candidate = monthly_occurrence(year, month, requested_day)
    while candidate <= completion_date:
        year, month = _month_add(year, month, 1)
        candidate = monthly_occurrence(year, month, requested_day)
    return candidate

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
    if not date_str:
        return ''
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        weekdays = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
        return weekdays[d.weekday()]
    except:
        return ''

def format_date_ru(date_str):
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
    if not date_str or date_str == '':
        return ''
    weekday = get_weekday_ru(date_str)
    date_formatted = format_date_ru(date_str)
    if weekday:
        return f"{date_formatted}, {weekday}"
    return date_formatted

def get_now_msk():
    """Возвращает текущее время по МСК (UTC+3)"""
    return datetime.now(timezone.utc) + timedelta(hours=3)

def format_deadline(deadline_date, deadline_time, current_date):
    """Форматирует дедлайн для отображения на карточке задачи"""
    if not deadline_date:
        return ''
    
    try:
        deadline_date_obj = datetime.strptime(deadline_date, '%Y-%m-%d').date()
    except:
        return ''
    
    today = current_date or datetime.now().date()
    days_diff = (deadline_date_obj - today).days
    
    if days_diff < 0:
        return '🔴 просрочен!'
    elif days_diff == 0:
        # Сегодня
        if deadline_time and deadline_time.strip():
            return f'⏰ до {deadline_time}'
        else:
            return '⏰ сегодня'
    elif days_diff == 1:
        # Завтра
        return f'⏰ до завтра'
    else:
        # В будущем
        return f'⏰ до {format_date_ru(deadline_date)}'
    
    return ''

def active_task_order_key(task):
    """Sort active tasks inside a block. Focus keeps manual order; other blocks prioritize deadlines."""
    position = task.get('position') or 0
    task_id = task.get('id') or 0
    if task.get('category') == 'focus':
        return (0, '', '', position, task_id)

    deadline_date = (task.get('deadline_date') or '').strip()
    deadline_time = (task.get('deadline_time') or '').strip()
    if not deadline_date:
        return (2, '9999-12-31', '23:59', position, task_id)
    return (1, deadline_date, deadline_time or '23:59', position, task_id)


def move_overdue_tasks_to_backlog(user_id):
    """Переносит ВСЕ невыполненные просроченные задачи на сегодня."""
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now().date()
    today_str = today.strftime('%Y-%m-%d')
    
    cur.execute('''
        UPDATE tasks 
        SET date = %s
        WHERE user_id = %s AND status = 'active' AND quarter IS NULL 
        AND date IS NOT NULL AND date != '' AND date::date < %s
    ''', (today_str, user_id, today_str))
    
    conn.commit()
    conn.close()

# --- ГЛАВНАЯ СТРАНИЦА ---
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
    
    user_id = session['user_id']
    
    move_overdue_tasks_to_backlog(user_id)
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    view_date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    view_date = datetime.strptime(view_date_str, '%Y-%m-%d').date()
    
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND quarter IS NULL 
        AND date = %s
        ORDER BY position ASC, id ASC
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

    # Во всех обычных блоках дедлайны выше задач без дедлайна.
    # В «Фокусе» сохраняем только ручной порядок — иначе drag&drop визуально откатывается назад.
    for category_tasks in categories.values():
        category_tasks.sort(key=active_task_order_key)
    
    current_quarter = get_current_quarter()
    
    today = datetime.now().date()
    is_today = view_date == today
    is_tomorrow = view_date == today + timedelta(days=1)
    
    date_label = format_date_with_weekday(view_date_str)
    
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
                                   format_date_with_weekday=format_date_with_weekday,
                                   format_deadline=format_deadline)

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
        ORDER BY date ASC, position ASC
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

# --- СТРАНИЦА КВАРТАЛОВ ---
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
        cur.execute('''
            SELECT * FROM tasks
            WHERE user_id = %s AND sphere_id = %s AND quarter = %s AND status = %s
            ORDER BY position ASC, created_at ASC, id ASC
        ''', (user_id, sphere['id'], quarter, 'active'))
        sphere_tasks = cur.fetchall()
        for task in sphere_tasks:
            cur.execute('''
                SELECT * FROM subtasks
                WHERE user_id = %s AND task_id = %s
                ORDER BY is_done ASC, position ASC, created_at ASC, id ASC
            ''', (user_id, task['id']))
            task['subtasks'] = cur.fetchall()
        sphere['tasks'] = sphere_tasks

    # Отдельный блок выполненных задач выбранного квартала.
    cur.execute('''
        SELECT * FROM tasks
        WHERE user_id = %s AND quarter = %s AND status = 'done'
        ORDER BY completed_at_epoch DESC NULLS LAST, completed_at DESC NULLS LAST, id DESC
    ''', (user_id, quarter))
    completed_tasks = cur.fetchall()
    for task in completed_tasks:
        cur.execute('''
            SELECT * FROM subtasks
            WHERE user_id = %s AND task_id = %s
            ORDER BY is_done ASC, position ASC, created_at ASC, id ASC
        ''', (user_id, task['id']))
        task['subtasks'] = cur.fetchall()

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
                                   completed_tasks=completed_tasks,
                                   username=session.get('username', 'Пользователь'),
                                   current_quarter=current_q,
                                   format_date_ru=format_date_ru)

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
    
    cutoff = get_now_utc() - timedelta(hours=36)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND completed_at >= %s
        ORDER BY completed_at DESC
    ''', (user_id, 'done', cutoff))
    tasks = cur.fetchall()
    conn.close()
    
    # Дату и время выполнения группирует браузер по часовому поясу устройства.
    return render_template_string(DONE_PAGE, username=session.get('username', 'Пользователь'))

# --- API: Добавить задачу в "Позже" ---
@app.route('/api/task/later', methods=['POST'])
def add_later_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, default_category, date, duration, repeat_type, repeat_day, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    ''', (session['user_id'], title, 'later', 'later', '', '', 'none', None, 'active'))
    task = cur.fetchone()
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'task': dict(task)})

# --- API: Добавить группу в "Позже" ---
@app.route('/api/later/group', methods=['POST'])
def add_later_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('INSERT INTO later_groups (user_id, name) VALUES (%s, %s) RETURNING id, name', (session['user_id'], name))
    group = cur.fetchone()
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'group': dict(group)})

@app.route('/api/later/groups')
def get_later_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT id, name FROM later_groups WHERE user_id = %s ORDER BY created_at ASC', (session['user_id'],))
    groups = cur.fetchall()
    conn.close()
    return jsonify([dict(group) for group in groups])

# --- API: Добавить задачу в группу "Позже" ---
@app.route('/api/task/later/group', methods=['POST'])
def add_task_to_later_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    title = data.get('title', '').strip()
    group = data.get('group', '').strip()
    if not title or not group:
        return jsonify({'error': 'Title and group are required'}), 400
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT name FROM later_groups WHERE user_id = %s AND name = %s', (session['user_id'], group))
    if not cur.fetchone():
        conn.close()
        return jsonify({'error': 'Group not found'}), 404
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, default_category, date, duration, repeat_type, repeat_day, status, later_group)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    ''', (session['user_id'], title, 'later', 'later', '', '', 'none', None, 'active', group))
    task = cur.fetchone()
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'task': dict(task)})

# --- API: Переместить задачу в группу "Позже" ---
@app.route('/api/task/<int:task_id>/move_to_later_group', methods=['PUT'])
def move_to_later_group(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    group = data.get('group', '').strip()
    if not group:
        return jsonify({'error': 'Group is required'}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM later_groups WHERE user_id = %s AND name = %s', (session['user_id'], group))
    if not cur.fetchone():
        conn.close()
        return jsonify({'error': 'Group not found'}), 404
    cur.execute('UPDATE tasks SET later_group = %s WHERE id = %s AND user_id = %s', (group, task_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'group': group})

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
        cur.execute('UPDATE tasks SET category = %s, sphere = NULL, quarter = NULL, sphere_id = NULL WHERE user_id = %s AND sphere_id = %s', ('later', session['user_id'], sphere_id))
    cur.execute('DELETE FROM spheres WHERE id = %s AND user_id = %s', (sphere_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Sphere deleted'})

# --- API: Добавить задачу в сферу (квартал) ---
@app.route('/api/task/quarter', methods=['POST'])
def add_quarter_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    title = data.get('title', '').strip()
    sphere = data.get('sphere', '').strip()
    quarter = data.get('quarter', '').strip()

    if not title or not sphere or not quarter:
        return jsonify({'error': 'Title, sphere and quarter are required'}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT id FROM spheres WHERE user_id = %s AND name = %s AND quarter = %s',
                (session['user_id'], sphere, quarter))
    sphere_result = cur.fetchone()
    if not sphere_result:
        conn.close()
        return jsonify({'error': 'Sphere not found'}), 404
    sphere_id = sphere_result['id']

    cur.execute("""
        SELECT COALESCE(MAX(position), -1) + 1 AS next_position
        FROM tasks
        WHERE user_id = %s AND sphere_id = %s AND quarter = %s AND status = 'active'
    """, (session['user_id'], sphere_id, quarter))
    position = cur.fetchone()['next_position']

    cur.execute("""
        INSERT INTO tasks (
            user_id, title, category, default_category, date, duration, status,
            quarter, sphere, sphere_id, position, comment, deadline_date
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    """, (session['user_id'], title, 'later', 'later', '', '', 'active',
          quarter, sphere, sphere_id, position, '', ''))
    task = dict(cur.fetchone())
    task['subtasks'] = []
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'task': task})


@app.route('/api/task/<int:task_id>/quarter_edit', methods=['PUT'])
def update_quarter_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    title = data.get('title', '').strip()
    comment = data.get('comment', '').strip()
    deadline_date = data.get('deadline_date', '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        UPDATE tasks
        SET title = %s, comment = %s, deadline_date = %s
        WHERE id = %s AND user_id = %s AND quarter IS NOT NULL
        RETURNING *
    """, (title, comment, deadline_date, task_id, session['user_id']))
    task = cur.fetchone()
    if not task:
        conn.close()
        return jsonify({'error': 'Task not found'}), 404
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'task': dict(task)})


@app.route('/api/quarter/tasks/reorder', methods=['POST'])
def reorder_quarter_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    task_ids = data.get('task_ids') or []
    sphere_id = data.get('sphere_id')
    quarter = data.get('quarter')
    if not task_ids or not sphere_id or not quarter:
        return jsonify({'error': 'task_ids, sphere_id and quarter are required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    for position, task_id in enumerate(task_ids):
        cur.execute("""
            UPDATE tasks SET position = %s
            WHERE id = %s AND user_id = %s AND sphere_id = %s
              AND quarter = %s AND status = 'active'
        """, (position, task_id, session['user_id'], sphere_id, quarter))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/task/<int:task_id>/subtasks', methods=['POST'])
def add_subtask(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT 1 FROM tasks WHERE id = %s AND user_id = %s AND quarter IS NOT NULL',
                (task_id, session['user_id']))
    if not cur.fetchone():
        conn.close()
        return jsonify({'error': 'Task not found'}), 404

    cur.execute("""
        SELECT COALESCE(MAX(position), -1) + 1 AS next_position
        FROM subtasks WHERE task_id = %s AND user_id = %s
    """, (task_id, session['user_id']))
    position = cur.fetchone()['next_position']
    cur.execute("""
        INSERT INTO subtasks (task_id, user_id, title, position)
        VALUES (%s, %s, %s, %s)
        RETURNING *
    """, (task_id, session['user_id'], title, position))
    subtask = dict(cur.fetchone())
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'subtask': subtask})


@app.route('/api/subtask/<int:subtask_id>', methods=['GET', 'PUT'])
def update_subtask(subtask_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == 'GET':
        cur.execute('SELECT * FROM subtasks WHERE id = %s AND user_id = %s',
                    (subtask_id, session['user_id']))
        subtask = cur.fetchone()
        conn.close()
        if not subtask:
            return jsonify({'error': 'Subtask not found'}), 404
        return jsonify(dict(subtask))

    data = request.json or {}
    fields = []
    values = []

    if 'title' in data:
        title = str(data.get('title') or '').strip()
        if not title:
            conn.close()
            return jsonify({'error': 'Title is required'}), 400
        fields.append('title = %s')
        values.append(title)
    if 'is_done' in data:
        fields.append('is_done = %s')
        values.append(bool(data.get('is_done')))
    if 'comment' in data:
        fields.append('comment = %s')
        values.append(str(data.get('comment') or '').strip())
    if 'deadline_date' in data:
        deadline_date = str(data.get('deadline_date') or '').strip()
        if deadline_date:
            try:
                datetime.strptime(deadline_date, '%Y-%m-%d')
            except ValueError:
                conn.close()
                return jsonify({'error': 'Invalid deadline date'}), 400
        fields.append('deadline_date = %s')
        values.append(deadline_date)

    if not fields:
        conn.close()
        return jsonify({'error': 'Nothing to update'}), 400

    values.extend([subtask_id, session['user_id']])
    cur.execute(f"""
        UPDATE subtasks SET {", ".join(fields)}
        WHERE id = %s AND user_id = %s
        RETURNING *
    """, values)
    subtask = cur.fetchone()
    if not subtask:
        conn.close()
        return jsonify({'error': 'Subtask not found'}), 404
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'subtask': dict(subtask)})


@app.route('/api/subtask/<int:subtask_id>', methods=['DELETE'])
def delete_subtask(subtask_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM subtasks WHERE id = %s AND user_id = %s',
                (subtask_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/task/<int:task_id>/subtasks/reorder', methods=['POST'])
def reorder_subtasks(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    subtask_ids = data.get('subtask_ids') or []
    if not subtask_ids:
        return jsonify({'error': 'subtask_ids are required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM tasks WHERE id = %s AND user_id = %s AND quarter IS NOT NULL',
                (task_id, session['user_id']))
    if not cur.fetchone():
        conn.close()
        return jsonify({'error': 'Task not found'}), 404

    for position, subtask_id in enumerate(subtask_ids):
        cur.execute('''
            UPDATE subtasks SET position = %s
            WHERE id = %s AND user_id = %s AND task_id = %s
        ''', (position, subtask_id, session['user_id'], task_id))

    conn.commit()
    conn.close()
    return jsonify({'success': True})

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
    deadline_date = data.get('deadline_date', '')
    deadline_time = data.get('deadline_time', '')
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO tasks (user_id, title, category, default_category, date, duration, repeat_type, repeat_day, status, comment, deadline_date, deadline_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], title, category, category, date, duration, repeat_type, repeat_day, 'active', comment, deadline_date, deadline_time))
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
    deadline_date = data.get('deadline_date', '')
    deadline_time = data.get('deadline_time', '')
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT repeat_type FROM tasks WHERE id = %s AND user_id = %s', (task_id, session['user_id']))
    task = cur.fetchone()
    
    if task and task[0] != 'none':
        cur.execute('''
            UPDATE tasks SET 
                title = %s, category = %s, default_category = %s, date = %s, duration = %s, 
                repeat_type = %s, repeat_day = %s, comment = %s,
                deadline_date = %s, deadline_time = %s
            WHERE id = %s AND user_id = %s
        ''', (title, category, category, date, duration, repeat_type, repeat_day, comment, deadline_date, deadline_time, task_id, session['user_id']))
    else:
        cur.execute('''
            UPDATE tasks SET 
                title = %s, category = %s, date = %s, duration = %s, 
                repeat_type = %s, repeat_day = %s, comment = %s,
                deadline_date = %s, deadline_time = %s
            WHERE id = %s AND user_id = %s
        ''', (title, category, date, duration, repeat_type, repeat_day, comment, deadline_date, deadline_time, task_id, session['user_id']))
    
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

    # В квартале крупную задачу можно завершить только после всех её подзадач.
    if task.get('quarter'):
        cur.execute('''
            SELECT COUNT(*) AS remaining
            FROM subtasks
            WHERE user_id = %s AND task_id = %s AND is_done = FALSE
        ''', (session['user_id'], task_id))
        remaining = cur.fetchone()['remaining']
        if remaining:
            conn.close()
            return jsonify({
                'error': 'Complete subtasks first',
                'message': 'Сначала выполните все подзадачи'
            }), 409

    now_utc = get_now_utc()
    now_epoch = get_now_epoch()
    repeat_type = task.get('repeat_type') or 'none'

    if repeat_type == 'none':
        cur.execute('''
            UPDATE tasks
            SET status = %s, completed_at = %s, completed_at_epoch = %s
            WHERE id = %s
        ''', ('done', now_utc, now_epoch, task_id))
    else:
        completion_date = datetime.now().date()
        # Если будущую повторяющуюся задачу закрыли заранее, следующий экземпляр
        # должен идти ПОСЛЕ запланированной даты, а не снова появляться в том же будущем дне.
        schedule_anchor = completion_date
        try:
            scheduled_date = datetime.strptime(task.get('date') or '', '%Y-%m-%d').date()
            if scheduled_date > schedule_anchor:
                schedule_anchor = scheduled_date
        except (ValueError, TypeError):
            pass

        default_cat = task.get('default_category') or 'personal'

        if repeat_type == 'daily':
            new_date = schedule_anchor + timedelta(days=1)
        elif repeat_type == 'weekly' and task.get('repeat_day') is not None:
            new_date = next_weekday_after(schedule_anchor, task['repeat_day'])
        elif repeat_type == 'biweekly' and task.get('repeat_day') is not None:
            new_date = next_biweekly_date(task.get('date'), schedule_anchor, task['repeat_day'])
        elif repeat_type == 'monthly' and task.get('repeat_day') is not None:
            new_date = next_monthly_date(task.get('date'), schedule_anchor, task['repeat_day'])
        else:
            # Повреждённые старые данные: завершаем задачу без создания нового повтора.
            cur.execute('''
                UPDATE tasks
                SET status = %s, completed_at = %s, completed_at_epoch = %s
                WHERE id = %s
            ''', ('done', now_utc, now_epoch, task_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'Task done'})

        # В "Готово" создаём снимок выполненного экземпляра,
        # а исходную повторяющуюся задачу переносим на следующую дату.
        cur.execute('''
            INSERT INTO tasks (
                user_id, title, category, default_category, date, duration,
                repeat_type, repeat_day, status, quarter, sphere, later_group,
                sphere_id, completed_at, completed_at_epoch, comment,
                deadline_date, deadline_time, position
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            task['user_id'], task['title'], task['category'], default_cat,
            task.get('date'), task.get('duration'), 'none', None, 'done',
            task.get('quarter'), task.get('sphere'), task.get('later_group'),
            task.get('sphere_id'), now_utc, now_epoch, task.get('comment'),
            task.get('deadline_date', ''), task.get('deadline_time', ''),
            task.get('position', 0)
        ))

        cur.execute('''
            UPDATE tasks SET
                date = %s,
                status = 'active',
                completed_at = NULL,
                completed_at_epoch = NULL,
                category = %s
            WHERE id = %s
        ''', (new_date.strftime('%Y-%m-%d'), default_cat, task_id))

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
    cur.execute('UPDATE tasks SET status = %s, completed_at = NULL, completed_at_epoch = NULL WHERE id = %s AND user_id = %s', ('active', task_id, session['user_id']))
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
    
    cutoff = get_now_utc() - timedelta(hours=36)
    cur.execute('''
        SELECT * FROM tasks 
        WHERE user_id = %s AND status = %s AND completed_at >= %s
        ORDER BY completed_at DESC
    ''', (session['user_id'], 'done', cutoff))
    tasks = cur.fetchall()
    conn.close()
    
    result = []
    for task in tasks:
        item = dict(task)
        if item.get('completed_at'):
            item['completed_at'] = item['completed_at'].isoformat(timespec='seconds')
        if item.get('completed_at_epoch') is not None:
            item['completed_at_epoch'] = int(item['completed_at_epoch'])
        result.append(item)
    return jsonify(result)

# --- API: Переместить задачу (НЕ МЕНЯЕМ default_category) ---
@app.route('/api/task/<int:task_id>/move', methods=['PUT'])
def move_task(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    category = data.get('category', 'later')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('UPDATE tasks SET category = %s WHERE id = %s AND user_id = %s', 
               (category, task_id, session['user_id']))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Task moved'})

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
        AND date = %s
        ORDER BY position ASC, id ASC
    ''', (session['user_id'], 'active', date_str))
    tasks = [dict(task) for task in cur.fetchall()]
    conn.close()

    # Клиент раскладывает общий ответ по блокам. Глобальная сортировка не мешает:
    # внутри каждого блока относительный порядок задаёт active_task_order_key.
    tasks.sort(key=lambda task: ((task.get('category') or ''),) + active_task_order_key(task))
    return jsonify(tasks)

# --- API: Поиск среди активных задач ---
@app.route('/api/tasks/search')
def search_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify([])
    pattern = '%' + query + '%'
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT id, title, date, category, comment, deadline_date, deadline_time
        FROM tasks
        WHERE user_id = %s AND status = 'active'
          AND (title ILIKE %s OR COALESCE(comment, '') ILIKE %s)
        ORDER BY CASE WHEN date IS NULL OR date = '' THEN 1 ELSE 0 END, date ASC, position ASC, id ASC
        LIMIT 30
    ''', (session['user_id'], pattern, pattern))
    tasks = cur.fetchall()
    conn.close()
    return jsonify([dict(task) for task in tasks])

# --- API: Обновить порядок задач ---
@app.route('/api/tasks/reorder', methods=['POST'])
def reorder_tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    task_ids = data.get('task_ids', [])
    category = data.get('category', '')
    
    if not task_ids or not category:
        return jsonify({'error': 'Task IDs and category are required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    for index, task_id in enumerate(task_ids):
        cur.execute('''
            UPDATE tasks 
            SET position = %s 
            WHERE id = %s AND user_id = %s AND category = %s
        ''', (index, task_id, session['user_id'], category))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Tasks reordered'})

# --- API: Массовый перенос задач на дату ---
@app.route('/api/tasks/move_to_date', methods=['POST'])
def move_tasks_to_date():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    task_ids = data.get('task_ids', [])
    new_date = data.get('date', '')
    
    if not task_ids or not new_date:
        return jsonify({'error': 'Task IDs and date are required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    placeholders = ','.join(['%s'] * len(task_ids))
    cur.execute(f'''
        SELECT id FROM tasks 
        WHERE id IN ({placeholders}) AND user_id = %s
    ''', (*task_ids, session['user_id']))
    valid_tasks = cur.fetchall()
    
    if len(valid_tasks) != len(task_ids):
        conn.close()
        return jsonify({'error': 'Some tasks not found or unauthorized'}), 404
    
    cur.execute(f'''
        UPDATE tasks 
        SET date = %s 
        WHERE id IN ({placeholders}) AND user_id = %s
    ''', (new_date, *task_ids, session['user_id']))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': f'{len(task_ids)} tasks moved to {new_date}'})

# --- РЕГИСТРАЦИЯ ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        raw_password = request.form.get('password', '')
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        
        if not username or not raw_password:
            error = 'Заполните все обязательные поля'
            return render_template_string(REGISTER_PAGE, error=error)
        
        password = generate_password_hash(raw_password)
        
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
            error = 'Пользователь уже существует'
            return render_template_string(REGISTER_PAGE, error=error)
    
    return render_template_string(REGISTER_PAGE, error=error)

# --- ВХОД ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        raw_password = request.form.get('password', '')
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT id, username, password FROM users WHERE username = %s', (username,))
        user = cur.fetchone()
        
        valid_password = False
        if user:
            stored_password = user['password'] or ''
            try:
                valid_password = check_password_hash(stored_password, raw_password)
            except (ValueError, TypeError):
                valid_password = False
            
            # Однократно поддерживаем старые MD5-пароли и сразу заменяем их
            # на безопасный хеш после успешного входа.
            if not valid_password and len(stored_password) == 32:
                import hashlib
                if hashlib.md5(raw_password.encode('utf-8')).hexdigest() == stored_password:
                    valid_password = True
                    cur.execute(
                        'UPDATE users SET password = %s WHERE id = %s',
                        (generate_password_hash(raw_password), user['id'])
                    )
        
        if user and valid_password:
            conn.commit()
            conn.close()
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect('/')
        
        conn.close()
        error = 'Неверный логин или пароль'
    
    return render_template_string(LOGIN_PAGE, error=error)

# --- ВЫХОД ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ====== HTML ШАБЛОНЫ (ЧАСТЬ 2) ======

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
        input { width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ede5f5; border-radius: 8px; font-size: 14px; box-sizing: border-box; background: white; color: #4a3f5e; -webkit-appearance: none; }
        input:focus { outline: none; border-color: #8b7bb5; }
        button { width: 100%; padding: 12px; background: #8b7bb5; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; touch-action: manipulation; }
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

REGISTER_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Регистрация</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f6f2fd; margin: 0; color: #4a3f5e; }
        .card { background: #fcfaff; padding: 40px; border-radius: 16px; box-shadow: 0 4px 30px rgba(139, 123, 181, 0.10); width: 100%; max-width: 400px; }
        h2 { margin-bottom: 20px; color: #4a3f5e; }
        input { width: 100%; padding: 10px 14px; margin: 8px 0; border: 1.5px solid #ede5f5; border-radius: 8px; font-size: 14px; box-sizing: border-box; background: white; color: #4a3f5e; -webkit-appearance: none; }
        input:focus { outline: none; border-color: #8b7bb5; }
        button { width: 100%; padding: 12px; background: #8b7bb5; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 12px; touch-action: manipulation; }
        button:hover { background: #7a69a4; }
        .error { color: #d5a0a0; font-size: 14px; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 16px; font-size: 14px; color: #b5a7cc; }
        .link a { color: #8b7bb5; text-decoration: none; }
        .link a:hover { text-decoration: underline; }
        .optional { font-size: 12px; color: #b5a7cc; font-weight: 400; }
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
            <input type="email" name="email" placeholder="Email (необязательно)">
            <input type="text" name="phone" placeholder="Телефон (необязательно)">
            <button type="submit">Зарегистрироваться</button>
        </form>
        <div class="link">Уже есть аккаунт? <a href="/login">Войти</a></div>
    </div>
</body>
</html>
'''

MAIN_PAGE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Мой органайзер</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f6f2fd;
            padding: 16px;
            min-height: 100vh;
            color: #4a3f5e;
            -webkit-tap-highlight-color: transparent;
        }
        .app-container {
            display: flex;
            gap: 16px;
            max-width: 1400px;
            margin: 0 auto;
            align-items: flex-start;
            flex-wrap: wrap;
        }
        .center-column {
            flex: 1;
            min-width: 280px;
        }
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
            touch-action: manipulation;
        }
        .header .btn-exit:hover { background: #c5b8d8; }
        
        .date-nav {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            margin-bottom: 16px;
            background: #fcfaff;
            padding: 8px 14px;
            border-radius: 12px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .date-nav .nav-btn {
            background: none;
            border: none;
            font-size: 20px;
            color: #8b7bb5;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 8px;
            transition: 0.2s;
            touch-action: manipulation;
            text-decoration: none;
        }
        .date-nav .nav-btn:hover { background: #ede5f5; }
        .date-nav .date-label {
            font-size: 16px;
            font-weight: 600;
            color: #4a3f5e;
            min-width: 140px;
            text-align: center;
        }
        .date-nav .date-label .today-badge {
            font-weight: 400;
            font-size: 13px;
            color: #27ae60;
            background: #e8f5e9;
            padding: 2px 10px;
            border-radius: 12px;
            margin-left: 6px;
        }
        .date-nav .date-label .tomorrow-badge {
            font-weight: 400;
            font-size: 13px;
            color: #e67e22;
            background: #fef5e7;
            padding: 2px 10px;
            border-radius: 12px;
            margin-left: 6px;
        }

        .date-center {
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
        }
        .search-area { display: flex; align-items: center; margin-left: auto; position: relative; z-index: 5; }
        .search-toggle { border: none; background: transparent; color: #8b7bb5; cursor: pointer; padding: 6px 10px; border-radius: 8px; font-size: 13px; white-space: nowrap; transition: 0.2s; }
        .search-toggle:hover { background: #ede5f5; }
        .search-box { position: absolute; top: calc(100% + 6px); right: 0; width: 0; opacity: 0; overflow: hidden; transition: width 0.25s ease, opacity 0.2s ease; }
        .search-area.expanded .search-box { width: 230px; opacity: 1; overflow: visible; }
        .search-input { width: 100%; border: 1.5px solid #ede5f5; border-radius: 8px; padding: 7px 10px; font-size: 13px; color: #4a3f5e; background: white; outline: none; margin: 0; }
        .search-input:focus { border-color: #8b7bb5; }
        .search-results { display: none; position: absolute; z-index: 1000; top: calc(100% + 46px); right: 0; width: min(420px, calc(100vw - 32px)); max-height: 360px; overflow-y: auto; background: white; border: 1px solid #ede5f5; border-radius: 12px; box-shadow: 0 8px 24px rgba(74, 63, 94, 0.14); }
        .search-results.visible { display: block; }
        .search-result { padding: 10px 12px; border-bottom: 1px solid #f0eaf7; cursor: pointer; transition: background 0.15s; }
        .search-result:last-child { border-bottom: none; }
        .search-result:hover { background: #f8f4fc; }
        .search-result-title { color: #4a3f5e; font-size: 13px; font-weight: 600; margin-bottom: 4px; }
        .search-result-meta { color: #a095b5; font-size: 11px; }
        .search-empty { padding: 14px; color: #a095b5; text-align: center; font-size: 13px; }
        .flatpickr-calendar { border-radius: 12px; box-shadow: 0 8px 30px rgba(74, 63, 94, 0.16); font-family: 'Segoe UI', sans-serif; }
        .flatpickr-day.selected, .flatpickr-day.selected:hover { background: #8b7bb5; border-color: #8b7bb5; }
        
        .selection-panel {
            display: none;
            background: #fcfaff;
            border-radius: 12px;
            padding: 12px 20px;
            margin-bottom: 16px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 10px;
        }
        .selection-panel.active { display: flex; }
        .selection-panel .info { color: #8b7bb5; font-size: 14px; }
        .selection-panel .btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
        .selection-panel .btn-group button {
            padding: 6px 16px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            touch-action: manipulation;
        }
        .selection-panel .btn-move {
            background: #8b7bb5;
            color: white;
        }
        .selection-panel .btn-move:hover { background: #7a69a4; }
        .selection-panel .btn-clear {
            background: #ede5f5;
            color: #4a3f5e;
        }
        .selection-panel .btn-clear:hover { background: #e0d5ec; }
        .selection-panel .btn-select-all {
            background: #d5c8e6;
            color: #4a3f5e;
        }
        .selection-panel .btn-select-all:hover { background: #c5b8d8; }
        
        .move-date-input {
            display: none;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }
        .move-date-input.active { display: flex; }
        .move-date-input input[type="date"] {
            padding: 6px 12px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 13px;
            background: white;
            color: #4a3f5e;
            -webkit-appearance: none;
        }
        .move-date-input input:focus { outline: none; border-color: #8b7bb5; }
        .move-date-input .btn-confirm-move {
            background: #27ae60;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 6px 16px;
            cursor: pointer;
            font-size: 13px;
            touch-action: manipulation;
        }
        .move-date-input .btn-confirm-move:hover { background: #2ecc71; }
        .move-date-input .btn-cancel-move {
            background: #ede5f5;
            color: #4a3f5e;
            border: none;
            border-radius: 8px;
            padding: 6px 16px;
            cursor: pointer;
            font-size: 13px;
            touch-action: manipulation;
        }
        .move-date-input .btn-cancel-move:hover { background: #e0d5ec; }
        
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
        
        .block-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
        }
        .block {
            background: #fcfaff;
            border-radius: 12px;
            padding: 14px 16px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
            min-height: 180px;
            transition: opacity 0.3s, transform 0.3s;
        }
        .block.empty { opacity: 0.6; }
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
        .block-urgent .block-header { border-bottom-color: #e67e22; }
        .block-work .block-header { border-bottom-color: #3498db; }
        .block-home .block-header { border-bottom-color: #2ecc71; }
        .block-personal .block-header { border-bottom-color: #e74c3c; }
        
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
            transition: 0.2s;
            box-shadow: 0 1px 4px rgba(139, 123, 181, 0.04);
            cursor: grab;
            touch-action: none;
            user-select: none;
        }
        .task-card:active { cursor: grabbing; }
        .task-card.dragging { opacity: 0.4; transform: scale(0.98); }
        .task-card.drag-over { border-left-color: #8b7bb5; border-left-width: 6px; }
        .task-card:hover { background: #f5eefa; }
        .task-card .task-checkbox {
            margin-right: 6px;
            accent-color: #8b7bb5;
            cursor: pointer;
            width: 16px;
            height: 16px;
            flex-shrink: 0;
        }
        .task-card .task-info { 
            display: flex; 
            align-items: center; 
            gap: 10px; 
            flex-wrap: wrap; 
            cursor: pointer;
            flex: 1;
            touch-action: manipulation;
        }
        .task-card .task-info .task-duration { 
            font-size: 11px; 
            color: #b5a7cc; 
            background: #ede5f5; 
            padding: 1px 8px; 
            border-radius: 10px; 
        }
        .task-card .task-info .comment-badge {
            font-size: 11px;
            color: #8b7bb5;
            background: #ede5f5;
            padding: 1px 8px;
            border-radius: 10px;
            cursor: help;
        }
        .task-card .task-info .deadline-badge {
            font-size: 11px;
            color: #e67e22;
            background: #fef5e7;
            padding: 1px 8px;
            border-radius: 10px;
            margin-left: 4px;
            white-space: nowrap;
        }
        .task-card .task-actions {
            display: flex;
            gap: 4px;
            flex-shrink: 0;
        }
        .task-card .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 4px 6px;
            border-radius: 6px;
            touch-action: manipulation;
            min-width: 32px;
            min-height: 32px;
        }
        .task-card .task-actions button:hover { color: #8b7bb5; background: #ede5f5; }
        .task-card .task-actions .drag-handle {
            color: #d5c8e6;
            cursor: grab;
            font-size: 16px;
        }
        .task-card .task-actions .drag-handle:hover { color: #8b7bb5; background: none; }
        .task-card.tag-focus { border-left-color: #8b7bb5; }
        .task-card.tag-urgent { border-left-color: #e67e22; }
        .task-card.tag-work { border-left-color: #3498db; }
        .task-card.tag-home { border-left-color: #2ecc71; }
        .task-card.tag-personal { border-left-color: #e74c3c; }
        .task-card.tag-waiting { border-left-color: #8e44ad; }
        
        .empty-block { color: #c5b8d8; font-size: 13px; text-align: center; padding: 16px; }
        .add-task-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #f0e8fa;
            color: #8b7bb5;
            border: 2px solid #e0d5ec;
            cursor: pointer;
            font-size: 18px;
            font-weight: 300;
            margin: 6px auto 0;
            transition: 0.2s;
            line-height: 1;
            touch-action: manipulation;
        }
        .add-task-btn:hover { 
            background: #8b7bb5; 
            color: white; 
            border-color: #8b7bb5; 
            transform: scale(1.08);
        }
        
        .right-column { 
            flex: 0 0 200px; 
            display: flex; 
            flex-direction: column; 
            gap: 12px; 
        }
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
            touch-action: manipulation;
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
            touch-action: manipulation;
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
            touch-action: manipulation;
        }
        .sidebar-card .big-btn-done:hover { background: #2ecc71; }
        .sidebar-card .big-btn-future {
            background: #8e44ad;
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
            touch-action: manipulation;
        }
        .sidebar-card .big-btn-future:hover { background: #7d3c98; }
        
        /* Блок "Жду ответа" в правой колонке */
        .waiting-block {
            background: #fcfaff;
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: 0 2px 10px rgba(139, 123, 181, 0.08);
        }
        .waiting-block .block-header {
            font-size: 13px;
            font-weight: 600;
            color: #4a3f5e;
            margin-bottom: 8px;
            padding-bottom: 6px;
            border-bottom: 2px solid #8e44ad;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .waiting-block .block-header .count {
            font-size: 11px;
            font-weight: 400;
            color: #8b7bb5;
            background: #f0e8fa;
            padding: 2px 10px;
            border-radius: 12px;
        }
        .waiting-task {
            background: #faf5ff;
            border-radius: 6px;
            padding: 6px 10px;
            margin-bottom: 4px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            border-left: 3px solid #8e44ad;
        }
        .waiting-task .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 12px;
            padding: 2px 4px;
            touch-action: manipulation;
        }
        .waiting-task .task-actions button:hover { color: #8b7bb5; }
        .waiting-block .add-task-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: #f0e8fa;
            color: #8b7bb5;
            border: 2px solid #e0d5ec;
            cursor: pointer;
            font-size: 14px;
            font-weight: 300;
            margin: 4px auto 0;
            transition: 0.2s;
            line-height: 1;
            touch-action: manipulation;
        }
        .waiting-block .add-task-btn:hover { 
            background: #8b7bb5; 
            color: white; 
            border-color: #8b7bb5; 
            transform: scale(1.08);
        }
        .waiting-empty { color: #c5b8d8; font-size: 11px; text-align: center; padding: 8px; }
        
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
            max-width: 460px;
            width: 90%;
            box-shadow: 0 20px 60px rgba(74, 63, 94, 0.15);
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal h3 { font-size: 18px; margin-bottom: 4px; color: #4a3f5e; }
        .modal .sub { font-size: 13px; color: #8b7bb5; margin-bottom: 16px; }
        .modal label { font-size: 12px; font-weight: 600; color: #4a3f5e; display: block; margin-top: 12px; margin-bottom: 4px; }
        .modal input, .modal select, .modal textarea {
            width: 100%;
            padding: 8px 12px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            font-size: 14px;
            background: white;
            color: #4a3f5e;
            -webkit-appearance: none;
            font-family: inherit;
        }
        .modal textarea { resize: vertical; min-height: 60px; }
        .modal input:focus, .modal select:focus, .modal textarea:focus { outline: none; border-color: #8b7bb5; }
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
        .modal .modal-actions { display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
        .modal .modal-actions button { flex: 1; padding: 10px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; touch-action: manipulation; min-width: 80px; }
        .modal .btn-save { background: #8b7bb5; color: white; }
        .modal .btn-save:hover { background: #7a69a4; }
        .modal .btn-cancel { background: #ede5f5; color: #4a3f5e; }
        .modal .btn-cancel:hover { background: #e0d5ec; }
        .modal .btn-delete { background: #e74c3c; color: white; }
        .modal .btn-delete:hover { background: #c0392b; }
        .task-detail-modal { max-width: 680px; max-height: 88vh; display:flex; flex-direction:column; overflow:hidden; }
        .task-detail-scroll { flex:1; min-height:0; overflow-y:auto; padding-right:4px; padding-bottom:18px; }
        .task-edit-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px 12px; }
        .task-edit-field label { margin-top:6px; }
        .task-edit-full { grid-column:1 / -1; }
        .repeat-summary-btn {
            width:100%; margin-top:10px; padding:9px 11px; border:1.5px solid #ede5f5; border-radius:8px;
            background:#faf7fd; color:#6f6282; text-align:left; cursor:pointer; font-size:13px;
        }
        .repeat-editor { display:none; margin-top:8px; padding:10px 12px; background:#faf7fd; border-radius:9px; }
        .repeat-editor.open { display:block; }
        .add-repeat-editor { margin-top:10px; }
        .task-comment-section { margin-top:12px; margin-bottom:8px; }
        .task-comment-section label { margin-top:0; }
        .unsaved-modal { max-width:420px; }
        .unsaved-modal .sub { margin:8px 0 4px; line-height:1.45; }
        .task-detail-modal .modal-actions {
            position:sticky; bottom:0; margin:12px -20px -20px; padding:12px 20px 14px; background:#fcfaff;
            border-top:1px solid #eee7f5; z-index:2;
        }
        
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
            touch-action: manipulation;
        }
        .move-options button:hover { border-color: #8b7bb5; background: #f8f2fd; }
        .move-options button .cat-icon { display: block; font-size: 20px; }
        
        .task-detail { margin: 12px 0; padding: 10px; background: #f8f2fd; border-radius: 8px; }
        .task-detail .detail-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 14px; border-bottom: 1px solid #ede5f5; }
        .task-detail .detail-row:last-child { border-bottom: none; }
        .task-detail .detail-label { color: #8b7bb5; }
        
        @media (max-width: 768px) {
            body { padding: 10px; }
            .app-container { flex-direction: column; }
            .right-column { flex: 1 1 100%; flex-direction: row; flex-wrap: wrap; }
            .right-column .sidebar-card { flex: 1; min-width: 120px; }
            .right-column .waiting-block { flex: 1; min-width: 120px; }
            .center-column { flex: 1 1 100%; }
            .block { min-height: 140px; }
            .block-grid { grid-template-columns: 1fr 1fr; }
            .date-nav .date-label { font-size: 14px; min-width: 100px; }
            .date-nav { gap: 6px; padding: 8px 10px; }
            .search-area.expanded .search-box { width: 170px; }
            .search-toggle { padding: 6px 7px; font-size: 12px; }
            .header { flex-direction: column; text-align: center; }
            .modal { padding: 18px 16px; }
            .task-edit-grid { grid-template-columns:1fr; }
            .task-edit-full { grid-column:auto; }
            .task-detail-modal .modal-actions { margin:12px -16px -18px; padding:12px 16px 14px; }
            .task-card { padding: 8px 10px; }
            .task-card .task-actions button { padding: 4px 4px; min-width: 28px; min-height: 28px; font-size: 13px; }
            .selection-panel { flex-direction: column; align-items: stretch; }
            .selection-panel .btn-group { justify-content: center; }
            .move-date-input { flex-direction: column; align-items: stretch; }
        }
        @media (max-width: 480px) {
            .block-grid { grid-template-columns: 1fr; }
            .date-nav .date-label { font-size: 12px; min-width: 80px; }
            .search-area.expanded .search-box { width: 140px; }
            .search-toggle { font-size: 0; padding: 6px; }
            .search-toggle::before { content: '🔎'; font-size: 15px; }
            .date-nav .nav-btn { font-size: 16px; }
            .right-column .sidebar-card { min-width: 100px; }
            .right-column .waiting-block { min-width: 100px; }
            .right-column .sidebar-card .big-btn { font-size: 13px; padding: 10px; }
        }
    </style>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/themes/airbnb.css">
    <script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/l10n/ru.js"></script>
</head>
<body>
<div class="app-container">

    <div class="center-column">
        <div class="header">
            <h1>📋 Мои задачи</h1>
            <div>
                <span class="user">👤 {{ username }}</span>
                <a href="/logout" class="btn-exit" style="text-decoration:none; display:inline-block; margin-left:10px;">Выйти</a>
            </div>
        </div>

        <div class="date-nav" id="dateNav">
            <div class="date-center">
                <a href="/?date={{ prev_date }}" class="nav-btn">◀</a>
                <span class="date-label">
                    {{ date_label }}
                    {% if is_today %}<span class="today-badge">сегодня</span>{% endif %}
                    {% if is_tomorrow %}<span class="tomorrow-badge">завтра</span>{% endif %}
                </span>
                <a href="/?date={{ next_date }}" class="nav-btn">▶</a>
            </div>
            <div class="search-area" id="searchArea">
                <button type="button" class="search-toggle" id="searchToggle">🔎 Поиск задач</button>
                <div class="search-box">
                    <input type="search" class="search-input" id="taskSearchInput" placeholder="Найти задачу..." autocomplete="off">
                </div>
                <div class="search-results" id="searchResults"></div>
            </div>
        </div>

        <div class="selection-panel" id="selectionPanel">
            <span class="info" id="selectionInfo">Выбрано: 0 задач</span>
            <div class="btn-group">
                <button class="btn-select-all" id="selectAllBtn">Выбрать все</button>
                <button class="btn-clear" id="clearSelectionBtn">Снять все</button>
                <button class="btn-move" id="moveSelectedBtn">📅 Перенести на дату</button>
            </div>
            <div class="move-date-input" id="moveDateInput">
                <input type="date" id="moveDatePicker" value="{{ view_date }}">
                <button class="btn-confirm-move" id="confirmMoveBtn">✅ Перенести</button>
                <button class="btn-cancel-move" id="cancelMoveBtn">Отмена</button>
            </div>
        </div>

        <div class="focus-block" id="focusBlock">
            <div class="block-header">
                🎯 Фокус
                <span class="count" id="focusCount">0</span>
            </div>
            <div id="focusTasks"></div>
            <div class="empty-block" id="focusEmpty">Нет задач в фокусе</div>
        </div>

        <div class="block-grid" id="blockGrid">
            <div class="block block-urgent" id="block-urgent">
                <div class="block-header">⚡ До 15 минут <span class="count" id="count-urgent">0</span></div>
                <div id="tasks-urgent"></div>
                <button class="add-task-btn" data-category="urgent">+</button>
            </div>
            <div class="block block-work" id="block-work">
                <div class="block-header">💼 Работа <span class="count" id="count-work">0</span></div>
                <div id="tasks-work"></div>
                <button class="add-task-btn" data-category="work">+</button>
            </div>
            <div class="block block-home" id="block-home">
                <div class="block-header">🏠 Дом <span class="count" id="count-home">0</span></div>
                <div id="tasks-home"></div>
                <button class="add-task-btn" data-category="home">+</button>
            </div>
            <div class="block block-personal" id="block-personal">
                <div class="block-header">❤️ Личное <span class="count" id="count-personal">0</span></div>
                <div id="tasks-personal"></div>
                <button class="add-task-btn" data-category="personal">+</button>
            </div>
        </div>
    </div>

    <div class="right-column">
        <div class="sidebar-card">
            <a href="/quarter/{{ current_quarter }}" class="big-btn">🗓️ 3 месяца</a>
            <a href="/future" class="big-btn-future">📅 Будущие</a>
            <a href="/later" class="big-btn-secondary">🕰️ Позже</a>
            <a href="/done" class="big-btn-done">✅ Готово</a>
        </div>
        
        <!-- Блок "Жду ответа" перенесен в правую колонку -->
        <div class="waiting-block" id="block-waiting">
            <div class="block-header">
                ⏳ Жду ответа
                <span class="count" id="count-waiting">0</span>
            </div>
            <div id="tasks-waiting"></div>
            <button class="add-task-btn" data-category="waiting" title="Добавить задачу">+</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="addTaskModal">
    <div class="modal task-detail-modal">
        <div class="task-detail-scroll">
            <h3>➕ Новая задача</h3>
            <p class="sub" id="addTaskModalSub">Добавьте задачу в категорию</p>
            <input type="hidden" id="addTaskCategory">

            <div class="task-edit-grid">
                <div class="task-edit-field task-edit-full">
                    <label for="addTaskTitle">Название задачи</label>
                    <input type="text" id="addTaskTitle" placeholder="Что нужно сделать?" autofocus>
                </div>
                <div class="task-edit-field">
                    <label for="addTaskDate">📅 Дата выполнения</label>
                    <input type="date" id="addTaskDate" value="{{ view_date }}">
                </div>
                <div class="task-edit-field">
                    <label for="addTaskDuration">⏱️ Время выполнения</label>
                    <input type="text" id="addTaskDuration" placeholder="1 ч">
                </div>
                <div class="task-edit-field">
                    <label for="addDeadlineDate">⏰ Дедлайн</label>
                    <input type="date" id="addDeadlineDate" value="">
                </div>
                <div class="task-edit-field">
                    <label for="addDeadlineTime">Время дедлайна</label>
                    <input type="time" id="addDeadlineTime" value="">
                </div>
            </div>

            <div class="repeat-editor open add-repeat-editor">
                <div class="checkbox-group" style="margin-top:0;">
                    <input type="checkbox" id="addTaskRepeat">
                    <label for="addTaskRepeat">🔄 Повторяющаяся задача</label>
                </div>
                <div class="repeat-options" id="addRepeatOptions">
                    <label for="addRepeatType">Тип повторения</label>
                    <select id="addRepeatType">
                        <option value="daily">📆 Каждый день</option>
                        <option value="weekly">📅 Каждую неделю</option>
                        <option value="biweekly">🗓️ Каждые 2 недели</option>
                        <option value="monthly">📌 Каждый месяц</option>
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
                    <div id="addMonthlyDayGroup" style="margin-top:8px; display:none;">
                        <label for="addMonthlyDay">Число месяца</label>
                        <select id="addMonthlyDay">
                            <option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option><option value="6">6</option><option value="7">7</option><option value="8">8</option><option value="9">9</option><option value="10">10</option><option value="11">11</option><option value="12">12</option><option value="13">13</option><option value="14">14</option><option value="15">15</option><option value="16">16</option><option value="17">17</option><option value="18">18</option><option value="19">19</option><option value="20">20</option><option value="21">21</option><option value="22">22</option><option value="23">23</option><option value="24">24</option><option value="25">25</option><option value="26">26</option><option value="27">27</option><option value="28">28</option><option value="29">29</option><option value="30">30</option><option value="31">31</option>
                        </select>
                        <div style="font-size:11px; color:#9b8db5; margin-top:4px;">Если такого числа нет, задача появится в последний день месяца.</div>
                    </div>
                </div>
            </div>

            <div class="task-comment-section">
                <label for="addTaskComment">💬 Комментарий</label>
                <textarea id="addTaskComment" placeholder="Дополнительная информация..."></textarea>
            </div>
        </div>
        <div class="modal-actions">
            <button class="btn-save" id="saveAddTaskBtn">💾 Сохранить</button>
            <button class="btn-cancel" id="cancelAddTaskBtn">Отмена</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="viewTaskModal">
    <div class="modal task-detail-modal">
        <div class="task-detail-scroll">
            <h3 id="viewTaskTitle">📌 Задача</h3>
            <p class="sub" id="viewTaskCategory"></p>
            <input type="hidden" id="viewTaskId">

            <div class="task-edit-grid">
                <div class="task-edit-field task-edit-full">
                    <label for="viewTaskTitleInput">Название задачи</label>
                    <input type="text" id="viewTaskTitleInput">
                </div>

                <div class="task-edit-field">
                    <label for="viewTaskDate">📅 Дата выполнения</label>
                    <input type="date" id="viewTaskDate">
                </div>
                <div class="task-edit-field">
                    <label for="viewTaskDuration">⏱️ Время выполнения</label>
                    <input type="text" id="viewTaskDuration" placeholder="1 ч">
                </div>

                <div class="task-edit-field">
                    <label for="viewDeadlineDate">⏰ Дедлайн</label>
                    <input type="date" id="viewDeadlineDate">
                </div>
                <div class="task-edit-field">
                    <label for="viewDeadlineTime">Время дедлайна</label>
                    <input type="time" id="viewDeadlineTime">
                </div>

                <div class="task-edit-field task-edit-full">
                    <label for="viewTaskCategorySelect">📂 Категория</label>
                    <select id="viewTaskCategorySelect">
                        <option value="focus">🎯 Фокус</option>
                        <option value="urgent">⚡ До 15 минут</option>
                        <option value="work">💼 Работа</option>
                        <option value="home">🏠 Дом</option>
                        <option value="personal">❤️ Личное</option>
                        <option value="waiting">⏳ Жду ответа</option>
                        <option value="later">🕰️ Позже</option>
                    </select>
                </div>

            </div>

            <button type="button" class="repeat-summary-btn" id="viewRepeatSummary">🔄 Не повторяется</button>
            <div class="repeat-editor" id="viewRepeatEditor">
                <div class="checkbox-group" style="margin-top:0;">
                    <input type="checkbox" id="viewTaskRepeat">
                    <label for="viewTaskRepeat">Повторяющаяся задача</label>
                </div>
                <div class="repeat-options" id="viewRepeatOptions">
                    <label for="viewRepeatType">Тип повторения</label>
                    <select id="viewRepeatType">
                        <option value="daily">📆 Каждый день</option>
                        <option value="weekly">📅 Каждую неделю</option>
                        <option value="biweekly">🗓️ Каждые 2 недели</option>
                        <option value="monthly">📌 Каждый месяц</option>
                    </select>
                    <div id="viewWeeklyDayGroup" style="margin-top:8px; display:none;">
                        <label for="viewRepeatDay">День недели</label>
                        <select id="viewRepeatDay">
                            <option value="0">Воскресенье</option><option value="1">Понедельник</option><option value="2">Вторник</option><option value="3">Среда</option><option value="4">Четверг</option><option value="5">Пятница</option><option value="6">Суббота</option>
                        </select>
                    </div>
                    <div id="viewMonthlyDayGroup" style="margin-top:8px; display:none;">
                        <label for="viewMonthlyDay">Число месяца</label>
                        <select id="viewMonthlyDay">
                            <option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option><option value="6">6</option><option value="7">7</option><option value="8">8</option><option value="9">9</option><option value="10">10</option><option value="11">11</option><option value="12">12</option><option value="13">13</option><option value="14">14</option><option value="15">15</option><option value="16">16</option><option value="17">17</option><option value="18">18</option><option value="19">19</option><option value="20">20</option><option value="21">21</option><option value="22">22</option><option value="23">23</option><option value="24">24</option><option value="25">25</option><option value="26">26</option><option value="27">27</option><option value="28">28</option><option value="29">29</option><option value="30">30</option><option value="31">31</option>
                        </select>
                        <div style="font-size:11px; color:#9b8db5; margin-top:4px;">Если такого числа нет, задача появится в последний день месяца.</div>
                    </div>
                </div>
            </div>

            <div class="task-comment-section">
                <label for="viewTaskComment">💬 Комментарий</label>
                <textarea id="viewTaskComment" placeholder="Дополнительная информация..."></textarea>
            </div>
        </div>

        <div class="modal-actions">
            <button class="btn-save" id="viewTaskSave">💾 Сохранить</button>
            <button class="btn-delete" id="viewTaskDelete">🗑️ Удалить</button>
            <button class="btn-cancel" id="cancelViewBtn">Закрыть</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="unsavedTaskModal" style="z-index:1100;">
    <div class="modal unsaved-modal">
        <h3>Есть несохранённые изменения</h3>
        <p class="sub">Сохранить изменения перед закрытием задачи?</p>
        <div class="modal-actions">
            <button class="btn-save" id="unsavedTaskSave">💾 Сохранить</button>
            <button class="btn-delete" id="unsavedTaskDiscard">Сбросить</button>
            <button class="btn-cancel" id="unsavedTaskContinue">Вернуться</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="moveModal">
    <div class="modal">
        <h3>➡️ Переместить задачу</h3>
        <p class="sub" id="moveTaskTitle">Выберите категорию</p>
        <input type="hidden" id="moveTaskId">
        <div class="move-options">
            <button class="move-cat-btn" data-category="focus"><span class="cat-icon">🎯</span> Фокус</button>
            <button class="move-cat-btn" data-category="urgent"><span class="cat-icon">⚡</span> До 15 мин</button>
            <button class="move-cat-btn" data-category="work"><span class="cat-icon">💼</span> Работа</button>
            <button class="move-cat-btn" data-category="home"><span class="cat-icon">🏠</span> Дом</button>
            <button class="move-cat-btn" data-category="personal"><span class="cat-icon">❤️</span> Личное</button>
            <button class="move-cat-btn" data-category="waiting"><span class="cat-icon">⏳</span> Жду ответа</button>
            <button class="move-cat-btn" data-category="later" style="grid-column: span 2;"><span class="cat-icon">🕰️</span> Позже</button>
        </div>
        <div class="modal-actions">
            <button class="btn-cancel" id="cancelMoveBtn">Отмена</button>
        </div>
    </div>
</div>

<script>
    let currentViewTaskId = null;
    let initialViewTaskState = null;
    let moveTaskId = null;
    let currentViewDate = '{{ view_date }}';
    let draggedTaskId = null;
    let dragSourceBlock = null;
    let selectedTasks = new Set();
    let dragTimeout = null;
    
    document.addEventListener('DOMContentLoaded', function() {
        initDragDrop();
        updateEmptyBlocks();
        updateSelectionPanel();
        initTaskSearch();
        initDatePickers();
    });
    
    function toggleTaskSelection(taskId) {
        if (selectedTasks.has(taskId)) {
            selectedTasks.delete(taskId);
        } else {
            selectedTasks.add(taskId);
        }
        updateSelectionPanel();
        updateCheckboxes();
    }
    
    function selectAllTasks() {
        const checkboxes = document.querySelectorAll('.task-checkbox');
        checkboxes.forEach(cb => {
            const taskId = parseInt(cb.dataset.taskId);
            selectedTasks.add(taskId);
            cb.checked = true;
        });
        updateSelectionPanel();
    }
    
    function clearAllSelection() {
        selectedTasks.clear();
        updateSelectionPanel();
        updateCheckboxes();
    }
    
    function updateCheckboxes() {
        document.querySelectorAll('.task-checkbox').forEach(cb => {
            const taskId = parseInt(cb.dataset.taskId);
            cb.checked = selectedTasks.has(taskId);
        });
    }
    
    function updateSelectionPanel() {
        const panel = document.getElementById('selectionPanel');
        const info = document.getElementById('selectionInfo');
        const count = selectedTasks.size;
        if (count > 0) {
            panel.classList.add('active');
            info.textContent = '✅ Выбрано: ' + count + ' задач';
        } else {
            panel.classList.remove('active');
            info.textContent = 'Выбрано: 0 задач';
        }
    }
    
    document.getElementById('selectAllBtn').addEventListener('click', selectAllTasks);
    document.getElementById('clearSelectionBtn').addEventListener('click', clearAllSelection);
    
    document.getElementById('moveSelectedBtn').addEventListener('click', function() {
        if (selectedTasks.size === 0) {
            alert('Выберите хотя бы одну задачу');
            return;
        }
        document.getElementById('moveDateInput').classList.toggle('active');
    });
    
    document.getElementById('cancelMoveBtn').addEventListener('click', function() {
        document.getElementById('moveDateInput').classList.remove('active');
    });
    
    document.getElementById('confirmMoveBtn').addEventListener('click', function() {
        if (selectedTasks.size === 0) {
            alert('Выберите хотя бы одну задачу');
            return;
        }
        const newDate = document.getElementById('moveDatePicker').value;
        if (!newDate) {
            alert('Выберите дату');
            return;
        }
        const taskIds = Array.from(selectedTasks);
        fetch('/api/tasks/move_to_date', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_ids: taskIds, date: newDate })
        })
        .then(res => res.json())
        .then(() => {
            clearAllSelection();
            document.getElementById('moveDateInput').classList.remove('active');
            loadTasks();
        });
    });
    
    function initDragDrop() {
        const taskCards = document.querySelectorAll('.task-card');
        taskCards.forEach(card => {
            card.removeEventListener('dragstart', handleDragStart);
            card.removeEventListener('dragend', handleDragEnd);
            card.removeEventListener('dragover', handleDragOver);
            card.removeEventListener('dragenter', handleDragEnter);
            card.removeEventListener('dragleave', handleDragLeave);
            card.removeEventListener('drop', handleDrop);
            card.removeEventListener('touchstart', handleTouchStart);
            card.removeEventListener('touchmove', handleTouchMove);
            card.removeEventListener('touchend', handleTouchEnd);
            
            card.addEventListener('dragstart', handleDragStart);
            card.addEventListener('dragend', handleDragEnd);
            card.addEventListener('dragover', handleDragOver);
            card.addEventListener('dragenter', handleDragEnter);
            card.addEventListener('dragleave', handleDragLeave);
            card.addEventListener('drop', handleDrop);
            card.addEventListener('touchstart', handleTouchStart, { passive: true });
            card.addEventListener('touchmove', handleTouchMove, { passive: false });
            card.addEventListener('touchend', handleTouchEnd, { passive: true });
        });
    }
    
    function handleDragStart(e) {
        draggedTaskId = this.dataset.taskId;
        dragSourceBlock = this.closest('.block, .waiting-block, .focus-block');
        this.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', this.dataset.taskId);
    }
    
    function handleDragEnd(e) {
        this.classList.remove('dragging');
        document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
        if (dragTimeout) clearTimeout(dragTimeout);
        dragTimeout = setTimeout(saveOrder, 300);
    }
    
    function handleDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
    }
    
    function handleDragEnter(e) {
        e.preventDefault();
        this.classList.add('drag-over');
    }
    
    function handleDragLeave(e) {
        this.classList.remove('drag-over');
    }
    
    function handleDrop(e) {
        e.preventDefault();
        this.classList.remove('drag-over');
        const targetCard = this;
        const targetBlock = this.closest('.block, .waiting-block, .focus-block');
        const sourceBlock = dragSourceBlock;
        
        if (!targetBlock || !sourceBlock) return;
        
        if (targetBlock === sourceBlock) {
            reorderTasks(sourceBlock, draggedTaskId, targetCard);
        }
    }
    
    function getTaskContainer(block) {
        if (!block) return null;
        return block.querySelector('[id^="tasks-"]') || block.querySelector('#focusTasks');
    }

    function getBlockCategory(block) {
        if (!block) return '';
        const categoryById = {
            'focusBlock': 'focus', 'block-urgent': 'urgent', 'block-work': 'work',
            'block-home': 'home', 'block-personal': 'personal', 'block-waiting': 'waiting'
        };
        return categoryById[block.id] || '';
    }

    function reorderTasks(block, taskId, targetElement) {
        const container = getTaskContainer(block);
        if (!container) return;
        const cards = container.querySelectorAll('.task-card');
        let targetIndex = -1;
        let currentIndex = -1;
        
        cards.forEach((card, index) => {
            if (card.dataset.taskId === taskId) {
                currentIndex = index;
            }
            if (card === targetElement) {
                targetIndex = index;
            }
        });
        
        if (currentIndex === -1 || targetIndex === -1 || currentIndex === targetIndex) {
            return;
        }
        
        if (currentIndex < targetIndex) {
            targetElement.parentNode.insertBefore(cards[currentIndex], targetElement.nextSibling);
        } else {
            targetElement.parentNode.insertBefore(cards[currentIndex], targetElement);
        }
        
        updatePositions(block);
        if (dragTimeout) clearTimeout(dragTimeout);
        dragTimeout = setTimeout(saveOrder, 300);
    }
    
    function saveOrder() {
        const requests = [];
        document.querySelectorAll('.block, .focus-block, .waiting-block').forEach(block => {
            const container = getTaskContainer(block);
            if (!container) return;
            const taskIds = Array.from(container.querySelectorAll('.task-card')).map(card => parseInt(card.dataset.taskId));
            const category = getBlockCategory(block);
            if (!category || !taskIds.length) return;

            requests.push(fetch('/api/tasks/reorder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_ids: taskIds, category: category })
            }));
        });

        // После ручной перестановки снова применяем правило приоритета дедлайнов.
        if (requests.length) Promise.allSettled(requests).then(() => loadTasks());
    }
    
    function updatePositions(block) {
        const container = getTaskContainer(block);
        if (!container) return;
        const cards = container.querySelectorAll('.task-card');
        const category = getBlockCategory(block);
        if (!category) return;
        
        const countEl = document.getElementById('count-' + category);
        if (countEl) {
            countEl.textContent = cards.length;
        }
        
        updateEmptyBlocks();
    }
    
    function updateEmptyBlocks() {
        const blockGrid = document.getElementById('blockGrid');
        if (!blockGrid) return;
        const blocks = Array.from(blockGrid.querySelectorAll('.block'));
        blocks.forEach((block, index) => {
            const container = getTaskContainer(block);
            const hasTasks = !!(container && container.querySelector('.task-card'));
            block.classList.toggle('empty', !hasTasks);
            block.style.order = hasTasks ? index : blocks.length + index;
        });
    }
    
    let touchDragData = null;
    
    function handleTouchStart(e) {
        const touch = e.touches[0];
        touchDragData = {
            taskId: this.dataset.taskId,
            card: this,
            startX: touch.clientX,
            startY: touch.clientY,
            block: this.closest('.block, .waiting-block, .focus-block')
        };
    }
    
    function handleTouchMove(e) {
        if (!touchDragData) return;
        e.preventDefault();
    }
    
    function handleTouchEnd(e) {
        if (!touchDragData) return;
        const touch = e.changedTouches[0];
        const element = document.elementFromPoint(touch.clientX, touch.clientY);
        if (element) {
            const targetCard = element.closest('.task-card');
            const targetBlock = element.closest('.block, .waiting-block, .focus-block');
            if (targetCard && targetCard !== touchDragData.card) {
                const sourceBlock = touchDragData.block;
                if (sourceBlock && targetBlock && sourceBlock === targetBlock) {
                    reorderTasks(sourceBlock, touchDragData.taskId, targetCard);
                }
            }
        }
        touchDragData = null;
    }
    
    function loadTasks() {
        fetch('/api/tasks/date/' + currentViewDate)
            .then(res => res.json())
            .then(tasks => {
                const categories = { focus: [], urgent: [], work: [], home: [], personal: [], waiting: [] };
                tasks.forEach(t => {
                    if (categories[t.category]) categories[t.category].push(t);
                });
                renderTasks(categories);
                initDragDrop();
                updateEmptyBlocks();
                updateSelectionPanel();
                updateCheckboxes();
            });
    }
    
    function renderTasks(categories) {
        const containerMap = {
            focus: { tasks: 'focusTasks', count: 'focusCount', empty: 'focusEmpty' },
            urgent: { tasks: 'tasks-urgent', count: 'count-urgent', block: 'block-urgent' },
            work: { tasks: 'tasks-work', count: 'count-work', block: 'block-work' },
            home: { tasks: 'tasks-home', count: 'count-home', block: 'block-home' },
            personal: { tasks: 'tasks-personal', count: 'count-personal', block: 'block-personal' },
            waiting: { tasks: 'tasks-waiting', count: 'count-waiting', block: 'block-waiting' }
        };
        
        for (const [cat, data] of Object.entries(containerMap)) {
            const tasks = categories[cat] || [];
            const container = document.getElementById(data.tasks);
            const countEl = document.getElementById(data.count);
            const blockEl = document.getElementById(data.block);
            
            if (!container) continue;
            
            container.innerHTML = '';
            tasks.forEach(task => {
                const card = createTaskCard(task, cat);
                container.appendChild(card);
            });
            
            if (countEl) {
                countEl.textContent = tasks.length;
            }
            
            if (cat === 'focus') {
                const emptyEl = document.getElementById('focusEmpty');
                if (emptyEl) {
                    emptyEl.style.display = tasks.length === 0 ? 'block' : 'none';
                }
            }
        }
        updateEmptyBlocks();
        updateSelectionPanel();
    }
    
    function createTaskCard(task, category) {
        const div = document.createElement('div');
        div.className = 'task-card tag-' + category;
        div.dataset.taskId = task.id;
        div.draggable = true;
        
        const isChecked = selectedTasks.has(task.id);
        
        let durationHtml = '';
        if (task.duration) {
            durationHtml = '<span class="task-duration">⏱️ ' + task.duration + '</span>';
        }
        
        let commentHtml = '';
        if (task.comment && task.comment.trim() !== '') {
            commentHtml = '<span class="comment-badge" title="' + task.comment.replace(/"/g, '&quot;') + '">💬</span>';
        }
        
        let deadlineHtml = '';
        if (task.deadline_date) {
            const deadlineText = getDeadlineText(task.deadline_date, task.deadline_time);
            if (deadlineText) {
                deadlineHtml = '<span class="deadline-badge">' + deadlineText + '</span>';
            }
        }
        
        div.innerHTML = `
            <div class="task-info" data-task-id="${task.id}">
                <input type="checkbox" class="task-checkbox" data-task-id="${task.id}" ${isChecked ? 'checked' : ''}>
                <span>${task.title}</span>
                ${durationHtml}
                ${commentHtml}
                ${deadlineHtml}
            </div>
            <div class="task-actions">
                <span class="drag-handle" title="Перетащить">⠿</span>
                <button class="done-btn" title="Выполнено" data-task-id="${task.id}">✅</button>
                <button class="move-to-focus-btn" title="В фокус" data-task-id="${task.id}" style="display:${task.category !== 'focus' ? 'inline' : 'none'}">⭐</button>
            </div>
        `;
        
        const checkbox = div.querySelector('.task-checkbox');
        checkbox.addEventListener('change', function(e) {
            e.stopPropagation();
            const taskId = parseInt(this.dataset.taskId);
            toggleTaskSelection(taskId);
        });
        
        div.querySelector('.task-info').addEventListener('click', function(e) {
            if (e.target.type === 'checkbox') return;
            e.stopPropagation();
            const taskId = this.dataset.taskId;
            viewTask(taskId);
        });
        
        div.querySelector('.done-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            fetch('/api/task/' + task.id + '/done', { method: 'POST' })
                .then(async res => {
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(data.message || 'Не удалось завершить задачу');
                    return data;
                })
                .then(() => { loadTasks(); })
                .catch(err => alert(err.message));
        });
        
        const focusBtn = div.querySelector('.move-to-focus-btn');
        if (focusBtn) {
            focusBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                const taskId = this.dataset.taskId;
                fetch('/api/task/' + taskId + '/move', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: 'focus' })
                })
                .then(() => { loadTasks(); });
            });
        }
        
        return div;
    }
    
    function getDeadlineText(deadlineDate, deadlineTime) {
        if (!deadlineDate) return '';
        try {
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            const deadline = new Date(deadlineDate + 'T00:00:00');
            const diffDays = Math.floor((deadline - today) / (1000 * 60 * 60 * 24));
            
            if (diffDays < 0) return '🔴 просрочен!';
            if (diffDays === 0) {
                if (deadlineTime) return '⏰ до ' + deadlineTime;
                return '⏰ сегодня';
            }
            if (diffDays === 1) return '⏰ до завтра';
            const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
            const day = deadline.getDate();
            const month = months[deadline.getMonth()];
            return '⏰ до ' + day + ' ' + month;
        } catch(e) {
            return '';
        }
    }
    
    let searchTimeout = null;

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, function(char) {
            return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'})[char];
        });
    }

    function formatSearchDate(dateStr) {
        if (!dateStr) return 'Без даты';
        try {
            const date = new Date(dateStr + 'T00:00:00');
            const months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
            return date.getDate() + ' ' + months[date.getMonth()];
        } catch (e) { return dateStr; }
    }

    function getSearchCategoryName(category) {
        const names = { focus:'🎯 Фокус', urgent:'⚡ До 15 минут', work:'💼 Работа', home:'🏠 Дом', personal:'❤️ Личное', waiting:'⏳ Жду ответа', later:'🕰️ Позже' };
        return names[category] || category || '';
    }

    function renderSearchResults(tasks) {
        const results = document.getElementById('searchResults');
        if (!results) return;
        if (!tasks.length) {
            results.innerHTML = '<div class="search-empty">Ничего не найдено</div>';
            results.classList.add('visible');
            return;
        }
        results.innerHTML = tasks.map(task => `
            <div class="search-result" data-task-id="${task.id}">
                <div class="search-result-title">${escapeHtml(task.title)}</div>
                <div class="search-result-meta">📅 ${escapeHtml(formatSearchDate(task.date))} · ${escapeHtml(getSearchCategoryName(task.category))}</div>
            </div>
        `).join('');
        results.querySelectorAll('.search-result').forEach(item => {
            item.addEventListener('click', function() {
                viewTask(this.dataset.taskId);
                results.classList.remove('visible');
            });
        });
        results.classList.add('visible');
    }

    function performTaskSearch(query) {
        const trimmed = query.trim();
        const results = document.getElementById('searchResults');
        if (!results) return;
        if (!trimmed) {
            results.innerHTML = '';
            results.classList.remove('visible');
            return;
        }
        fetch('/api/tasks/search?q=' + encodeURIComponent(trimmed))
            .then(res => res.json())
            .then(tasks => renderSearchResults(tasks))
            .catch(() => {
                results.innerHTML = '<div class="search-empty">Не удалось выполнить поиск</div>';
                results.classList.add('visible');
            });
    }

    function initTaskSearch() {
        const area = document.getElementById('searchArea');
        const toggle = document.getElementById('searchToggle');
        const input = document.getElementById('taskSearchInput');
        const results = document.getElementById('searchResults');
        if (!area || !toggle || !input || !results) return;

        function closeSearch() {
            clearTimeout(searchTimeout);
            input.value = '';
            results.innerHTML = '';
            results.classList.remove('visible');
            area.classList.remove('expanded');
        }

        toggle.addEventListener('click', function(e) {
            e.stopPropagation();
            if (area.classList.contains('expanded')) {
                closeSearch();
                return;
            }
            area.classList.add('expanded');
            setTimeout(() => input.focus(), 120);
        });
        input.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            const query = this.value;
            searchTimeout = setTimeout(() => performTaskSearch(query), 250);
        });
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeSearch();
                toggle.focus();
            }
        });
        results.addEventListener('click', function(e) {
            if (e.target.closest('.search-result')) closeSearch();
        });
        document.addEventListener('click', function(e) {
            if (!area.contains(e.target)) closeSearch();
        });
    }

    function setDateInputValue(id, value) {
        const input = document.getElementById(id);
        if (!input) return;
        if (input._flatpickr) input._flatpickr.setDate(value || null, false);
        else input.value = value || '';
    }

    function initDatePickers() {
        if (typeof flatpickr === 'undefined') return;
        flatpickr.localize(flatpickr.l10ns.ru);
        document.querySelectorAll('input[type="date"]').forEach(input => {
            if (input._flatpickr) return;
            flatpickr(input, { locale: flatpickr.l10ns.ru, dateFormat: 'Y-m-d', altInput: true, altFormat: 'j F Y', allowInput: true, disableMobile: true });
        });
    }

    function getViewTaskState() {
        const isRepeating = document.getElementById('viewTaskRepeat').checked;
        const repeatType = isRepeating ? document.getElementById('viewRepeatType').value : 'none';
        let repeatDay = null;
        if (isRepeating && (repeatType === 'weekly' || repeatType === 'biweekly')) {
            repeatDay = document.getElementById('viewRepeatDay').value;
        } else if (isRepeating && repeatType === 'monthly') {
            repeatDay = document.getElementById('viewMonthlyDay').value;
        }
        return {
            title: document.getElementById('viewTaskTitleInput').value,
            date: document.getElementById('viewTaskDate').value,
            duration: document.getElementById('viewTaskDuration').value,
            deadline_date: document.getElementById('viewDeadlineDate').value,
            deadline_time: document.getElementById('viewDeadlineTime').value,
            category: document.getElementById('viewTaskCategorySelect').value,
            repeat_type: repeatType,
            repeat_day: repeatDay,
            comment: document.getElementById('viewTaskComment').value
        };
    }

    function rememberViewTaskState() {
        initialViewTaskState = JSON.stringify(getViewTaskState());
    }

    function isViewTaskDirty() {
        return initialViewTaskState !== null && JSON.stringify(getViewTaskState()) !== initialViewTaskState;
    }

    function closeViewTaskModal() {
        document.getElementById('viewTaskModal').classList.remove('open');
        document.getElementById('unsavedTaskModal').classList.remove('open');
        initialViewTaskState = null;
    }

    function requestCloseViewTask() {
        if (!document.getElementById('viewTaskModal').classList.contains('open')) return;
        if (!isViewTaskDirty()) {
            closeViewTaskModal();
            return;
        }
        document.getElementById('unsavedTaskModal').classList.add('open');
    }

    function updateViewRepeatSummary() {
        const summary = document.getElementById('viewRepeatSummary');
        const repeat = document.getElementById('viewTaskRepeat').checked;
        if (!repeat) {
            summary.textContent = '🔄 Не повторяется';
            return;
        }
        const type = document.getElementById('viewRepeatType').value;
        const labels = {daily:'Каждый день', weekly:'Каждую неделю', biweekly:'Каждые 2 недели', monthly:'Каждый месяц'};
        let detail = labels[type] || 'Повторяется';
        if (type === 'weekly' || type === 'biweekly') {
            const days = ['воскресенье','понедельник','вторник','среда','четверг','пятница','суббота'];
            detail += ' · ' + days[Number(document.getElementById('viewRepeatDay').value) || 0];
        } else if (type === 'monthly') {
            detail += ' · ' + document.getElementById('viewMonthlyDay').value + ' число';
        }
        summary.textContent = '🔄 ' + detail;
    }

    function viewTask(taskId) {
        fetch('/api/task/' + taskId)
            .then(res => res.json())
            .then(task => {
                currentViewTaskId = task.id;
                document.getElementById('viewTaskId').value = task.id;
                document.getElementById('viewTaskTitle').textContent = '📌 ' + task.title;
                document.getElementById('viewTaskTitleInput').value = task.title || '';
                setDateInputValue('viewTaskDate', task.date || '');
                document.getElementById('viewTaskDuration').value = task.duration || '';
                document.getElementById('viewTaskComment').value = task.comment || '';
                setDateInputValue('viewDeadlineDate', task.deadline_date || '');
                document.getElementById('viewDeadlineTime').value = task.deadline_time || '';
                document.getElementById('viewTaskCategorySelect').value = task.category || 'later';
                
                const isRepeating = task.repeat_type && task.repeat_type !== 'none';
                document.getElementById('viewTaskRepeat').checked = isRepeating;
                
                const repeatOptions = document.getElementById('viewRepeatOptions');
                if (isRepeating) {
                    repeatOptions.classList.add('visible');
                    document.getElementById('viewRepeatType').value = task.repeat_type || 'daily';
                    const isWeeklyKind = task.repeat_type === 'weekly' || task.repeat_type === 'biweekly';
                    document.getElementById('viewWeeklyDayGroup').style.display = isWeeklyKind ? 'block' : 'none';
                    document.getElementById('viewMonthlyDayGroup').style.display = task.repeat_type === 'monthly' ? 'block' : 'none';
                    if (isWeeklyKind) {
                        document.getElementById('viewRepeatDay').value = task.repeat_day == null ? 1 : task.repeat_day;
                    } else if (task.repeat_type === 'monthly') {
                        document.getElementById('viewMonthlyDay').value = task.repeat_day == null ? 1 : task.repeat_day;
                    }
                } else {
                    repeatOptions.classList.remove('visible');
                    document.getElementById('viewRepeatType').value = 'daily';
                    document.getElementById('viewWeeklyDayGroup').style.display = 'none';
                    document.getElementById('viewMonthlyDayGroup').style.display = 'none';
                }

                document.getElementById('viewRepeatEditor').classList.remove('open');
                updateViewRepeatSummary();
                rememberViewTaskState();
                document.getElementById('viewTaskModal').classList.add('open');
            });
    }
    
    function saveViewTask() {
        const taskId = document.getElementById('viewTaskId').value;
        const title = document.getElementById('viewTaskTitleInput').value.trim();
        const date = document.getElementById('viewTaskDate').value;
        const duration = document.getElementById('viewTaskDuration').value.trim();
        const comment = document.getElementById('viewTaskComment').value.trim();
        const category = document.getElementById('viewTaskCategorySelect').value;
        const deadline_date = document.getElementById('viewDeadlineDate').value;
        const deadline_time = document.getElementById('viewDeadlineTime').value;
        const isRepeating = document.getElementById('viewTaskRepeat').checked;
        let repeatType = 'none';
        let repeatDay = null;

        if (!title) {
            alert('Введите название');
            return Promise.resolve(false);
        }

        if (isRepeating) {
            repeatType = document.getElementById('viewRepeatType').value;
            if (repeatType === 'weekly' || repeatType === 'biweekly') {
                repeatDay = parseInt(document.getElementById('viewRepeatDay').value);
            } else if (repeatType === 'monthly') {
                repeatDay = parseInt(document.getElementById('viewMonthlyDay').value);
            }
        }

        return fetch('/api/task/' + taskId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: title,
                date: date,
                duration: duration,
                comment: comment,
                category: category,
                repeat_type: repeatType,
                repeat_day: repeatDay,
                deadline_date: deadline_date,
                deadline_time: deadline_time
            })
        })
        .then(res => {
            if (!res.ok) throw new Error('Не удалось сохранить задачу');
            return res.json();
        })
        .then(() => {
            rememberViewTaskState();
            closeViewTaskModal();
            loadTasks();
            return true;
        })
        .catch(err => {
            alert(err.message || 'Не удалось сохранить задачу');
            return false;
        });
    }

    document.getElementById('viewTaskSave').addEventListener('click', saveViewTask);

    document.getElementById('viewTaskDelete').addEventListener('click', function() {
        if (currentViewTaskId && confirm('Удалить задачу навсегда?')) {
            fetch('/api/task/' + currentViewTaskId, { method: 'DELETE' })
                .then(() => {
                    closeViewTaskModal();
                    loadTasks();
                });
        }
    });
    
    document.getElementById('cancelViewBtn').addEventListener('click', requestCloseViewTask);

    document.getElementById('viewTaskModal').addEventListener('click', function(e) {
        if (e.target === this) requestCloseViewTask();
    });

    document.getElementById('unsavedTaskSave').addEventListener('click', function() {
        document.getElementById('unsavedTaskModal').classList.remove('open');
        saveViewTask();
    });

    document.getElementById('unsavedTaskDiscard').addEventListener('click', function() {
        closeViewTaskModal();
    });

    document.getElementById('unsavedTaskContinue').addEventListener('click', function() {
        document.getElementById('unsavedTaskModal').classList.remove('open');
    });

    document.getElementById('unsavedTaskModal').addEventListener('click', function(e) {
        if (e.target === this) this.classList.remove('open');
    });
    
    document.getElementById('viewRepeatSummary').addEventListener('click', function() {
        document.getElementById('viewRepeatEditor').classList.toggle('open');
    });

    document.getElementById('viewTaskRepeat').addEventListener('change', function() {
        const options = document.getElementById('viewRepeatOptions');
        if (this.checked) {
            options.classList.add('visible');
            const type = document.getElementById('viewRepeatType').value;
            document.getElementById('viewWeeklyDayGroup').style.display = (type === 'weekly' || type === 'biweekly') ? 'block' : 'none';
            document.getElementById('viewMonthlyDayGroup').style.display = type === 'monthly' ? 'block' : 'none';
        } else {
            options.classList.remove('visible');
            document.getElementById('viewWeeklyDayGroup').style.display = 'none';
            document.getElementById('viewMonthlyDayGroup').style.display = 'none';
        }
        updateViewRepeatSummary();
    });
    
    document.getElementById('viewRepeatType').addEventListener('change', function() {
        document.getElementById('viewWeeklyDayGroup').style.display = (this.value === 'weekly' || this.value === 'biweekly') ? 'block' : 'none';
        document.getElementById('viewMonthlyDayGroup').style.display = this.value === 'monthly' ? 'block' : 'none';
        updateViewRepeatSummary();
    });
    document.getElementById('viewRepeatDay').addEventListener('change', updateViewRepeatSummary);
    document.getElementById('viewMonthlyDay').addEventListener('change', updateViewRepeatSummary);
    
    document.querySelectorAll('.add-task-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const category = this.dataset.category;
            document.getElementById('addTaskCategory').value = category;
            document.getElementById('addTaskModalSub').textContent = 'Добавьте задачу в категорию: ' + getCategoryName(category);
            document.getElementById('addTaskTitle').value = '';
            setDateInputValue('addTaskDate', currentViewDate);
            document.getElementById('addTaskDuration').value = '';
            document.getElementById('addTaskComment').value = '';
            setDateInputValue('addDeadlineDate', '');
            document.getElementById('addDeadlineTime').value = '';
            document.getElementById('addTaskRepeat').checked = false;
            document.getElementById('addRepeatOptions').classList.remove('visible');
            document.getElementById('addWeeklyDayGroup').style.display = 'none';
            document.getElementById('addMonthlyDayGroup').style.display = 'none';
            document.getElementById('addTaskModal').classList.add('open');
            setTimeout(() => document.getElementById('addTaskTitle').focus(), 100);
        });
    });
    
    function getCategoryName(cat) {
        const names = {
            'focus': '🎯 Фокус',
            'urgent': '⚡ До 15 минут',
            'work': '💼 Работа',
            'home': '🏠 Дом',
            'personal': '❤️ Личное',
            'waiting': '⏳ Жду ответа',
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
        const duration = document.getElementById('addTaskDuration').value.trim();
        const comment = document.getElementById('addTaskComment').value.trim();
        const deadline_date = document.getElementById('addDeadlineDate').value;
        const deadline_time = document.getElementById('addDeadlineTime').value;
        const isRepeating = document.getElementById('addTaskRepeat').checked;
        let repeatType = 'none';
        let repeatDay = null;
        
        if (!title) { alert('Введите название'); return; }
        
        if (isRepeating) {
            repeatType = document.getElementById('addRepeatType').value;
            if (repeatType === 'weekly' || repeatType === 'biweekly') {
                repeatDay = parseInt(document.getElementById('addRepeatDay').value);
            } else if (repeatType === 'monthly') {
                repeatDay = parseInt(document.getElementById('addMonthlyDay').value);
            }
        }
        
        fetch('/api/task/direct', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                title: title, 
                category: category, 
                date: date, 
                duration: duration, 
                comment: comment, 
                repeat_type: repeatType, 
                repeat_day: repeatDay,
                deadline_date: deadline_date,
                deadline_time: deadline_time
            })
        })
        .then(res => res.json())
        .then(() => {
            document.getElementById('addTaskModal').classList.remove('open');
            loadTasks();
        });
    });
    
    document.getElementById('addTaskTitle').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') document.getElementById('saveAddTaskBtn').click();
    });
    
    document.getElementById('addTaskRepeat').addEventListener('change', function() {
        const options = document.getElementById('addRepeatOptions');
        if (this.checked) {
            options.classList.add('visible');
            const type = document.getElementById('addRepeatType').value;
            document.getElementById('addWeeklyDayGroup').style.display = (type === 'weekly' || type === 'biweekly') ? 'block' : 'none';
            document.getElementById('addMonthlyDayGroup').style.display = type === 'monthly' ? 'block' : 'none';
        } else {
            options.classList.remove('visible');
            document.getElementById('addWeeklyDayGroup').style.display = 'none';
            document.getElementById('addMonthlyDayGroup').style.display = 'none';
        }
    });
    
    document.getElementById('addRepeatType').addEventListener('change', function() {
        document.getElementById('addWeeklyDayGroup').style.display = (this.value === 'weekly' || this.value === 'biweekly') ? 'block' : 'none';
        document.getElementById('addMonthlyDayGroup').style.display = this.value === 'monthly' ? 'block' : 'none';
    });
    
    document.querySelectorAll('.move-cat-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const taskId = document.getElementById('moveTaskId').value;
            const category = this.dataset.category;
            
            fetch('/api/task/' + taskId + '/move', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category: category })
            })
            .then(res => res.json())
            .then(() => {
                document.getElementById('moveModal').classList.remove('open');
                loadTasks();
                updateEmptyBlocks();
            });
        });
    });
    
    document.getElementById('cancelMoveBtn').addEventListener('click', () => {
        document.getElementById('moveModal').classList.remove('open');
    });
    
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            const url = this.getAttribute('href');
            if (url) {
                window.location.href = url;
            }
        });
    });
    
    loadTasks();
</script>
</body>
</html>
'''

FUTURE_PAGE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📅 Будущие — Мой органайзер</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f6f2fd;
            padding: 16px;
            min-height: 100vh;
            color: #4a3f5e;
            -webkit-tap-highlight-color: transparent;
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
            touch-action: manipulation;
        }
        .header .btn-back:hover { background: #e0d5ec; }
        
        .date-group {
            margin-bottom: 20px;
        }
        .date-group .date-title {
            font-size: 18px;
            font-weight: 600;
            color: #4a3f5e;
            margin-bottom: 10px;
            padding-bottom: 6px;
            border-bottom: 2px solid #ede5f5;
        }
        .task-item {
            cursor: pointer;
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
            border-left: 4px solid #8e44ad;
        }
        .task-item .task-info {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .task-item .task-info .task-duration {
            font-size: 11px;
            color: #b5a7cc;
            background: #ede5f5;
            padding: 1px 8px;
            border-radius: 10px;
        }
        .task-item .task-info .comment-badge {
            font-size: 11px;
            color: #8b7bb5;
            background: #ede5f5;
            padding: 1px 8px;
            border-radius: 10px;
            cursor: help;
        }
        .task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 4px 6px;
            border-radius: 6px;
            touch-action: manipulation;
        }
        .task-item .task-actions button:hover { color: #8b7bb5; background: #ede5f5; }
        
        .empty-list { color: #c5b8d8; text-align: center; padding: 30px; }
        .modal-overlay { display:none; position:fixed; inset:0; background:rgba(40,30,55,.35); z-index:2000; align-items:center; justify-content:center; padding:16px; }
        .modal-overlay.open { display:flex; }
        .modal { width:min(520px, 100%); max-height:90vh; overflow-y:auto; background:#fcfaff; border-radius:14px; padding:20px; box-shadow:0 16px 50px rgba(40,30,55,.2); }
        .modal h3 { margin-bottom:12px; }
        .modal label { display:block; margin:9px 0 4px; font-size:13px; color:#8b7bb5; }
        .modal input, .modal textarea, .modal select { width:100%; padding:9px 11px; border:1.5px solid #ede5f5; border-radius:8px; background:white; color:#4a3f5e; outline:none; font:inherit; }
        .modal textarea { min-height:70px; resize:vertical; }
        .modal-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; flex-wrap:wrap; }
        .modal-actions button { border:none; border-radius:8px; padding:9px 14px; cursor:pointer; }
        .modal-actions .save { background:#8b7bb5; color:white; }
        .modal-actions .cancel { background:#ede5f5; color:#4a3f5e; }
        .repeat-row { display:flex; gap:8px; align-items:center; margin-top:10px; }
        .repeat-row input { width:auto; }
        .weekly-row { display:none; }
        .weekly-row.visible { display:block; }
        
        @media (max-width: 600px) {
            .header { flex-direction: column; text-align: center; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📅 Будущие</h1>
        <div>
            <span class="user">👤 {{ username }}</span>
            <a href="/" class="btn-back" style="margin-left:12px;">← Назад</a>
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#d5c8e6; color:#4a3f5e;">Выйти</a>
        </div>
    </div>
    
    <div id="futureContainer">
        {% if sorted_dates %}
            {% for date_key in sorted_dates %}
            <div class="date-group">
                <div class="date-title">{{ format_date_with_weekday(date_key) }}</div>
                {% for task in tasks_by_date[date_key] %}
                <div class="task-item" data-task-id="{{ task.id }}">
                    <div class="task-info">
                        <span>{{ task.title }}</span>
                        {% if task.duration %}
                        <span class="task-duration">⏱️ {{ task.duration }}</span>
                        {% endif %}
                        {% if task.comment and task.comment != '' %}
                        <span class="comment-badge" title="{{ task.comment }}">💬</span>
                        {% endif %}
                    </div>
                    <div class="task-actions">
                        <button class="done-btn" data-task-id="{{ task.id }}">✅</button>
                        <button class="delete-btn" data-task-id="{{ task.id }}">🗑️</button>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% endfor %}
        {% else %}
            <div class="empty-list">📭 Нет задач на будущие даты</div>
        {% endif %}
    </div>
</div>

<div class="modal-overlay" id="futureEditModal">
    <div class="modal">
        <h3>✏️ Редактировать задачу</h3>
        <input type="hidden" id="futureEditId">
        <label>Название задачи</label><input type="text" id="futureEditTitle">
        <label>📅 Дата выполнения</label><input type="date" id="futureEditDate">
        <label>⏱️ Время выполнения</label><input type="text" id="futureEditDuration" placeholder="1 ч">
        <label>💬 Комментарий</label><textarea id="futureEditComment"></textarea>
        <label>⏰ Дедлайн (дата)</label><input type="date" id="futureEditDeadlineDate">
        <label>⏰ Дедлайн (время)</label><input type="time" id="futureEditDeadlineTime">
        <label>📂 Категория</label>
        <select id="futureEditCategory">
            <option value="focus">🎯 Фокус</option><option value="urgent">⚡ До 15 минут</option>
            <option value="work">💼 Работа</option><option value="home">🏠 Дом</option>
            <option value="personal">❤️ Личное</option><option value="waiting">⏳ Жду ответа</option>
            <option value="later">🕰️ Позже</option>
        </select>
        <div class="repeat-row"><input type="checkbox" id="futureEditRepeat"><label for="futureEditRepeat" style="margin:0;">🔄 Повторяющаяся задача</label></div>
        <div id="futureRepeatSettings" style="display:none;">
            <label>Тип повторения</label>
            <select id="futureRepeatType"><option value="daily">📆 Каждый день</option><option value="weekly">📅 Каждую неделю</option><option value="biweekly">🗓️ Каждые 2 недели</option><option value="monthly">📌 Каждый месяц</option></select>
            <div class="weekly-row" id="futureWeeklyRow">
                <label>День недели</label>
                <select id="futureRepeatDay"><option value="0">Воскресенье</option><option value="1">Понедельник</option><option value="2">Вторник</option><option value="3">Среда</option><option value="4">Четверг</option><option value="5">Пятница</option><option value="6">Суббота</option></select>
            </div>
            <div class="weekly-row" id="futureMonthlyRow">
                <label>Число месяца</label>
                <select id="futureMonthlyDay"><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option><option value="6">6</option><option value="7">7</option><option value="8">8</option><option value="9">9</option><option value="10">10</option><option value="11">11</option><option value="12">12</option><option value="13">13</option><option value="14">14</option><option value="15">15</option><option value="16">16</option><option value="17">17</option><option value="18">18</option><option value="19">19</option><option value="20">20</option><option value="21">21</option><option value="22">22</option><option value="23">23</option><option value="24">24</option><option value="25">25</option><option value="26">26</option><option value="27">27</option><option value="28">28</option><option value="29">29</option><option value="30">30</option><option value="31">31</option></select>
            </div>
        </div>
        <div class="modal-actions"><button class="save" id="futureEditSave">💾 Сохранить</button><button class="cancel" id="futureEditCancel">Закрыть</button></div>
    </div>
</div>

<script>
    const futureModal = document.getElementById('futureEditModal');
    const repeatCheck = document.getElementById('futureEditRepeat');
    const repeatType = document.getElementById('futureRepeatType');

    function syncFutureRepeatUI() {
        document.getElementById('futureRepeatSettings').style.display = repeatCheck.checked ? 'block' : 'none';
        const weeklyKind = repeatType.value === 'weekly' || repeatType.value === 'biweekly';
        document.getElementById('futureWeeklyRow').classList.toggle('visible', repeatCheck.checked && weeklyKind);
        document.getElementById('futureMonthlyRow').classList.toggle('visible', repeatCheck.checked && repeatType.value === 'monthly');
    }
    repeatCheck.addEventListener('change', syncFutureRepeatUI);
    repeatType.addEventListener('change', syncFutureRepeatUI);

    function openFutureTask(taskId) {
        fetch('/api/task/' + taskId)
            .then(res => { if (!res.ok) throw new Error('Не удалось открыть задачу'); return res.json(); })
            .then(task => {
                document.getElementById('futureEditId').value = task.id;
                document.getElementById('futureEditTitle').value = task.title || '';
                document.getElementById('futureEditDate').value = task.date || '';
                document.getElementById('futureEditDuration').value = task.duration || '';
                document.getElementById('futureEditComment').value = task.comment || '';
                document.getElementById('futureEditDeadlineDate').value = task.deadline_date || '';
                document.getElementById('futureEditDeadlineTime').value = task.deadline_time || '';
                document.getElementById('futureEditCategory').value = task.category || 'personal';
                repeatCheck.checked = task.repeat_type && task.repeat_type !== 'none';
                repeatType.value = ['daily','weekly','biweekly','monthly'].includes(task.repeat_type) ? task.repeat_type : 'daily';
                document.getElementById('futureRepeatDay').value = task.repeat_day == null ? '1' : String(task.repeat_day);
                document.getElementById('futureMonthlyDay').value = task.repeat_day == null ? '1' : String(task.repeat_day);
                syncFutureRepeatUI();
                futureModal.classList.add('open');
            }).catch(err => alert(err.message));
    }

    document.querySelectorAll('.task-item').forEach(item => {
        item.addEventListener('click', function(e) {
            if (e.target.closest('button')) return;
            openFutureTask(this.dataset.taskId);
        });
    });

    document.getElementById('futureEditCancel').addEventListener('click', () => futureModal.classList.remove('open'));
    futureModal.addEventListener('click', e => { if (e.target === futureModal) futureModal.classList.remove('open'); });

    document.getElementById('futureEditSave').addEventListener('click', function() {
        const taskId = document.getElementById('futureEditId').value;
        const title = document.getElementById('futureEditTitle').value.trim();
        if (!title) { alert('Введите название задачи'); return; }
        const isRepeat = repeatCheck.checked;
        const payload = {
            title,
            category: document.getElementById('futureEditCategory').value,
            date: document.getElementById('futureEditDate').value,
            duration: document.getElementById('futureEditDuration').value.trim(),
            comment: document.getElementById('futureEditComment').value.trim(),
            deadline_date: document.getElementById('futureEditDeadlineDate').value,
            deadline_time: document.getElementById('futureEditDeadlineTime').value,
            repeat_type: isRepeat ? repeatType.value : 'none',
            repeat_day: !isRepeat ? null :
                ((repeatType.value === 'weekly' || repeatType.value === 'biweekly')
                    ? parseInt(document.getElementById('futureRepeatDay').value)
                    : (repeatType.value === 'monthly' ? parseInt(document.getElementById('futureMonthlyDay').value) : null))
        };
        fetch('/api/task/' + taskId, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) })
            .then(res => { if (!res.ok) throw new Error('Не удалось сохранить задачу'); location.reload(); })
            .catch(err => alert(err.message));
    });

    document.querySelectorAll('.done-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            fetch('/api/task/' + this.dataset.taskId + '/done', { method: 'POST' })
                .then(async res => {
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(data.message || 'Не удалось завершить задачу');
                    location.reload();
                })
                .catch(err => alert(err.message));
        });
    });
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (confirm('Удалить задачу?')) fetch('/api/task/' + this.dataset.taskId, { method: 'DELETE' }).then(() => location.reload());
        });
    });
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
            -webkit-tap-highlight-color: transparent;
        }
        .container { max-width: 900px; margin: 0 auto; }
        .header {
            background: #fcfaff; border-radius: 12px; padding: 16px 24px;
            margin-bottom: 20px; display: flex; justify-content: space-between;
            align-items: center; flex-wrap: wrap; gap: 10px;
            box-shadow: 0 2px 10px rgba(139,123,181,.08);
        }
        .header h1 { font-size: 22px; }
        .user { color: #8b7bb5; font-size: 14px; }
        .btn-back {
            background: #ede5f5; color: #4a3f5e; border: none; padding: 8px 18px;
            border-radius: 8px; text-decoration: none; cursor: pointer;
        }
        .quarter-nav {
            display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; justify-content: center;
        }
        .q-link {
            padding: 8px 16px; border-radius: 8px; text-decoration: none;
            background: #fcfaff; color: #4a3f5e; border: 1.5px solid #ede5f5; font-size: 14px;
        }
        .q-link.current { background: #8b7bb5; color: white; border-color: #8b7bb5; }
        .q-link.past { opacity: .6; }

        .add-sphere {
            background: #fcfaff; border-radius: 12px; padding: 16px 20px; margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(139,123,181,.08); display: flex; gap: 10px; flex-wrap: wrap;
        }
        .add-sphere input, .add-task-form input, .subtask-add input, .modal input, .modal textarea {
            border: 1.5px solid #e5daef; border-radius: 8px; background: white; color: #4a3f5e;
            font-family: inherit; font-size: 14px; outline: none;
        }
        .add-sphere input:focus, .add-task-form input:focus, .subtask-add input:focus, .modal input:focus, .modal textarea:focus {
            border-color: #8b7bb5;
        }
        .add-sphere input { flex: 1; min-width: 160px; padding: 10px 14px; }
        button { font-family: inherit; }
        .primary-btn {
            background: #8b7bb5; color: white; border: none; border-radius: 8px; padding: 9px 18px;
            cursor: pointer;
        }

        .sphere {
            background: #fcfaff; border-radius: 12px; padding: 18px 20px; margin-bottom: 16px;
            box-shadow: 0 2px 10px rgba(139,123,181,.08); border-left: 5px solid #d5c8e6;
        }
        .sphere-header {
            display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 12px;
        }
        .sphere-header h3 { font-size: 18px; }
        .sphere-actions { display:flex; gap:4px; }
        .icon-btn, .task-action, .subtask-delete {
            background: none; border: none; cursor: pointer; border-radius: 6px; padding: 4px 6px;
            color: #aa9abb;
        }
        .icon-btn:hover, .task-action:hover, .subtask-delete:hover { background: #eee5f6; color: #75658e; }

        .tasks-container { min-height: 8px; }
        .task-item {
            background: #faf5ff; border-radius: 9px; padding: 11px 12px; margin-bottom: 8px;
            box-shadow: 0 1px 4px rgba(139,123,181,.05); border: 1px solid transparent;
            transition: transform .12s, opacity .12s, border-color .12s;
        }
        .task-item.dragging { opacity: .45; transform: scale(.995); }
        .task-item.drag-over { border-color: #a998c3; }
        .task-main {
            display: flex; justify-content: space-between; gap: 10px; align-items: flex-start;
        }
        .task-left { display: flex; gap: 8px; min-width: 0; flex: 1; align-items: flex-start; }
        .drag-handle { color: #c0b2d2; cursor: grab; user-select: none; padding-top: 2px; }
        .task-content { min-width: 0; flex: 1; }
        .task-title-row { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
        .task-title { font-size: 16px; line-height: 1.4; font-weight: 500; word-break: break-word; cursor: pointer; }
        .task-title:hover, .subtask-title:hover { color: #7d6b98; }
        .task-comment { font-size: 13px; color: #998bac; margin-top: 3px; white-space: pre-wrap; }
        .task-deadline { font-size: 12px; color: #9a7a79; margin-top: 4px; }
        .task-actions { display: flex; gap: 2px; flex-shrink: 0; }

        .subtasks { margin: 8px 0 0 27px; }
        .subtask {
            display: flex; align-items: flex-start; gap: 7px; padding: 5px 0; font-size: 14px; line-height: 1.35; color: #655a75;
            border: 1px solid transparent; border-radius: 5px;
        }
        .subtask.dragging { opacity: .45; }
        .subtask-drag { background:none; border:none; color:#c0b2d2; cursor:grab; padding:0 2px; font-size:15px; }
        .subtask input[type="checkbox"] { accent-color: #8b7bb5; width: 15px; height: 15px; }
        .subtask.done .subtask-title { text-decoration: line-through; color: #aaa0b5; }
        .subtask-content { flex:1; min-width:0; }
        .subtask-title { word-break: break-word; cursor: pointer; font-size:14px; }
        .subtask-comment { font-size:12px; color:#998bac; margin-top:2px; white-space:pre-wrap; }
        .subtask-deadline { font-size:11px; color:#9a7a79; margin-top:2px; }
        .subtask.done .subtask-comment, .subtask.done .subtask-deadline { color:#b7adbf; }
        .subtask-delete { font-size: 12px; padding: 2px 4px; opacity: .65; }
        .subtask-add { display: none; gap: 6px; margin-top: 5px; }
        .subtask-add.open { display: flex; }
        .subtask-add input { flex: 1; min-width: 80px; padding: 6px 9px; font-size: 13px; }
        .subtask-add button {
            border: none; background: #e9e0f2; color: #75658e; border-radius: 7px; padding: 4px 8px; cursor: pointer;
        }
        .add-subtask-toggle { font-weight:700; font-size:18px; line-height:1; padding:0 4px; color:#9b8aae; }
        .add-subtask-toggle:hover { background:#eee5f6; color:#75658e; }
        .completed-section {
            background:#fcfaff; border-radius:12px; padding:18px 20px; margin-top:26px; margin-bottom:16px;
            box-shadow:0 2px 10px rgba(139,123,181,.08); border-left:5px solid #b8d8c0;
        }
        .completed-section h3 { font-size:18px; margin-bottom:12px; }
        .completed-item { background:#f7faf7; border:1px solid #e4efe6; border-radius:9px; padding:11px 12px; margin-bottom:8px; }
        .completed-title { font-size:15px; text-decoration:line-through; color:#776e7f; }
        .completed-meta { font-size:11px; color:#9a90a2; margin-top:3px; }
        .completed-comment { font-size:12px; color:#998bac; margin-top:4px; white-space:pre-wrap; }
        .completed-subtasks { margin:7px 0 0 18px; }
        .completed-subtask { font-size:13px; color:#aaa0b5; padding:2px 0; }
        .completed-subtask-title { text-decoration:line-through; }
        .completed-empty { color:#b8aac8; font-style:italic; padding:8px 2px 10px; font-size:13px; }

        .add-task-form { display: flex; gap: 8px; margin-top: 12px; }
        .add-task-form input { flex:1; padding:8px 12px; min-width:100px; }
        .add-task-form button { padding:8px 14px; }
        .empty-sphere { color:#b8aac8; font-style:italic; padding:8px 2px 10px; font-size:13px; }

        .modal-overlay {
            position: fixed; inset: 0; background: rgba(45,36,55,.28); display:none;
            align-items:center; justify-content:center; padding:16px; z-index:1000;
        }
        .modal-overlay.open { display:flex; }
        .modal {
            background:#fffdfd; width:100%; max-width:460px; border-radius:14px; padding:20px;
            box-shadow:0 16px 50px rgba(40,30,55,.18);
        }
        .modal h3 { margin-bottom:14px; font-size:18px; }
        .modal label { display:block; font-size:12px; color:#817490; margin:10px 0 5px; }
        .modal input, .modal textarea { width:100%; padding:9px 11px; }
        .modal textarea { min-height:80px; resize:vertical; }
        .modal-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
        .modal-actions button { border:none; border-radius:8px; padding:8px 14px; cursor:pointer; }
        .save-btn { background:#8b7bb5; color:white; }
        .cancel-btn { background:#ede5f5; color:#4a3f5e; }

        @media (max-width: 600px) {
            .header { flex-direction:column; text-align:center; }
            .add-sphere { flex-direction:column; }
            .add-sphere input { width:100%; }
            .task-item { padding:10px; }
            .subtasks { margin-left:20px; }
            .drag-handle { font-size:18px; }
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
            <a href="/logout" class="btn-back" style="margin-left:8px; background:#d5c8e6;">Выйти</a>
        </div>
    </div>

    <div class="quarter-nav">
        {% for q in quarters %}
        <a href="/quarter/{{ q.id }}" class="q-link {% if q.id == quarter %}current{% endif %} {% if q.id != quarter and q.id < current_quarter %}past{% endif %}">
            {{ q.name }} {{ q.year }}{% if q.current %} ⭐{% endif %}
        </a>
        {% endfor %}
    </div>

    <div class="add-sphere">
        <input type="text" id="sphereName" placeholder="Название сферы (например: Работа, Здоровье...)">
        <button class="primary-btn" id="addSphereBtn">➕ Добавить сферу</button>
    </div>

    <div id="spheresContainer">
        {% for sphere in spheres %}
        <div class="sphere" data-sphere-id="{{ sphere.id }}" data-sphere-name="{{ sphere.name }}">
            <div class="sphere-header">
                <h3>📂 {{ sphere.name }}</h3>
                <div class="sphere-actions">
                    <button class="icon-btn edit-sphere-btn" data-sphere-id="{{ sphere.id }}" data-sphere-name="{{ sphere.name }}" title="Переименовать">✏️</button>
                    <button class="icon-btn delete-sphere-btn" data-sphere-id="{{ sphere.id }}" data-sphere-name="{{ sphere.name }}" title="Удалить">🗑️</button>
                </div>
            </div>

            <div class="tasks-container" data-sphere-id="{{ sphere.id }}">
                {% for task in sphere.tasks %}
                <div class="task-item" data-task-id="{{ task.id }}">
                    <div class="task-main">
                        <div class="task-left">
                            <span class="drag-handle" title="Перетащить">⋮⋮</span>
                            <div class="task-content">
                                <div class="task-title-row">
                                    <div class="task-title" title="Нажмите, чтобы изменить задачу">{{ task.title }}</div>
                                    <button class="task-action add-subtask-toggle" title="Добавить подзадачу">＋</button>
                                </div>
                                <div class="task-comment" {% if not task.comment %}style="display:none"{% endif %}>{{ task.comment or '' }}</div>
                                <div class="task-deadline" {% if not task.deadline_date %}style="display:none"{% endif %}>⏰ {{ format_date_ru(task.deadline_date) if task.deadline_date else '' }}</div>
                            </div>
                        </div>
                        <div class="task-actions">
                            <button class="task-action done-btn" title="Готово">✅</button>
                            <button class="task-action delete-btn" title="Удалить">🗑️</button>
                        </div>
                    </div>
                    <div class="subtasks">
                        <div class="subtask-list">
                            {% for subtask in task.subtasks %}
                            <div class="subtask {% if subtask.is_done %}done{% endif %}" data-subtask-id="{{ subtask.id }}">
                                <button class="subtask-drag" title="Перетащить">⋮⋮</button>
                                <input type="checkbox" class="subtask-check" {% if subtask.is_done %}checked{% endif %}>
                                <div class="subtask-content">
                                    <div class="subtask-title" title="Нажмите, чтобы изменить подзадачу">{{ subtask.title }}</div>
                                    <div class="subtask-comment" {% if not subtask.comment %}style="display:none"{% endif %}>{{ subtask.comment or '' }}</div>
                                    <div class="subtask-deadline" {% if not subtask.deadline_date %}style="display:none"{% endif %}>⏰ {{ format_date_ru(subtask.deadline_date) if subtask.deadline_date else '' }}</div>
                                </div>
                                <button class="subtask-delete" title="Удалить подзадачу">✕</button>
                            </div>
                            {% endfor %}
                        </div>
                        <div class="subtask-add">
                            <input type="text" class="subtask-input" placeholder="Новая подзадача...">
                            <button class="add-subtask-btn" title="Добавить">Добавить</button>
                        </div>
                    </div>
                </div>
                {% else %}
                <div class="empty-sphere">Нет задач в этой сфере</div>
                {% endfor %}
            </div>

            <div class="add-task-form">
                <input type="text" class="taskInput" placeholder="Новая задача...">
                <button class="primary-btn addTaskBtn" data-sphere="{{ sphere.name }}">➕ Добавить</button>
            </div>
        </div>
        {% else %}
        <div style="text-align:center;padding:40px;color:#b8aac8;background:#fcfaff;border-radius:12px;">
            <p style="font-size:18px;">📭 Нет сфер</p>
            <p style="font-size:14px;">Добавьте первую сферу выше</p>
        </div>
        {% endfor %}
    </div>

    <div class="completed-section" id="completedSection">
        <h3>✅ Готово</h3>
        <div id="completedTasks">
            {% for task in completed_tasks %}
            <div class="completed-item" data-task-id="{{ task.id }}">
                <div class="completed-title">{{ task.title }}</div>
                <div class="completed-meta">{% if task.sphere %}📂 {{ task.sphere }}{% endif %}</div>
                {% if task.comment %}<div class="completed-comment">{{ task.comment }}</div>{% endif %}
                {% if task.deadline_date %}<div class="task-deadline">⏰ {{ format_date_ru(task.deadline_date) }}</div>{% endif %}
                {% if task.subtasks %}
                <div class="completed-subtasks">
                    {% for subtask in task.subtasks %}
                    <div class="completed-subtask">
                        <div class="completed-subtask-title">✓ {{ subtask.title }}</div>
                        {% if subtask.comment %}<div style="font-size:11px;text-decoration:none;color:#aaa0b5;margin-left:14px;">{{ subtask.comment }}</div>{% endif %}
                        {% if subtask.deadline_date %}<div style="font-size:10px;text-decoration:none;color:#b7adbf;margin-left:14px;">⏰ {{ format_date_ru(subtask.deadline_date) }}</div>{% endif %}
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
            {% else %}
            <div class="completed-empty">Здесь появятся выполненные задачи квартала</div>
            {% endfor %}
        </div>
    </div>
</div>

<div class="modal-overlay" id="quarterTaskModal">
    <div class="modal">
        <h3>✏️ Изменить задачу</h3>
        <input type="hidden" id="quarterEditTaskId">
        <label for="quarterEditTitle">Название</label>
        <input type="text" id="quarterEditTitle">
        <label for="quarterEditComment">Комментарий</label>
        <textarea id="quarterEditComment" placeholder="Комментарий будет виден мелким шрифтом под задачей"></textarea>
        <label for="quarterEditDeadline">⏰ Дедлайн</label>
        <input type="date" id="quarterEditDeadline">
        <div class="modal-actions">
            <button class="cancel-btn" id="quarterEditCancel">Отмена</button>
            <button class="save-btn" id="quarterEditSave">Сохранить</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="subtaskEditModal">
    <div class="modal">
        <h3>✏️ Изменить подзадачу</h3>
        <input type="hidden" id="subtaskEditId">
        <label for="subtaskEditTitle">Название</label>
        <input type="text" id="subtaskEditTitle">
        <label for="subtaskEditComment">Комментарий</label>
        <textarea id="subtaskEditComment" placeholder="Комментарий будет виден под подзадачей"></textarea>
        <label for="subtaskEditDeadline">⏰ Дедлайн</label>
        <input type="date" id="subtaskEditDeadline">
        <div class="modal-actions">
            <button class="cancel-btn" id="subtaskEditCancel">Отмена</button>
            <button class="save-btn" id="subtaskEditSave">Сохранить</button>
        </div>
    </div>
</div>

<script>
const quarter = '{{ quarter }}';
const spheresContainer = document.getElementById('spheresContainer');
let draggedTask = null;
let draggedSubtask = null;

function formatDeadline(value) {
    if (!value) return '';
    const parts = value.split('-');
    return parts.length === 3 ? parts[2] + '.' + parts[1] + '.' + parts[0] : value;
}

function ensureEmptyState(container) {
    const hasTasks = !!container.querySelector(':scope > .task-item');
    const empty = container.querySelector(':scope > .empty-sphere');
    if (!hasTasks && !empty) {
        const el = document.createElement('div');
        el.className = 'empty-sphere';
        el.textContent = 'Нет задач в этой сфере';
        container.appendChild(el);
    } else if (hasTasks && empty) {
        empty.remove();
    }
}

function ensureCompletedEmptyState() {
    const container = document.getElementById('completedTasks');
    if (!container) return;
    const hasTasks = !!container.querySelector('.completed-item');
    const empty = container.querySelector('.completed-empty');
    if (!hasTasks && !empty) {
        const el = document.createElement('div');
        el.className = 'completed-empty';
        el.textContent = 'Здесь появятся выполненные задачи квартала';
        container.appendChild(el);
    } else if (hasTasks && empty) {
        empty.remove();
    }
}

function applySubtaskData(row, subtask) {
    const title = row.querySelector('.subtask-title');
    const comment = row.querySelector('.subtask-comment');
    const deadline = row.querySelector('.subtask-deadline');
    if (title) title.textContent = subtask.title || '';
    if (comment) {
        comment.textContent = subtask.comment || '';
        comment.style.display = subtask.comment ? '' : 'none';
    }
    if (deadline) {
        deadline.textContent = subtask.deadline_date ? '⏰ ' + formatDeadline(subtask.deadline_date) : '';
        deadline.style.display = subtask.deadline_date ? '' : 'none';
    }
}

function buildSubtask(subtask) {
    const row = document.createElement('div');
    row.className = 'subtask' + (subtask.is_done ? ' done' : '');
    row.dataset.subtaskId = subtask.id;

    const drag = document.createElement('button');
    drag.className = 'subtask-drag';
    drag.title = 'Перетащить';
    drag.textContent = '⋮⋮';

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'subtask-check';
    check.checked = !!subtask.is_done;

    const content = document.createElement('div');
    content.className = 'subtask-content';

    const title = document.createElement('div');
    title.className = 'subtask-title';
    title.title = 'Нажмите, чтобы изменить подзадачу';

    const comment = document.createElement('div');
    comment.className = 'subtask-comment';

    const deadline = document.createElement('div');
    deadline.className = 'subtask-deadline';

    content.append(title, comment, deadline);

    const del = document.createElement('button');
    del.className = 'subtask-delete';
    del.title = 'Удалить подзадачу';
    del.textContent = '✕';

    row.append(drag, check, content, del);
    applySubtaskData(row, subtask);
    return row;
}

function buildTask(task) {
    const card = document.createElement('div');
    card.className = 'task-item';
    card.dataset.taskId = task.id;

    const main = document.createElement('div');
    main.className = 'task-main';

    const left = document.createElement('div');
    left.className = 'task-left';
    const handle = document.createElement('span');
    handle.className = 'drag-handle';
    handle.title = 'Перетащить';
    handle.textContent = '⋮⋮';

    const content = document.createElement('div');
    content.className = 'task-content';
    const titleRow = document.createElement('div');
    titleRow.className = 'task-title-row';
    const title = document.createElement('div');
    title.className = 'task-title';
    title.title = 'Нажмите, чтобы изменить задачу';
    title.textContent = task.title || '';
    const addSubtaskToggle = document.createElement('button');
    addSubtaskToggle.className = 'task-action add-subtask-toggle';
    addSubtaskToggle.title = 'Добавить подзадачу';
    addSubtaskToggle.textContent = '＋';
    titleRow.append(title, addSubtaskToggle);
    const comment = document.createElement('div');
    comment.className = 'task-comment';
    comment.textContent = task.comment || '';
    comment.style.display = task.comment ? '' : 'none';
    const deadline = document.createElement('div');
    deadline.className = 'task-deadline';
    deadline.textContent = task.deadline_date ? '⏰ ' + formatDeadline(task.deadline_date) : '';
    deadline.style.display = task.deadline_date ? '' : 'none';
    content.append(titleRow, comment, deadline);
    left.append(handle, content);

    const actions = document.createElement('div');
    actions.className = 'task-actions';
    [['✅','done-btn','Готово'],['🗑️','delete-btn','Удалить']].forEach(([txt, cls, ttl]) => {
        const button = document.createElement('button');
        button.className = 'task-action ' + cls;
        button.title = ttl;
        button.textContent = txt;
        actions.appendChild(button);
    });
    main.append(left, actions);

    const subtasks = document.createElement('div');
    subtasks.className = 'subtasks';
    const list = document.createElement('div');
    list.className = 'subtask-list';
    (task.subtasks || []).forEach(st => list.appendChild(buildSubtask(st)));
    const add = document.createElement('div');
    add.className = 'subtask-add';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'subtask-input';
    input.placeholder = 'Новая подзадача...';
    const addBtn = document.createElement('button');
    addBtn.className = 'add-subtask-btn';
    addBtn.textContent = 'Добавить';
    add.append(input, addBtn);
    subtasks.append(list, add);

    card.append(main, subtasks);
    normalizeSubtasks(card, false);
    return card;
}

function buildCompletedFromCard(card) {
    const item = document.createElement('div');
    item.className = 'completed-item';
    item.dataset.taskId = card.dataset.taskId;

    const title = document.createElement('div');
    title.className = 'completed-title';
    title.textContent = card.querySelector('.task-title')?.textContent || '';
    item.appendChild(title);

    const sphere = card.closest('.sphere')?.dataset.sphereName || '';
    if (sphere) {
        const meta = document.createElement('div');
        meta.className = 'completed-meta';
        meta.textContent = '📂 ' + sphere;
        item.appendChild(meta);
    }

    const comment = card.querySelector('.task-comment')?.textContent || '';
    if (comment) {
        const c = document.createElement('div');
        c.className = 'completed-comment';
        c.textContent = comment;
        item.appendChild(c);
    }

    const deadline = card.querySelector('.task-deadline')?.textContent || '';
    if (deadline) {
        const d = document.createElement('div');
        d.className = 'task-deadline';
        d.textContent = deadline;
        item.appendChild(d);
    }

    const rows = card.querySelectorAll('.subtask');
    if (rows.length) {
        const list = document.createElement('div');
        list.className = 'completed-subtasks';
        rows.forEach(row => {
            const st = document.createElement('div');
            st.className = 'completed-subtask';

            const stTitle = document.createElement('div');
            stTitle.className = 'completed-subtask-title';
            stTitle.textContent = '✓ ' + (row.querySelector('.subtask-title')?.textContent || '');
            st.appendChild(stTitle);

            const stCommentText = row.querySelector('.subtask-comment')?.textContent || '';
            if (stCommentText) {
                const stComment = document.createElement('div');
                stComment.style.cssText = 'font-size:11px;text-decoration:none;color:#aaa0b5;margin-left:14px;';
                stComment.textContent = stCommentText;
                st.appendChild(stComment);
            }

            const stDeadlineText = row.querySelector('.subtask-deadline')?.textContent || '';
            if (stDeadlineText) {
                const stDeadline = document.createElement('div');
                stDeadline.style.cssText = 'font-size:10px;text-decoration:none;color:#b7adbf;margin-left:14px;';
                stDeadline.textContent = stDeadlineText;
                st.appendChild(stDeadline);
            }
            list.appendChild(st);
        });
        item.appendChild(list);
    }
    return item;
}

function normalizeSubtasks(card, persist = true) {
    const list = card.querySelector('.subtask-list');
    if (!list) return;
    const rows = Array.from(list.querySelectorAll(':scope > .subtask'));
    rows.filter(row => !row.classList.contains('done')).forEach(row => list.appendChild(row));
    rows.filter(row => row.classList.contains('done')).forEach(row => list.appendChild(row));
    if (persist && rows.length) saveSubtaskOrder(card);
}

function saveQuarterOrder(container) {
    const taskIds = Array.from(container.querySelectorAll(':scope > .task-item')).map(el => Number(el.dataset.taskId));
    if (!taskIds.length) return;
    fetch('/api/quarter/tasks/reorder', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({task_ids: taskIds, sphere_id: Number(container.dataset.sphereId), quarter})
    }).catch(() => {});
}

function saveSubtaskOrder(card) {
    const ids = Array.from(card.querySelectorAll('.subtask-list > .subtask')).map(el => Number(el.dataset.subtaskId));
    if (!ids.length) return;
    fetch('/api/task/' + card.dataset.taskId + '/subtasks/reorder', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({subtask_ids: ids})
    }).catch(() => {});
}

function openSubtaskEditor(row) {
    const subtaskId = row.dataset.subtaskId;
    fetch('/api/subtask/' + subtaskId)
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(subtask => {
            document.getElementById('subtaskEditId').value = subtask.id;
            document.getElementById('subtaskEditTitle').value = subtask.title || '';
            document.getElementById('subtaskEditComment').value = subtask.comment || '';
            document.getElementById('subtaskEditDeadline').value = subtask.deadline_date || '';
            document.getElementById('subtaskEditModal').classList.add('open');
        })
        .catch(() => alert('Не удалось открыть подзадачу'));
}

function openQuarterEditor(card) {
    fetch('/api/task/' + card.dataset.taskId)
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(task => {
            document.getElementById('quarterEditTaskId').value = task.id;
            document.getElementById('quarterEditTitle').value = task.title || '';
            document.getElementById('quarterEditComment').value = task.comment || '';
            document.getElementById('quarterEditDeadline').value = task.deadline_date || '';
            document.getElementById('quarterTaskModal').classList.add('open');
        })
        .catch(() => alert('Не удалось открыть задачу'));
}

function closeSubtaskForms(exceptCard = null) {
    document.querySelectorAll('.subtask-add.open').forEach(form => {
        if (!exceptCard || !exceptCard.contains(form)) {
            form.classList.remove('open');
            form.querySelector('.subtask-input').value = '';
        }
    });
}

function submitSubtask(card) {
    const form = card.querySelector('.subtask-add');
    const input = form.querySelector('.subtask-input');
    const title = input.value.trim();
    if (!title) return;

    fetch('/api/task/' + card.dataset.taskId + '/subtasks', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({title})
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(data => {
        card.querySelector('.subtask-list').appendChild(buildSubtask(data.subtask));
        input.value = '';
        form.classList.remove('open');
        normalizeSubtasks(card);
    })
    .catch(() => alert('Не удалось добавить подзадачу'));
}

document.getElementById('quarterEditCancel').addEventListener('click', () => {
    document.getElementById('quarterTaskModal').classList.remove('open');
});
document.getElementById('quarterTaskModal').addEventListener('click', e => {
    if (e.target.id === 'quarterTaskModal') e.currentTarget.classList.remove('open');
});
document.getElementById('quarterEditSave').addEventListener('click', () => {
    const taskId = document.getElementById('quarterEditTaskId').value;
    const title = document.getElementById('quarterEditTitle').value.trim();
    const comment = document.getElementById('quarterEditComment').value.trim();
    const deadline = document.getElementById('quarterEditDeadline').value;
    if (!title) { alert('Введите название'); return; }

    fetch('/api/task/' + taskId + '/quarter_edit', {
        method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({title, comment, deadline_date:deadline})
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(data => {
        const card = document.querySelector('.task-item[data-task-id="' + taskId + '"]');
        if (card) {
            card.querySelector('.task-title').textContent = data.task.title || '';
            const commentEl = card.querySelector('.task-comment');
            commentEl.textContent = data.task.comment || '';
            commentEl.style.display = data.task.comment ? '' : 'none';
            const deadlineEl = card.querySelector('.task-deadline');
            deadlineEl.textContent = data.task.deadline_date ? '⏰ ' + formatDeadline(data.task.deadline_date) : '';
            deadlineEl.style.display = data.task.deadline_date ? '' : 'none';
        }
        document.getElementById('quarterTaskModal').classList.remove('open');
    })
    .catch(() => alert('Не удалось сохранить задачу'));
});


document.getElementById('subtaskEditCancel').addEventListener('click', () => {
    document.getElementById('subtaskEditModal').classList.remove('open');
});
document.getElementById('subtaskEditModal').addEventListener('click', e => {
    if (e.target.id === 'subtaskEditModal') e.currentTarget.classList.remove('open');
});
document.getElementById('subtaskEditSave').addEventListener('click', () => {
    const subtaskId = document.getElementById('subtaskEditId').value;
    const title = document.getElementById('subtaskEditTitle').value.trim();
    const comment = document.getElementById('subtaskEditComment').value.trim();
    const deadline = document.getElementById('subtaskEditDeadline').value;
    if (!title) { alert('Введите название'); return; }

    fetch('/api/subtask/' + subtaskId, {
        method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({title, comment, deadline_date:deadline})
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(data => {
        const row = document.querySelector('.subtask[data-subtask-id="' + subtaskId + '"]');
        if (row) applySubtaskData(row, data.subtask);
        document.getElementById('subtaskEditModal').classList.remove('open');
    })
    .catch(() => alert('Не удалось сохранить подзадачу'));
});

// Редкие действия со сферами оставляем с перезагрузкой страницы.
document.getElementById('addSphereBtn').addEventListener('click', function() {
    const name = document.getElementById('sphereName').value.trim();
    if (!name) { alert('Введите название сферы'); return; }
    fetch('/api/sphere', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, quarter})
    }).then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(() => location.reload()).catch(() => alert('Не удалось добавить сферу'));
});
document.getElementById('sphereName').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('addSphereBtn').click();
});
document.querySelectorAll('.edit-sphere-btn').forEach(btn => {
    btn.addEventListener('click', function() {
        const newName = prompt('Введите новое название сферы:', this.dataset.sphereName);
        if (!newName || !newName.trim()) return;
        fetch('/api/sphere/' + this.dataset.sphereId, {
            method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:newName.trim()})
        }).then(() => location.reload());
    });
});
document.querySelectorAll('.delete-sphere-btn').forEach(btn => {
    btn.addEventListener('click', function() {
        if (!confirm('Удалить сферу "' + this.dataset.sphereName + '"? Задачи переедут в "Распределить".')) return;
        fetch('/api/sphere/' + this.dataset.sphereId, {method:'DELETE'}).then(() => location.reload());
    });
});

// Задачи добавляются без полной перезагрузки.
document.querySelectorAll('.addTaskBtn').forEach(btn => {
    btn.addEventListener('click', function() {
        const sphereEl = this.closest('.sphere');
        const input = sphereEl.querySelector('.taskInput');
        const title = input.value.trim();
        if (!title) { alert('Введите название задачи'); return; }
        const container = sphereEl.querySelector('.tasks-container');

        fetch('/api/task/quarter', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({title, sphere:this.dataset.sphere, quarter})
        })
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(data => {
            input.value = '';
            container.appendChild(buildTask(data.task));
            ensureEmptyState(container);
            saveQuarterOrder(container);
        })
        .catch(() => alert('Не удалось добавить задачу'));
    });
});
document.querySelectorAll('.taskInput').forEach(input => {
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') this.closest('.add-task-form').querySelector('.addTaskBtn').click();
    });
});

spheresContainer.addEventListener('click', function(e) {
    const card = e.target.closest('.task-item');
    if (!card) return;

    if (e.target.closest('.subtask-title')) {
        openSubtaskEditor(e.target.closest('.subtask'));
        return;
    }

    if (e.target.closest('.task-title')) {
        openQuarterEditor(card);
        return;
    }

    if (e.target.closest('.add-subtask-toggle')) {
        const form = card.querySelector('.subtask-add');
        const opening = !form.classList.contains('open');
        closeSubtaskForms(card);
        form.classList.toggle('open', opening);
        if (opening) setTimeout(() => form.querySelector('.subtask-input').focus(), 0);
        return;
    }

    if (e.target.closest('.add-subtask-btn')) {
        submitSubtask(card);
        return;
    }

    if (e.target.closest('.done-btn')) {
        const container = card.closest('.tasks-container');
        fetch('/api/task/' + card.dataset.taskId + '/done', {method:'POST'})
            .then(async r => {
                const data = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(data.message || 'Не удалось завершить задачу');
                return data;
            })
            .then(() => {
                const completed = buildCompletedFromCard(card);
                card.remove();
                ensureEmptyState(container);
                saveQuarterOrder(container);
                document.getElementById('completedTasks').prepend(completed);
                ensureCompletedEmptyState();
            })
            .catch(err => alert(err.message));
        return;
    }

    if (e.target.closest('.delete-btn')) {
        if (!confirm('Удалить задачу?')) return;
        const container = card.closest('.tasks-container');
        fetch('/api/task/' + card.dataset.taskId, {method:'DELETE'})
            .then(r => {
                if (!r.ok) throw new Error();
                card.remove();
                ensureEmptyState(container);
                saveQuarterOrder(container);
            })
            .catch(() => alert('Не удалось удалить задачу'));
        return;
    }

    if (e.target.closest('.subtask-delete')) {
        const row = e.target.closest('.subtask');
        fetch('/api/subtask/' + row.dataset.subtaskId, {method:'DELETE'})
            .then(r => {
                if (!r.ok) throw new Error();
                row.remove();
                saveSubtaskOrder(card);
            })
            .catch(() => alert('Не удалось удалить подзадачу'));
    }
});

spheresContainer.addEventListener('change', function(e) {
    if (!e.target.classList.contains('subtask-check')) return;
    const row = e.target.closest('.subtask');
    const card = e.target.closest('.task-item');
    const isDone = e.target.checked;

    fetch('/api/subtask/' + row.dataset.subtaskId, {
        method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({is_done:isDone})
    })
    .then(r => {
        if (!r.ok) throw new Error();
        row.classList.toggle('done', isDone);
        normalizeSubtasks(card);
    })
    .catch(() => {
        e.target.checked = !isDone;
        alert('Не удалось обновить подзадачу');
    });
});

spheresContainer.addEventListener('keydown', function(e) {
    if (!e.target.classList.contains('subtask-input')) return;
    if (e.key === 'Enter') {
        e.preventDefault();
        submitSubtask(e.target.closest('.task-item'));
    } else if (e.key === 'Escape') {
        e.target.value = '';
        e.target.closest('.subtask-add').classList.remove('open');
    }
});

document.addEventListener('click', function(e) {
    if (!e.target.closest('.task-item')) closeSubtaskForms();
});

// Drag & drop крупных задач внутри одной сферы.
spheresContainer.addEventListener('mousedown', function(e) {
    const handle = e.target.closest('.drag-handle');
    if (!handle) return;
    const card = handle.closest('.task-item');
    if (card) card.draggable = true;
});

// Drag & drop подзадач — только внутри той же задачи и той же группы статуса.
spheresContainer.addEventListener('mousedown', function(e) {
    const handle = e.target.closest('.subtask-drag');
    if (!handle) return;
    const row = handle.closest('.subtask');
    if (row) row.draggable = true;
});

spheresContainer.addEventListener('dragstart', function(e) {
    const subtask = e.target.closest('.subtask');
    if (subtask && subtask.draggable) {
        draggedSubtask = subtask;
        subtask.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        return;
    }

    const card = e.target.closest('.task-item');
    if (!card || !card.draggable) { e.preventDefault(); return; }
    draggedTask = card;
    card.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
});

spheresContainer.addEventListener('dragover', function(e) {
    if (draggedSubtask) {
        const target = e.target.closest('.subtask');
        if (!target || target === draggedSubtask) return;
        const sameCard = draggedSubtask.closest('.task-item') === target.closest('.task-item');
        const sameState = draggedSubtask.classList.contains('done') === target.classList.contains('done');
        if (!sameCard || !sameState) return;
        e.preventDefault();
        const rect = target.getBoundingClientRect();
        if (e.clientY < rect.top + rect.height / 2) target.before(draggedSubtask);
        else target.after(draggedSubtask);
        return;
    }

    if (!draggedTask) return;
    const target = e.target.closest('.task-item');
    if (!target || target === draggedTask) return;
    const sourceContainer = draggedTask.closest('.tasks-container');
    const targetContainer = target.closest('.tasks-container');
    if (sourceContainer !== targetContainer) return;
    e.preventDefault();
    const rect = target.getBoundingClientRect();
    if (e.clientY < rect.top + rect.height / 2) target.before(draggedTask);
    else target.after(draggedTask);
});

spheresContainer.addEventListener('dragend', function() {
    if (draggedSubtask) {
        const card = draggedSubtask.closest('.task-item');
        draggedSubtask.classList.remove('dragging');
        draggedSubtask.draggable = false;
        saveSubtaskOrder(card);
        draggedSubtask = null;
        return;
    }

    if (draggedTask) {
        const container = draggedTask.closest('.tasks-container');
        draggedTask.classList.remove('dragging');
        draggedTask.draggable = false;
        saveQuarterOrder(container);
        draggedTask = null;
    }
});

document.querySelectorAll('.task-item').forEach(card => normalizeSubtasks(card, false));
document.querySelectorAll('.tasks-container').forEach(ensureEmptyState);
ensureCompletedEmptyState();
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
            -webkit-tap-highlight-color: transparent;
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
            touch-action: manipulation;
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
            -webkit-appearance: none;
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
            touch-action: manipulation;
        }
        .add-task button:hover { background: #7a69a4; }
        
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
        }
        .task-item .task-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .task-item .task-info .task-duration {
            font-size: 11px;
            color: #b5a7cc;
            background: #ede5f5;
            padding: 1px 8px;
            border-radius: 10px;
        }
        .task-item .task-info .comment-badge {
            font-size: 11px;
            color: #8b7bb5;
            background: #ede5f5;
            padding: 1px 8px;
            border-radius: 10px;
            cursor: help;
        }
        .task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 4px 6px;
            border-radius: 6px;
            touch-action: manipulation;
        }
        .task-item .task-actions button:hover { color: #8b7bb5; background: #ede5f5; }
        
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
            padding: 4px 8px;
            border-radius: 6px;
            touch-action: manipulation;
        }
        .group-header .delete-group:hover { background: #ede5f5; color: #e74c3c; }
        
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
            -webkit-appearance: none;
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
            touch-action: manipulation;
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
            padding: 4px 6px;
            border-radius: 6px;
            touch-action: manipulation;
        }
        .group-task-item .task-actions button:hover { color: #8b7bb5; background: #ede5f5; }
        
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
            -webkit-appearance: none;
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
            touch-action: manipulation;
        }
        .add-group-task button:hover { background: #7a69a4; }
        
        .move-to-group-btn {
            background: none;
            border: none;
            color: #b5a7cc;
            cursor: pointer;
            font-size: 16px;
            padding: 4px 8px;
            touch-action: manipulation;
        }
        .move-to-group-btn:hover { color: #8b7bb5; }
        
        .group-task-list {
            margin-top: 6px;
        }
        .group-picker {
            display: none;
            position: fixed;
            z-index: 3000;
            background: #fcfaff;
            border: 1.5px solid #ede5f5;
            border-radius: 10px;
            padding: 10px;
            box-shadow: 0 8px 28px rgba(74,63,94,.18);
            gap: 8px;
            align-items: center;
        }
        .group-picker.open { display: flex; }
        .group-picker select {
            min-width: 170px;
            max-width: 240px;
            padding: 8px 10px;
            border: 1.5px solid #ede5f5;
            border-radius: 8px;
            color: #4a3f5e;
            background: white;
            outline: none;
        }
        .group-picker button {
            border: none; border-radius: 8px; padding: 8px 10px; cursor: pointer;
            background: #8b7bb5; color: white;
        }
        .group-picker .picker-cancel { background: #ede5f5; color: #4a3f5e; }
        
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
                        {% if task.duration %}
                        <span class="task-duration">⏱️ {{ task.duration }}</span>
                        {% endif %}
                        {% if task.comment and task.comment != '' %}
                        <span class="comment-badge" title="{{ task.comment }}">💬</span>
                        {% endif %}
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
                        {% if task.duration %}
                        <span class="task-duration">⏱️ {{ task.duration }}</span>
                        {% endif %}
                        {% if task.comment and task.comment != '' %}
                        <span class="comment-badge" title="{{ task.comment }}">💬</span>
                        {% endif %}
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

<div class="group-picker" id="groupPicker">
    <select id="groupPickerSelect"></select>
    <button type="button" id="groupPickerConfirm">Переместить</button>
    <button type="button" class="picker-cancel" id="groupPickerCancel">✕</button>
</div>

<script>
    let pendingMoveTaskId = null;

    function refreshLaterLayout() {
        const scrollY = window.scrollY;
        return fetch('/later', { headers: { 'X-Requested-With': 'fetch' } })
            .then(res => res.text())
            .then(html => {
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const fresh = doc.querySelector('.later-layout');
                const current = document.querySelector('.later-layout');
                if (fresh && current) current.replaceWith(fresh);
                window.scrollTo(0, scrollY);
            });
    }

    function closeGroupPicker() {
        const picker = document.getElementById('groupPicker');
        picker.classList.remove('open');
        pendingMoveTaskId = null;
    }

    function openGroupPicker(button) {
        pendingMoveTaskId = button.dataset.taskId;
        const picker = document.getElementById('groupPicker');
        const select = document.getElementById('groupPickerSelect');
        select.innerHTML = '<option>Загрузка…</option>';
        picker.classList.add('open');

        const rect = button.getBoundingClientRect();
        picker.style.top = Math.min(window.innerHeight - 80, rect.bottom + 6) + 'px';
        picker.style.left = Math.max(10, Math.min(window.innerWidth - 330, rect.left - 170)) + 'px';

        fetch('/api/later/groups')
            .then(res => res.json())
            .then(groups => {
                select.innerHTML = '';
                if (!Array.isArray(groups) || groups.length === 0) {
                    const option = document.createElement('option');
                    option.value = '';
                    option.textContent = 'Сначала создайте группу';
                    select.appendChild(option);
                    document.getElementById('groupPickerConfirm').disabled = true;
                    return;
                }
                document.getElementById('groupPickerConfirm').disabled = false;
                groups.forEach(group => {
                    const option = document.createElement('option');
                    option.value = group.name;
                    option.textContent = group.name;
                    select.appendChild(option);
                });
            });
    }

    document.getElementById('groupPickerCancel').addEventListener('click', closeGroupPicker);
    document.getElementById('groupPickerConfirm').addEventListener('click', function() {
        const group = document.getElementById('groupPickerSelect').value;
        if (!pendingMoveTaskId || !group) return;
        fetch('/api/task/' + pendingMoveTaskId + '/move_to_later_group', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group })
        }).then(res => {
            if (!res.ok) throw new Error('Не удалось переместить задачу');
            closeGroupPicker();
            return refreshLaterLayout();
        }).catch(err => alert(err.message));
    });

    document.addEventListener('click', function(e) {
        const addLaterBtn = e.target.closest('#addLaterBtn');
        if (addLaterBtn) {
            const input = document.getElementById('laterTaskInput');
            const title = input.value.trim();
            if (!title) { alert('Введите название задачи'); return; }
            fetch('/api/task/later', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title })
            }).then(res => {
                if (!res.ok) throw new Error('Не удалось добавить задачу');
                input.value = '';
                return refreshLaterLayout();
            }).catch(err => alert(err.message));
            return;
        }

        const addGroupBtn = e.target.closest('#addGroupBtn');
        if (addGroupBtn) {
            const input = document.getElementById('newGroupInput');
            const name = input.value.trim();
            if (!name) { alert('Введите название группы'); return; }
            fetch('/api/later/group', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            }).then(res => {
                if (!res.ok) throw new Error('Не удалось создать группу');
                input.value = '';
                return refreshLaterLayout();
            }).catch(err => alert(err.message));
            return;
        }

        const moveBtn = e.target.closest('.move-to-group-btn');
        if (moveBtn) { openGroupPicker(moveBtn); return; }

        const addGroupTaskBtn = e.target.closest('.add-group-task-btn');
        if (addGroupTaskBtn) {
            const group = addGroupTaskBtn.dataset.group;
            const section = addGroupTaskBtn.closest('.group-section');
            const input = section.querySelector('.group-task-input');
            const title = input.value.trim();
            if (!title) { alert('Введите название задачи'); return; }
            fetch('/api/task/later/group', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, group })
            }).then(res => {
                if (!res.ok) throw new Error('Не удалось добавить задачу');
                input.value = '';
                return refreshLaterLayout();
            }).catch(err => alert(err.message));
            return;
        }

        const deleteGroupBtn = e.target.closest('.delete-group');
        if (deleteGroupBtn) {
            const groupId = deleteGroupBtn.dataset.groupId;
            const groupName = deleteGroupBtn.dataset.groupName;
            if (confirm('Удалить группу "' + groupName + '"? Задачи вернутся в общий список.')) {
                fetch('/api/later/group/' + groupId, { method: 'DELETE' })
                    .then(res => { if (!res.ok) throw new Error('Не удалось удалить группу'); return refreshLaterLayout(); })
                    .catch(err => alert(err.message));
            }
            return;
        }

        const doneBtn = e.target.closest('.done-btn');
        if (doneBtn && doneBtn.closest('.later-layout')) {
            fetch('/api/task/' + doneBtn.dataset.taskId + '/done', { method: 'POST' })
                .then(res => { if (!res.ok) throw new Error('Не удалось выполнить задачу'); return refreshLaterLayout(); })
                .catch(err => alert(err.message));
            return;
        }

        const deleteBtn = e.target.closest('.delete-btn');
        if (deleteBtn && deleteBtn.closest('.later-layout')) {
            if (confirm('Удалить задачу?')) {
                fetch('/api/task/' + deleteBtn.dataset.taskId, { method: 'DELETE' })
                    .then(res => { if (!res.ok) throw new Error('Не удалось удалить задачу'); return refreshLaterLayout(); })
                    .catch(err => alert(err.message));
            }
            return;
        }

        if (!e.target.closest('#groupPicker')) closeGroupPicker();
    });

    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Enter') return;
        if (e.target.id === 'laterTaskInput') {
            e.preventDefault(); document.getElementById('addLaterBtn').click();
        } else if (e.target.id === 'newGroupInput') {
            e.preventDefault(); document.getElementById('addGroupBtn').click();
        } else if (e.target.classList.contains('group-task-input')) {
            e.preventDefault(); e.target.closest('.group-section').querySelector('.add-group-task-btn').click();
        }
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
            -webkit-tap-highlight-color: transparent;
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
            touch-action: manipulation;
        }
        .header .btn-back:hover { background: #e0d5ec; }
        
        .date-group {
            margin-bottom: 24px;
        }
        .date-group .date-title {
            font-size: 18px;
            font-weight: 600;
            color: #4a3f5e;
            margin-bottom: 10px;
            padding-bottom: 6px;
            border-bottom: 2px solid #ede5f5;
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
        .task-item .task-info .comment-badge {
            font-size: 11px;
            color: #8b7bb5;
            background: #ede5f5;
            padding: 1px 8px;
            border-radius: 10px;
            cursor: help;
        }
        .task-item .task-actions button {
            background: none;
            border: none;
            color: #c5b8d8;
            cursor: pointer;
            font-size: 14px;
            padding: 4px 6px;
            border-radius: 6px;
            touch-action: manipulation;
        }
        .task-item .task-actions button:hover { color: #8b7bb5; background: #ede5f5; }
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
    
    <div id="doneContainer">
        <div class="empty-list">Загрузка…</div>
    </div>
    <div class="info-note">⏳ Задачи хранятся 36 часов, затем удаляются автоматически</div>
</div>

<script>
    function getCompletedDate(task) {
        if (task.completed_at_epoch !== null && task.completed_at_epoch !== undefined) {
            return new Date(Number(task.completed_at_epoch) * 1000);
        }
        // Старые записи без epoch трактуем как локальное время устройства,
        // чтобы не добавлять часовой пояс второй раз.
        return task.completed_at ? new Date(task.completed_at) : null;
    }

    function loadDoneTasks() {
        fetch('/api/tasks/done')
            .then(res => res.json())
            .then(tasks => {
                const container = document.getElementById('doneContainer');
                if (tasks.length === 0) {
                    container.innerHTML = '<div class="empty-list">📭 Здесь пока пусто. Выполненные задачи появятся здесь на 36 часов.</div>';
                    return;
                }
                
                const tasksByDate = {};
                tasks.forEach(task => {
                    if (task.completed_at) {
                        const completed = getCompletedDate(task);
                        const dateKey = [completed.getFullYear(), String(completed.getMonth() + 1).padStart(2, '0'), String(completed.getDate()).padStart(2, '0')].join('-');
                        if (!tasksByDate[dateKey]) tasksByDate[dateKey] = [];
                        tasksByDate[dateKey].push(task);
                    }
                });
                
                const sortedDates = Object.keys(tasksByDate).sort((a, b) => b.localeCompare(a));
                
                container.innerHTML = '';
                sortedDates.forEach(dateKey => {
                    const dateGroup = document.createElement('div');
                    dateGroup.className = 'date-group';
                    const parts = dateKey.split('-').map(Number);
                    const dateObj = new Date(parts[0], parts[1] - 1, parts[2]);
                    const months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
                    const weekdays = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
                    const day = dateObj.getDate();
                    const month = months[dateObj.getMonth()];
                    const weekday = weekdays[dateObj.getDay()];
                    dateGroup.innerHTML = '<div class="date-title">' + day + ' ' + month + ', ' + weekday + '</div>';
                    
                    tasksByDate[dateKey].forEach(task => {
                        const item = document.createElement('div');
                        item.className = 'task-item';
                        item.dataset.taskId = task.id;
                        const completedDate = getCompletedDate(task);
                        const completedTime = completedDate ? completedDate.toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'}) : 'только что';
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
                        dateGroup.appendChild(item);
                    });
                    
                    container.appendChild(dateGroup);
                });
                
                document.querySelectorAll('.restore-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const taskId = this.dataset.taskId;
                        fetch('/api/task/' + taskId + '/restore', { method: 'POST' })
                            .then(() => loadDoneTasks());
                    });
                });
                
                document.querySelectorAll('.delete-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const taskId = this.dataset.taskId;
                        if (confirm('Удалить задачу навсегда?')) {
                            fetch('/api/task/' + taskId, { method: 'DELETE' })
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)