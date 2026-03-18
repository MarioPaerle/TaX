import os
import select
import threading
import time
import uuid
import stat as stat_module

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_sock import Sock
import paramiko

app = Flask(__name__)
app.secret_key = 'ssh-manager-local-key-change-me'
sock = Sock(app)

# Global SSH sessions store (single-user local app)
ssh_sessions = {}  # sid -> {client, sftp, host, port, username, home}


def get_sess():
    sid = session.get('sid')
    if sid and sid in ssh_sessions:
        return ssh_sessions[sid]
    return None


# ────────────────────────── PAGES ──────────────────────────

@app.route('/')
def index():
    return redirect(url_for('files') if get_sess() else url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        host = request.form.get('host', '').strip()
        port = int(request.form.get('port') or 22)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        keyfile = request.form.get('keyfile', '').strip()

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = dict(hostname=host, port=port, username=username, timeout=10)
            if keyfile:
                connect_kwargs['key_filename'] = keyfile
            elif password:
                connect_kwargs['password'] = password

            client.connect(**connect_kwargs)
            sftp = client.open_sftp()

            _, stdout, _ = client.exec_command('echo $HOME')
            home = stdout.read().decode().strip() or f'/home/{username}'

            sid = str(uuid.uuid4())
            ssh_sessions[sid] = dict(
                client=client, sftp=sftp,
                host=host, port=port, username=username, home=home
            )
            session['sid'] = sid
            return redirect(url_for('files'))
        except Exception as e:
            error = str(e)

    return render_template('login.html', error=error)


@app.route('/disconnect')
def disconnect():
    sid = session.get('sid')
    if sid and sid in ssh_sessions:
        try:
            ssh_sessions[sid]['sftp'].close()
            ssh_sessions[sid]['client'].close()
        except Exception:
            pass
        del ssh_sessions[sid]
    session.clear()
    return redirect(url_for('login'))


@app.route('/files')
def files():
    d = get_sess()
    if not d:
        return redirect(url_for('login'))
    return render_template('files.html', host=d['host'], username=d['username'], home=d['home'])


@app.route('/slurm')
def slurm():
    d = get_sess()
    if not d:
        return redirect(url_for('login'))
    return render_template('slurm.html', host=d['host'], username=d['username'])


@app.route('/terminal')
def terminal():
    d = get_sess()
    if not d:
        return redirect(url_for('login'))
    return render_template('terminal.html', host=d['host'], username=d['username'])


# ────────────────────────── FILE API ──────────────────────────

@app.route('/api/files/list')
def api_list():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401

    path = request.args.get('path', d['home'])
    show_hidden = request.args.get('hidden', 'false') == 'true'

    try:
        items = []
        for attr in d['sftp'].listdir_attr(path):
            if not show_hidden and attr.filename.startswith('.'):
                continue
            is_dir = stat_module.S_ISDIR(attr.st_mode)
            items.append(dict(
                name=attr.filename,
                is_dir=is_dir,
                size=0 if is_dir else attr.st_size,
                path=path.rstrip('/') + '/' + attr.filename,
            ))
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        parent = '/'.join(path.rstrip('/').split('/')[:-1]) or '/'
        return jsonify({'path': path, 'items': items, 'parent': parent})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/read')
def api_read():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    path = request.args.get('path')
    try:
        with d['sftp'].open(path, 'r') as f:
            content = f.read().decode('utf-8', errors='replace')
        return jsonify({'content': content})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/write', methods=['POST'])
def api_write():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    body = request.json
    try:
        with d['sftp'].open(body['path'], 'w') as f:
            f.write(body.get('content', '').encode('utf-8'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/touch', methods=['POST'])
def api_touch():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    try:
        with d['sftp'].open(request.json['path'], 'w') as f:
            f.write(b'')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/mkdir', methods=['POST'])
def api_mkdir():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    try:
        d['sftp'].mkdir(request.json['path'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/delete', methods=['POST'])
def api_delete():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    path = request.json['path']
    try:
        _, stdout, _ = d['client'].exec_command(f'rm -rf -- "{path}"')
        stdout.channel.recv_exit_status()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/rename', methods=['POST'])
def api_rename():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    body = request.json
    try:
        d['sftp'].rename(body['old_path'], body['new_path'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ────────────────────────── RUN API ──────────────────────────

@app.route('/api/run', methods=['POST'])
def api_run():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    body = request.json
    cmd = body.get('command', '')
    cwd = body.get('cwd', '')
    if cwd:
        cmd = f'cd "{cwd}" && {cmd}'
    try:
        _, stdout, stderr = d['client'].exec_command(cmd, timeout=30)
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        rc = stdout.channel.recv_exit_status()
        return jsonify({'stdout': out, 'stderr': err, 'rc': rc})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ────────────────────────── SLURM API ──────────────────────────

@app.route('/api/slurm/queue')
def api_slurm_queue():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    try:
        cmd = f'squeue -u {d["username"]} -o "%i|%j|%T|%M|%l|%D|%P|%R" --noheader 2>&1'
        _, stdout, _ = d['client'].exec_command(cmd)
        output = stdout.read().decode()
        jobs = []
        for line in output.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('|')
            if len(parts) >= 8:
                jobs.append(dict(
                    id=parts[0].strip(), name=parts[1].strip(),
                    state=parts[2].strip(), time=parts[3].strip(),
                    time_limit=parts[4].strip(), nodes=parts[5].strip(),
                    partition=parts[6].strip(), reason=parts[7].strip(),
                ))
        return jsonify({'jobs': jobs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/slurm/submit', methods=['POST'])
def api_slurm_submit():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    script = request.json.get('script_path', '').strip()
    try:
        _, stdout, stderr = d['client'].exec_command(f'sbatch "{script}"')
        return jsonify({'output': stdout.read().decode().strip(), 'error': stderr.read().decode().strip()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/slurm/cancel', methods=['POST'])
def api_slurm_cancel():
    d = get_sess()
    if not d:
        return jsonify({'error': 'Not connected'}), 401
    job_id = request.json.get('job_id')
    try:
        _, stdout, _ = d['client'].exec_command(f'scancel {job_id}')
        stdout.channel.recv_exit_status()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ────────────────────────── WEBSOCKET TERMINAL ──────────────────────────

@sock.route('/ws/terminal')
def ws_terminal(ws):
    d = get_sess()
    if not d:
        return

    channel = d['client'].invoke_shell(term='xterm-256color', width=220, height=50)
    stop = threading.Event()

    def read_from_ssh():
        while not stop.is_set():
            try:
                r, _, _ = select.select([channel], [], [], 0.1)
                if r:
                    data = channel.recv(4096)
                    if not data:
                        stop.set()
                        break
                    ws.send(data.decode('utf-8', errors='replace'))
            except Exception:
                stop.set()
                break

    reader = threading.Thread(target=read_from_ssh, daemon=True)
    reader.start()

    try:
        while not stop.is_set():
            msg = ws.receive()
            if msg is None:
                break
            channel.send(msg.encode('utf-8') if isinstance(msg, str) else msg)
    except Exception:
        pass
    finally:
        stop.set()
        try:
            channel.close()
        except Exception:
            pass


# ────────────────────────── MAIN ──────────────────────────

if __name__ == '__main__':
    def _open_browser():
        time.sleep(1.2)
        import webbrowser
        webbrowser.open('http://localhost:5000')

    threading.Thread(target=_open_browser, daemon=True).start()
    print('\n  SSH Manager → http://localhost:5000\n')
    app.run(debug=False, port=5000, threaded=True)
