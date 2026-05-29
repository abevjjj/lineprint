import socket
import inspect
import cleaners
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
from functools import wraps
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'termux_pro_secret')
ACCESS_PASSWORD = '123'
app.permanent_session_lifetime = timedelta(days=365)

# 数据库配置
os.makedirs(app.instance_path, exist_ok=True)
db_path = os.path.join(app.instance_path, 'printer_v4.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 数据库模型 ---
class Printer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    ip = db.Column(db.String(20))

class Title(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(100), unique=True)

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)

class ScheduledJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    printer_ip = db.Column(db.String(20))
    title = db.Column(db.String(100))
    content = db.Column(db.Text)
    job_type = db.Column(db.String(20))   # once / daily / weekly / monthly
    run_at = db.Column(db.String(10))     # HH:MM
    run_date = db.Column(db.String(20))   # YYYY-MM-DD（一次性用）
    weekday = db.Column(db.String(10))    # 0-6（周循环用，0=周一）
    monthday = db.Column(db.Integer)      # 1-31（月循环用）
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.String(30), default=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

with app.app_context():
    db.create_all()
    if not Title.query.first():
        db.session.add(Title(content="备餐明细"))
        db.session.commit()

# --- 调度器 ---
scheduler = BackgroundScheduler(timezone='Asia/Shanghai')

def execute_job(job_id):
    with app.app_context():
        job = ScheduledJob.query.get(job_id)
        if not job or not job.enabled:
            return
        lines = [l.strip() for l in job.content.splitlines() if l.strip()]
        for line in lines:
            send_raw_print(job.printer_ip, job.title, line)
        if job.job_type == 'once':
            job.enabled = False
            db.session.commit()

def schedule_job(job):
    jid = f'job_{job.id}'
    if scheduler.get_job(jid):
        scheduler.remove_job(jid)
    if not job.enabled:
        return
    try:
        hour, minute = (job.run_at or '08:00').split(':')
        if job.job_type == 'once':
            run_dt = datetime.strptime(f"{job.run_date} {job.run_at}", '%Y-%m-%d %H:%M')
            if run_dt > datetime.now():
                scheduler.add_job(execute_job, DateTrigger(run_date=run_dt),
                                  args=[job.id], id=jid, replace_existing=True)
        elif job.job_type == 'daily':
            scheduler.add_job(execute_job, CronTrigger(hour=hour, minute=minute),
                              args=[job.id], id=jid, replace_existing=True)
        elif job.job_type == 'weekly':
            scheduler.add_job(execute_job, CronTrigger(day_of_week=job.weekday, hour=hour, minute=minute),
                              args=[job.id], id=jid, replace_existing=True)
        elif job.job_type == 'monthly':
            scheduler.add_job(execute_job, CronTrigger(day=job.monthday, hour=hour, minute=minute),
                              args=[job.id], id=jid, replace_existing=True)
    except Exception as e:
        print(f'[Scheduler] 注册任务 job_{job.id} 失败: {e}')

def load_all_jobs():
    with app.app_context():
        for job in ScheduledJob.query.filter_by(enabled=True).all():
            schedule_job(job)

scheduler.start()
threading.Timer(1.0, load_all_jobs).start()

# --- 清洗方案 ---
def get_cleaning_schemes():
    schemes = {}
    for name, func in inspect.getmembers(cleaners, inspect.isfunction):
        display_name = func.__doc__.strip() if func.__doc__ else name
        schemes[name] = {"name": display_name, "func": func}
    return schemes

# --- ESC/POS 打印 ---
def send_raw_print(ip, title, content_line):
    ESC, GS = b'\x1b', b'\x1d'
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((ip, 9100))
            s.sendall(ESC + b'@')
            s.sendall(ESC + b'a\x01' + GS + b'!\x01')
            s.sendall(title.encode('gbk') + b'\n')
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            s.sendall(ESC + b'a\x00' + GS + b'!\x01')
            s.sendall(time_str.encode('gbk') + b'\n')
            s.sendall(GS + b'!\x11')
            s.sendall(content_line.encode('gbk') + b'\n')
            s.sendall(ESC + b'd\x05' + GS + b'V\x00')
        return True
    except:
        return False

