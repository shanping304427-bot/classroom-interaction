from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import sqlite3
import json
import uuid
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

DATABASE = 'classroom.db'

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS classrooms (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        teacher_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        checkin_open BOOLEAN DEFAULT 1,
        checkin_code TEXT,
        expires_at TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        classroom_id TEXT,
        name TEXT NOT NULL,
        student_id TEXT NOT NULL,
        class_name TEXT,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS checkins (
        id TEXT PRIMARY KEY,
        student_id TEXT,
        classroom_id TEXT,
        check_type TEXT CHECK(check_type IN ('in', 'out')),
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS questions (
        id TEXT PRIMARY KEY,
        student_id TEXT,
        classroom_id TEXT,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        answered BOOLEAN DEFAULT 0,
        FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS votes (
        id TEXT PRIMARY KEY,
        classroom_id TEXT,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS vote_results (
        id TEXT PRIMARY KEY,
        vote_id TEXT,
        student_id TEXT,
        option_index INTEGER,
        FOREIGN KEY (vote_id) REFERENCES votes(id),
        UNIQUE(vote_id, student_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS danmaku (
        id TEXT PRIMARY KEY,
        classroom_id TEXT,
        student_id TEXT,
        student_name TEXT,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
    )''')
    
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('teacher.html')

@app.route('/classroom/<classroom_id>')
def student_join(classroom_id):
    return render_template('student.html', classroom_id=classroom_id)

@app.route('/api/classroom', methods=['POST'])
def create_classroom():
    data = request.json
    classroom_id = str(uuid.uuid4())[:8]
    checkin_code = str(uuid.uuid4())[:6].upper()
    expires_at = datetime.now() + timedelta(hours=1)
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''INSERT INTO classrooms (id, name, teacher_name, checkin_code, expires_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (classroom_id, data['name'], data.get('teacher_name', ''), checkin_code, expires_at))
    conn.commit()
    conn.close()
    
    return jsonify({
        'id': classroom_id,
        'checkin_code': checkin_code,
        'link': f'/classroom/{classroom_id}',
        'expires_at': expires_at.isoformat()
    })

@app.route('/api/classroom/<classroom_id>')
def get_classroom(classroom_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('SELECT * FROM classrooms WHERE id = ?', (classroom_id,))
    room = c.fetchone()
    conn.close()
    
    if not room:
        return jsonify({'error': 'Classroom not found'}), 404
    
    is_expired = datetime.now() > datetime.fromisoformat(room[6]) if room[6] else False
    
    return jsonify({
        'id': room[0],
        'name': room[1],
        'teacher_name': room[2],
        'checkin_open': room[4] and not is_expired,
        'checkin_code': room[5] if (room[4] and not is_expired) else None,
        'is_expired': is_expired
    })

@app.route('/api/classroom/<classroom_id>/toggle', methods=['POST'])
def toggle_checkin(classroom_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('UPDATE classrooms SET checkin_open = NOT checkin_open WHERE id = ?', (classroom_id,))
    conn.commit()
    c.execute('SELECT checkin_open FROM classrooms WHERE id = ?', (classroom_id,))
    status = c.fetchone()[0]
    conn.close()
    
    socketio.emit('checkin_status', {'open': bool(status)}, room=classroom_id)
    return jsonify({'checkin_open': bool(status)})

@app.route('/api/classroom/<classroom_id>/join', methods=['POST'])
def join_classroom(classroom_id):
    data = request.json
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    c.execute('SELECT checkin_open, expires_at FROM classrooms WHERE id = ?', (classroom_id,))
    room = c.fetchone()
    if not room:
        conn.close()
        return jsonify({'error': 'Classroom not found'}), 404
    
    is_expired = datetime.now() > datetime.fromisoformat(room[1]) if room[1] else False
    if not room[0] or is_expired:
        conn.close()
        return jsonify({'error': '签到已关闭或链接已过期'}), 403
    
    student_id = str(uuid.uuid4())[:8]
    c.execute('''INSERT INTO students (id, classroom_id, name, student_id, class_name)
                 VALUES (?, ?, ?, ?, ?)''',
              (student_id, classroom_id, data['name'], data['student_id'], data.get('class_name', '')))
    conn.commit()
    conn.close()
    
    return jsonify({'student_id': student_id, 'name': data['name']})

@app.route('/api/classroom/<classroom_id>/checkin', methods=['POST'])
def checkin(classroom_id):
    data = request.json
    student_id = data['student_id']
    check_type = data['type']
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    checkin_id = str(uuid.uuid4())[:8]
    c.execute('''INSERT INTO checkins (id, student_id, classroom_id, check_type)
                 VALUES (?, ?, ?, ?)''',
              (checkin_id, student_id, classroom_id, check_type))
    conn.commit()
    
    c.execute('SELECT name FROM students WHERE id = ?', (student_id,))
    student_name = c.fetchone()[0]
    conn.close()
    
    socketio.emit('new_checkin', {
        'student_name': student_name,
        'student_id': student_id,
        'type': check_type,
        'time': datetime.now().isoformat()
    }, room=classroom_id)
    
    return jsonify({'success': True})

@app.route('/api/classroom/<classroom_id>/students')
def get_students(classroom_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    c.execute('''SELECT s.id, s.name, s.student_id, s.class_name,
                 MAX(CASE WHEN c.check_type = 'in' THEN c.timestamp END) as checkin_time,
                 MAX(CASE WHEN c.check_type = 'out' THEN c.timestamp END) as checkout_time
                 FROM students s
                 LEFT JOIN checkins c ON s.id = c.student_id
                 WHERE s.classroom_id = ?
                 GROUP BY s.id''', (classroom_id,))
    
    students = []
    for row in c.fetchall():
        students.append({
            'id': row[0],
            'name': row[1],
            'student_id': row[2],
            'class_name': row[3],
            'checkin_time': row[4],
            'checkout_time': row[5]
        })
    
    conn.close()
    return jsonify(students)

@app.route('/api/classroom/<classroom_id>/question', methods=['POST'])
def post_question(classroom_id):
    data = request.json
    question_id = str(uuid.uuid4())[:8]
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''INSERT INTO questions (id, classroom_id, student_id, content)
                 VALUES (?, ?, ?, ?)''',
              (question_id, classroom_id, data['student_id'], data['content']))
    conn.commit()
    
    c.execute('SELECT name FROM students WHERE id = ?', (data['student_id'],))
    student_name = c.fetchone()[0]
    conn.close()
    
    socketio.emit('new_question', {
        'id': question_id,
        'student_name': student_name,
        'content': data['content'],
        'time': datetime.now().isoformat()
    }, room=classroom_id)
    
    return jsonify({'success': True})

@app.route('/api/classroom/<classroom_id>/questions')
def get_questions(classroom_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''SELECT q.id, q.content, q.created_at, q.answered, s.name
                 FROM questions q
                 JOIN students s ON q.student_id = s.id
                 WHERE q.classroom_id = ?
                 ORDER BY q.created_at DESC''', (classroom_id,))
    
    questions = []
    for row in c.fetchall():
        questions.append({
            'id': row[0],
            'content': row[1],
            'created_at': row[2],
            'answered': row[3],
            'student_name': row[4]
        })
    
    conn.close()
    return jsonify(questions)

@app.route('/api/classroom/<classroom_id>/vote', methods=['POST'])
def create_vote(classroom_id):
    data = request.json
    vote_id = str(uuid.uuid4())[:8]
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('UPDATE votes SET active = 0 WHERE classroom_id = ?', (classroom_id,))
    c.execute('''INSERT INTO votes (id, classroom_id, question, options, active)
                 VALUES (?, ?, ?, ?, 1)''',
              (vote_id, classroom_id, data['question'], json.dumps(data['options'])))
    conn.commit()
    conn.close()
    
    socketio.emit('new_vote', {
        'id': vote_id,