# --- 鉴权装饰器 ---
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def schedule_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('schedule_authed'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# --- Config 工具 ---
def get_config(key):
    c = Config.query.filter_by(key=key).first()
    return c.value if c else None

def set_config(key, value):
    c = Config.query.filter_by(key=key).first()
    if c:
        c.value = value
    else:
        db.session.add(Config(key=key, value=value))
    db.session.commit()

# ===== 主路由 =====

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST' and request.form.get('password') == ACCESS_PASSWORD:
        session.permanent = True
        session['logged_in'] = True
        return redirect(url_for('index'))
    return '''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
  <title>登录</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#f4f4f9;min-height:100vh;display:flex;align-items:center;justify-content:center}
    .box{background:white;padding:32px 24px;border-radius:16px;box-shadow:0 4px 16px rgba(0,0,0,0.1);width:90%;max-width:360px}
    h2{text-align:center;margin-bottom:24px;color:#333;font-size:20px}
    input{font-size:18px;padding:14px;width:100%;border:1px solid #ddd;border-radius:10px;outline:none}
    button{font-size:18px;padding:14px;width:100%;margin-top:14px;background:#007bff;color:white;border:none;border-radius:10px;font-weight:bold;cursor:pointer}
  </style>
</head>
<body>
  <div class="box">
    <h2>🖨️ 打印终端</h2>
    <form method="post">
      <input type="password" name="password" placeholder="请输入密码" autofocus>
      <button type="submit">登 录</button>
    </form>
  </div>
</body>
</html>'''

@app.route('/')
@login_required
def index():
    printers = Printer.query.all()
    titles = Title.query.all()
    schemes = get_cleaning_schemes()
    return render_template('index.html', printers=printers, titles=titles, schemes=schemes)

@app.route('/api/clean', methods=['POST'])
@login_required
def api_clean():
    data = request.json
    scheme_name = data.get('scheme_id')
    raw_text = data.get('raw_text', '')
    schemes = get_cleaning_schemes()
    if scheme_name in schemes:
        cleaned_list = schemes[scheme_name]['func'](raw_text)
        return jsonify({'cleaned': "\n".join(cleaned_list)})
    return jsonify({'cleaned': '方案不存在'}), 404

@app.route('/api/print', methods=['POST'])
@login_required
def api_print():
    data = request.json
    lines = [l.strip() for l in data['content'].splitlines() if l.strip()]
    for line in lines:
        send_raw_print(data['ip'], data['title'], line)
    return jsonify({'status': 'ok'})

@app.route('/api/manage/<target>', methods=['POST', 'DELETE'])
@login_required
def manage(target):
    if target == 'printer':
        if request.method == 'POST':
            db.session.add(Printer(name=request.json['name'], ip=request.json['ip']))
        else:
            Printer.query.filter_by(id=request.args.get('id')).delete()
    else:
        if request.method == 'POST':
            db.session.add(Title(content=request.json['content']))
        else:
            Title.query.filter_by(id=request.args.get('id')).delete()
    db.session.commit()
    return jsonify({'status': 'ok'})

# ===== 定时功能 API =====

@app.route('/api/schedule/auth', methods=['POST'])
@login_required
def schedule_auth():
    data = request.json
    input_pwd = data.get('password', '')
    stored_pwd = get_config('schedule_password')
    if stored_pwd is None:
        if len(input_pwd) < 4:
            return jsonify({'error': '密码至少4位'}), 400
        set_config('schedule_password', input_pwd)
        session['schedule_authed'] = True
        return jsonify({'status': 'created', 'msg': '专属密码已设置，请牢记'})
    else:
        if input_pwd == stored_pwd:
            session['schedule_authed'] = True
            return jsonify({'status': 'ok'})
        return jsonify({'error': '密码错误'}), 403

@app.route('/api/schedule/status', methods=['GET'])
@login_required
def schedule_status():
    stored_pwd = get_config('schedule_password')
    return jsonify({
        'has_password': stored_pwd is not None,
        'authed': bool(session.get('schedule_authed'))
    })

@app.route('/api/schedule/jobs', methods=['GET'])
@login_required
@schedule_auth_required
def get_jobs():
    jobs = ScheduledJob.query.order_by(ScheduledJob.id.desc()).all()
    return jsonify([{
        'id': j.id, 'name': j.name, 'printer_ip': j.printer_ip,
        'title': j.title, 'content': j.content, 'job_type': j.job_type,
        'run_at': j.run_at, 'run_date': j.run_date,
        'weekday': j.weekday, 'monthday': j.monthday,
        'enabled': j.enabled, 'created_at': j.created_at
    } for j in jobs])

@app.route('/api/schedule/jobs', methods=['POST'])
@login_required
@schedule_auth_required
def create_job():
    d = request.json
    job = ScheduledJob(
        name=d.get('name', '未命名任务'),
        printer_ip=d['printer_ip'], title=d['title'], content=d['content'],
        job_type=d['job_type'], run_at=d.get('run_at', '08:00'),
        run_date=d.get('run_date'), weekday=d.get('weekday'),
        monthday=d.get('monthday'), enabled=True
    )
    db.session.add(job)
    db.session.commit()
    schedule_job(job)
    return jsonify({'status': 'ok', 'id': job.id})

@app.route('/api/schedule/jobs/<int:job_id>', methods=['PUT'])
@login_required
@schedule_auth_required
def update_job(job_id):
    job = ScheduledJob.query.get_or_404(job_id)
    d = request.json
    for field in ['name','printer_ip','title','content','job_type',
                  'run_at','run_date','weekday','monthday','enabled']:
        if field in d:
            setattr(job, field, d[field])
    db.session.commit()
    schedule_job(job)
    return jsonify({'status': 'ok'})

@app.route('/api/schedule/jobs/<int:job_id>', methods=['DELETE'])
@login_required
@schedule_auth_required
def delete_job(job_id):
    job = ScheduledJob.query.get_or_404(job_id)
    jid = f'job_{job.id}'
    if scheduler.get_job(jid):
        scheduler.remove_job(jid)
    db.session.delete(job)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/schedule/jobs/<int:job_id>/toggle', methods=['POST'])
@login_required
@schedule_auth_required
def toggle_job(job_id):
    job = ScheduledJob.query.get_or_404(job_id)
    job.enabled = not job.enabled
    db.session.commit()
    schedule_job(job)
    return jsonify({'status': 'ok', 'enabled': job.enabled})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5006)
