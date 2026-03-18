"""
SSH Manager — single file, self-installing, no folders needed.
Run: python app.py   (installs dependencies automatically on first run)
"""

# ── Auto-installer ────────────────────────────────────────────────────────────
def _bootstrap():
    import importlib, subprocess, sys, os, urllib.request, tempfile

    REQUIRED = ['flask', 'flask_sock', 'paramiko']
    missing  = [pkg for pkg in REQUIRED if importlib.util.find_spec(pkg) is None]
    if not missing:
        return  # all good, nothing to do

    print(f'\n[SSH Manager] First run — installing dependencies: {", ".join(missing)}')

    # Try pip directly first
    try:
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '--user',
             'flask', 'flask-sock', 'paramiko'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print('[SSH Manager] Install complete.\n')
        return
    except Exception:
        pass

    # pip not available → bootstrap it via get-pip.py then retry
    print('[SSH Manager] pip not found, downloading get-pip.py …')
    try:
        tmp = tempfile.mktemp(suffix='.py')
        urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', tmp)
        subprocess.check_call(
            [sys.executable, tmp, '--user', '--quiet'],
        )
        os.remove(tmp)
        # Now retry install
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '--user',
             'flask', 'flask-sock', 'paramiko'],
        )
        print('[SSH Manager] Install complete.\n')
    except Exception as e:
        print(f'\n[SSH Manager] ERROR: could not install dependencies automatically.\n'
              f'  Please run manually:\n'
              f'    python -m pip install --user flask flask-sock paramiko\n'
              f'  Details: {e}\n')
        sys.exit(1)

_bootstrap()
# ─────────────────────────────────────────────────────────────────────────────

import os, select, threading, time, uuid, stat as stat_module
from flask import Flask, request, session, redirect, url_for, jsonify, render_template_string
from flask_sock import Sock
import paramiko

app = Flask(__name__)
app.secret_key = 'ssh-manager-local-secret-2024'
sock = Sock(app)

ssh_sessions = {}  # sid -> {client, sftp, host, port, username, home}

def get_sess():
    sid = session.get('sid')
    return ssh_sessions.get(sid) if sid else None

# ═══════════════════════════════════════════════════════
#  SHARED CSS / JS  (injected into every page)
# ═══════════════════════════════════════════════════════

BASE_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#080c14;--surface:#0f1624;--surface2:#151f30;--border:#1e2d45;
  --text:#cdd9e5;--muted:#546070;--accent:#38bdf8;--accent-dim:rgba(56,189,248,.12);
  --green:#4ade80;--red:#f87171;--yellow:#fbbf24;--sidebar-w:210px;
}
html,body{height:100%}
body{font-family:'Fira Code','Courier New',monospace;background:var(--bg);color:var(--text)}
input,select,textarea{font-family:inherit;background:var(--surface2);border:1px solid var(--border);
  border-radius:4px;color:var(--text);padding:6px 10px;font-size:12px;outline:none;transition:border-color .15s;width:100%}
input:focus,select:focus,textarea:focus{border-color:var(--accent)}
input::placeholder{color:var(--muted)}
.btn{padding:6px 13px;border-radius:4px;border:1px solid var(--border);font-family:inherit;
  font-size:11px;cursor:pointer;transition:all .12s;background:var(--surface2);color:var(--text);display:inline-block}
.btn:hover{border-color:var(--muted)}
.btn-primary{background:var(--accent);color:#080c14;border-color:var(--accent);font-weight:600}
.btn-primary:hover{opacity:.85}
.btn-danger{color:var(--red);border-color:transparent;background:transparent}
.btn-danger:hover{border-color:var(--red);background:rgba(248,113,113,.08)}
.btn-sm{padding:4px 10px;font-size:10px}
.tag{display:inline-block;padding:2px 7px;border-radius:3px;font-size:10px;font-weight:500}
.tag-running{background:rgba(74,222,128,.12);color:var(--green)}
.tag-pending{background:rgba(251,191,36,.12);color:var(--yellow)}
.tag-failed{background:rgba(248,113,113,.12);color:var(--red)}
.tag-other{background:var(--surface2);color:var(--muted)}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
</style>
"""

# ═══════════════════════════════════════════════════════
#  TEMPLATES
# ═══════════════════════════════════════════════════════

LOGIN_HTML = BASE_STYLE + """
<style>
body{display:flex;align-items:center;justify-content:center;height:100vh;
  background-image:radial-gradient(ellipse at 20% 60%,rgba(56,189,248,.05) 0%,transparent 55%),
                   radial-gradient(ellipse at 80% 40%,rgba(56,189,248,.04) 0%,transparent 55%)}
.card{width:400px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:36px 32px 32px}
.logo{font-size:15px;font-weight:600;color:var(--accent);letter-spacing:.06em}
.logo-sub{font-size:11px;color:var(--muted);margin-top:5px}
.card-body{margin-top:28px}
.form-row{display:flex;gap:10px;margin-bottom:14px}
.form-group{display:flex;flex-direction:column;flex:1}
.form-group.port{flex:0 0 90px}
label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}
.btn-login{width:100%;padding:10px;background:var(--accent);color:#080c14;border:none;border-radius:4px;
  font-family:inherit;font-size:13px;font-weight:600;cursor:pointer;margin-top:20px;letter-spacing:.06em;transition:opacity .15s}
.btn-login:hover{opacity:.85}
.error{background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.25);border-radius:4px;
  color:var(--red);font-size:11px;padding:10px 12px;margin-bottom:18px;line-height:1.5}
.divider{border:none;border-top:1px solid var(--border);margin:18px 0}
.hint{font-size:10px;color:var(--muted)}
</style>
<div class="card">
  <div class="logo">⬡ SSH MANAGER</div>
  <div class="logo-sub">cluster file manager &amp; terminal</div>
  <div class="card-body">
    {% if error %}<div class="error">⚠ {{ error }}</div>{% endif %}
    <form method="POST">
      <div class="form-row">
        <div class="form-group"><label>Hostname / IP</label><input name="host" placeholder="cluster.univ.it" required autofocus></div>
        <div class="form-group port"><label>Port</label><input type="number" name="port" value="22"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Username</label><input name="username" placeholder="mario.rossi" required></div>
      </div>
      <hr class="divider">
      <div class="hint">Password or SSH key (leave blank to use key agent)</div><br>
      <div class="form-row">
        <div class="form-group"><label>Password</label><input type="password" name="password" placeholder="••••••••"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>SSH Key path (optional)</label><input name="keyfile" placeholder="C:/Users/mario/.ssh/id_rsa"></div>
      </div>
      <button type="submit" class="btn-login">CONNECT →</button>
    </form>
  </div>
</div>
"""

SIDEBAR = """
<style>
body{display:flex;height:100vh;overflow:hidden}
.sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column}
.sidebar-logo{padding:18px 16px 14px;border-bottom:1px solid var(--border)}
.logo-name{font-size:13px;font-weight:600;color:var(--accent);letter-spacing:.06em}
.logo-sub{font-size:10px;color:var(--muted);margin-top:3px}
.conn-info{margin-top:10px;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.conn-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-right:5px;animation:blink 2.5s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
.sidebar-nav{flex:1;padding:10px 0}
.nav-label{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;padding:10px 16px 4px}
.nav-item{display:flex;align-items:center;gap:9px;padding:8px 16px;font-size:12px;color:var(--muted);
  text-decoration:none;border-left:2px solid transparent;transition:all .12s}
.nav-item svg{width:14px;height:14px;flex-shrink:0}
.nav-item:hover{color:var(--text);background:var(--surface2)}
.nav-item.active{color:var(--accent);border-left-color:var(--accent);background:var(--accent-dim)}
.sidebar-footer{padding:12px 16px;border-top:1px solid var(--border)}
.btn-disco{width:100%;padding:7px 10px;background:transparent;border:1px solid var(--border);border-radius:4px;
  color:var(--muted);font-family:inherit;font-size:11px;cursor:pointer;transition:all .15s;text-decoration:none;display:block;text-align:center}
.btn-disco:hover{border-color:var(--red);color:var(--red)}
.main{flex:1;overflow:hidden;display:flex;flex-direction:column}
</style>
<aside class="sidebar">
  <div class="sidebar-logo">
    <div class="logo-name">⬡ SSH MANAGER</div>
    <div class="logo-sub">cluster interface</div>
    <div class="conn-info"><span class="conn-dot"></span>{{ username }}@{{ host }}</div>
  </div>
  <nav class="sidebar-nav">
    <div class="nav-label">Explorer</div>
    <a href="/files" class="nav-item {{ 'active' if active=='files' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7a2 2 0 012-2h5l2 2h7a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>Files
    </a>
    <div class="nav-label">Compute</div>
    <a href="/slurm" class="nav-item {{ 'active' if active=='slurm' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>SLURM Queue
    </a>
    <div class="nav-label">Shell</div>
    <a href="/terminal" class="nav-item {{ 'active' if active=='terminal' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>Terminal
    </a>
  </nav>
  <div class="sidebar-footer"><a href="/disconnect" class="btn-disco">disconnect</a></div>
</aside>
<main class="main">
"""

FILES_HTML = BASE_STYLE + SIDEBAR + """
<style>
.page{display:flex;flex:1;overflow:hidden;height:100%}
.filetree{width:250px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column}
.tree-header{display:flex;align-items:center;gap:4px;padding:7px 8px;border-bottom:1px solid var(--border);flex-shrink:0}
.path-bar{flex:1;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 4px}
.icon-btn{background:none;border:1px solid transparent;border-radius:3px;color:var(--muted);cursor:pointer;
  padding:3px 6px;font-size:11px;font-family:inherit;transition:all .12s;flex-shrink:0}
.icon-btn:hover{border-color:var(--border);color:var(--text)}
.file-list{flex:1;overflow-y:auto;padding:4px 0}
.file-item{display:flex;align-items:center;gap:8px;padding:5px 12px;font-size:12px;cursor:pointer;
  color:var(--text);white-space:nowrap;overflow:hidden;transition:background .1s;user-select:none}
.file-item:hover{background:var(--surface2)}
.file-item.active{background:var(--accent-dim);color:var(--accent)}
.file-item.is-dir{color:#7dd3fc}
.file-icon{font-size:12px;flex-shrink:0}
.file-name{overflow:hidden;text-overflow:ellipsis;flex:1}
.ctx-menu{position:fixed;background:var(--surface);border:1px solid var(--border);border-radius:5px;
  padding:4px 0;z-index:1000;min-width:130px;box-shadow:0 8px 24px rgba(0,0,0,.5);font-size:12px;display:none}
.ctx-item{padding:7px 14px;cursor:pointer;color:var(--text);transition:background .1s}
.ctx-item:hover{background:var(--surface2)}
.ctx-item.danger{color:var(--red)}
.editor-panel{flex:1;display:flex;flex-direction:column;overflow:hidden}
.editor-bar{display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0}
.editor-name{flex:1;font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.editor-name.open{color:var(--text)}
.status-dot{font-size:10px;color:var(--muted);white-space:nowrap}
.status-dot.dirty{color:var(--yellow)}
.status-dot.clean{color:var(--green)}
.editor-wrap{flex:1;overflow:hidden;position:relative}
.empty-hint{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:12px;gap:10px}
.empty-hint .icon{font-size:36px;opacity:.25}
.CodeMirror{height:100%!important;font-family:'Fira Code','Courier New',monospace!important;font-size:13px!important;line-height:1.65!important;background:#060b12!important;color:#cdd9e5!important}
.CodeMirror-scroll{height:100%!important}
.CodeMirror-gutters{background:#0a1020!important;border-right:1px solid #1e2d45!important}
.CodeMirror-linenumber{color:#3d5068!important}
.CodeMirror-cursor{border-left:1.5px solid #38bdf8!important}
.CodeMirror-selected{background:rgba(56,189,248,.15)!important}
.CodeMirror-activeline-background{background:rgba(255,255,255,.018)!important}
.cm-keyword{color:#c084fc!important}.cm-def{color:#7dd3fc!important}.cm-string{color:#86efac!important}
.cm-number{color:#fbbf24!important}.cm-comment{color:#3d5068!important;font-style:italic}
.cm-operator{color:#38bdf8!important}.cm-builtin{color:#f9a8d4!important}
</style>

<div class="page">
  <div class="filetree">
    <div class="tree-header">
      <div class="path-bar" id="pathBar">{{ home }}</div>
      <button class="icon-btn" onclick="goUp()" title="Up">↑</button>
      <button class="icon-btn" onclick="goHome()" title="Home">⌂</button>
      <button class="icon-btn" onclick="refresh()" title="Refresh">↻</button>
      <button class="icon-btn" onclick="newFile()" title="New file">+f</button>
      <button class="icon-btn" onclick="newFolder()" title="New folder">+d</button>
    </div>
    <div class="file-list" id="fileList">
      <div class="file-item"><span class="file-icon">…</span><span class="file-name" style="color:var(--muted)">Loading</span></div>
    </div>
  </div>
  <div class="editor-panel">
    <div class="editor-bar">
      <span class="editor-name" id="editorName">No file open</span>
      <span class="status-dot" id="statusDot">—</span>
      <button class="btn btn-sm" onclick="saveFile()" id="saveBtn" disabled>Save  Ctrl+S</button>
    </div>
    <div class="editor-wrap" id="editorWrap">
      <div class="empty-hint" id="emptyHint"><div class="icon">📂</div><div>Click a file to open it</div></div>
      <textarea id="cm" style="display:none"></textarea>
    </div>
  </div>
</div>

<div class="ctx-menu" id="ctxMenu">
  <div class="ctx-item" id="ctxOpen">Open</div>
  <div class="ctx-item" id="ctxRename">Rename</div>
  <div class="ctx-item danger" id="ctxDelete">Delete</div>
</div>

</main>

<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/python/python.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/shell/shell.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/javascript/javascript.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/closebrackets.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/selection/active-line.min.js"></script>
<script>
const HOME='{{ home }}';
let cwd=HOME,openPath=null,editor=null;

window.addEventListener('DOMContentLoaded',()=>{
  editor=CodeMirror.fromTextArea(document.getElementById('cm'),{
    lineNumbers:true,autoCloseBrackets:true,styleActiveLine:true,
    indentUnit:4,tabSize:4,indentWithTabs:false,
    extraKeys:{'Ctrl-S':saveFile,'Cmd-S':saveFile},
  });
  editor.getWrapperElement().style.cssText='height:100%;display:none';
  editor.on('change',()=>{if(openPath)setDirty(true)});
  loadDir(cwd);
});

async function loadDir(path){
  const r=await fetch('/api/files/list?path='+encodeURIComponent(path));
  const d=await r.json();
  if(d.error)return alert(d.error);
  cwd=d.path;
  document.getElementById('pathBar').textContent=cwd;
  const list=document.getElementById('fileList');
  list.innerHTML='';
  if(d.parent&&d.parent!==cwd)list.appendChild(mkItem('..',true,d.parent,null));
  d.items.forEach(f=>list.appendChild(mkItem(f.name,f.is_dir,f.path,f)));
}

function mkItem(name,isDir,path,meta){
  const div=document.createElement('div');
  div.className='file-item'+(isDir?' is-dir':'');
  div.dataset.path=path;
  div.innerHTML=`<span class="file-icon">${isDir?'📁':iconFor(name)}</span><span class="file-name">${name}</span>`;
  div.addEventListener('click',()=>{if(isDir)loadDir(path);else openFile(path,name,div)});
  div.addEventListener('contextmenu',e=>{if(name==='..')return;e.preventDefault();showCtx(e.clientX,e.clientY,path,isDir,name,div)});
  return div;
}

function iconFor(n){const e=n.split('.').pop().toLowerCase();
  return{py:'🐍',sh:'📜',bash:'📜',js:'📜',json:'📋',md:'📝',txt:'📝',slurm:'⚙',sbatch:'⚙',yaml:'📋',yml:'📋',csv:'📊',ipynb:'📓'}[e]||'📄'}
function modeFor(n){const e=n.split('.').pop().toLowerCase();
  return{py:'python',sh:'shell',bash:'shell',slurm:'shell',sbatch:'shell',js:'javascript',json:'javascript'}[e]||null}

async function openFile(path,name,el){
  document.querySelectorAll('.file-item.active').forEach(i=>i.classList.remove('active'));
  el.classList.add('active');
  const r=await fetch('/api/files/read?path='+encodeURIComponent(path));
  const d=await r.json();
  if(d.error)return alert(d.error);
  openPath=path;
  document.getElementById('editorName').textContent=name;
  document.getElementById('editorName').classList.add('open');
  document.getElementById('saveBtn').disabled=false;
  document.getElementById('emptyHint').style.display='none';
  editor.getWrapperElement().style.display='';
  editor.setOption('mode',modeFor(name));
  editor.setValue(d.content);
  editor.clearHistory();
  editor.refresh();
  setDirty(false);
}

async function saveFile(){
  if(!openPath)return;
  const r=await fetch('/api/files/write',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:openPath,content:editor.getValue()})});
  const d=await r.json();
  if(d.error)return alert('Save failed: '+d.error);
  setDirty(false);
}

function setDirty(v){
  const dot=document.getElementById('statusDot');
  dot.textContent=v?'● unsaved':'✓ saved';
  dot.className='status-dot '+(v?'dirty':'clean');
}

function refresh(){loadDir(cwd)}
function goHome(){loadDir(HOME)}
function goUp(){const p=cwd.replace(/\\/$/,'').split('/').slice(0,-1).join('/')||'/';loadDir(p)}

async function newFile(){const name=prompt('New file name:');if(!name)return;
  const path=cwd.replace(/\\/$/,'')+'/'+name;
  const r=await fetch('/api/files/touch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();if(d.error)return alert(d.error);refresh()}

async function newFolder(){const name=prompt('New folder name:');if(!name)return;
  const path=cwd.replace(/\\/$/,'')+'/'+name;
  const r=await fetch('/api/files/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();if(d.error)return alert(d.error);refresh()}

let ctxPath=null,ctxIsDir=null,ctxName=null,ctxEl=null;
function showCtx(x,y,path,isDir,name,el){
  ctxPath=path;ctxIsDir=isDir;ctxName=name;ctxEl=el;
  const m=document.getElementById('ctxMenu');
  document.getElementById('ctxOpen').textContent=isDir?'Open folder':'Open file';
  m.style.cssText=`display:block;left:${x}px;top:${y}px`;
}
document.getElementById('ctxOpen').onclick=()=>{if(ctxIsDir)loadDir(ctxPath);else openFile(ctxPath,ctxName,ctxEl);hideCtx()};
document.getElementById('ctxRename').onclick=async()=>{
  const newName=prompt('Rename to:',ctxName);if(!newName||newName===ctxName)return hideCtx();
  const dir=ctxPath.split('/').slice(0,-1).join('/');
  const r=await fetch('/api/files/rename',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({old_path:ctxPath,new_path:dir+'/'+newName})});
  const d=await r.json();if(d.error)alert(d.error);else{if(ctxPath===openPath){openPath=dir+'/'+newName;document.getElementById('editorName').textContent=newName;}refresh();}hideCtx();
};
document.getElementById('ctxDelete').onclick=async()=>{
  if(!confirm(`Delete "${ctxName}"?`))return hideCtx();
  const r=await fetch('/api/files/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:ctxPath})});
  const d=await r.json();if(d.error)alert(d.error);
  else{if(ctxPath===openPath){openPath=null;document.getElementById('emptyHint').style.display='';editor.getWrapperElement().style.display='none';document.getElementById('editorName').textContent='No file open';document.getElementById('editorName').classList.remove('open');document.getElementById('saveBtn').disabled=true;}refresh();}
  hideCtx();
};
function hideCtx(){document.getElementById('ctxMenu').style.display='none'}
document.addEventListener('click',hideCtx);
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideCtx()});
</script>
"""

SLURM_HTML = BASE_STYLE + SIDEBAR + """
<style>
.page{display:flex;flex-direction:column;flex:1;overflow:hidden}
.top-bar{display:flex;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0}
.bar-title{flex:1;font-size:12px;color:var(--muted)}
.content{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:6px;overflow:hidden}
.card-head{display:flex;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid var(--border);font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.card-head .badge{margin-left:auto;font-size:11px;color:var(--accent);letter-spacing:0}
.card-body{padding:14px}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em;font-weight:400;padding:0 12px 8px 0;white-space:nowrap}
td{padding:7px 12px 7px 0;border-top:1px solid var(--border);vertical-align:middle}
.empty-row{color:var(--muted);text-align:center;padding:24px!important}
.log{margin-top:10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:10px 12px;
  font-size:11px;line-height:1.6;color:var(--green);white-space:pre-wrap;min-height:38px;display:none}
.log.err{color:var(--red)}
.input-row{display:flex;gap:8px}
.input-row input{flex:1}
.spinning{animation:spin .6s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
<div class="page">
  <div class="top-bar">
    <span class="bar-title">SLURM Queue — <strong style="color:var(--text)">{{ username }}</strong></span>
    <button class="btn btn-sm" id="refreshBtn" onclick="loadQueue()">↻ Refresh</button>
  </div>
  <div class="content">
    <div class="card">
      <div class="card-head">Job Queue<span class="badge" id="badge">—</span></div>
      <div class="card-body">
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>ID</th><th>Name</th><th>State</th><th>Time</th><th>Limit</th><th>Nodes</th><th>Partition</th><th>Reason</th><th></th></tr></thead>
            <tbody id="queueBody"><tr><td class="empty-row" colspan="9">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-head">Submit Job</div>
      <div class="card-body">
        <div class="input-row"><input id="scriptPath" placeholder="/home/mario/jobs/train.slurm"><button class="btn btn-primary btn-sm" onclick="submitJob()">sbatch →</button></div>
        <div class="log" id="submitLog"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-head">Run Command</div>
      <div class="card-body">
        <div class="input-row"><input id="cmdInput" placeholder="sinfo, sacct -j 12345, module list …"><button class="btn btn-sm" onclick="runCmd()">Run →</button></div>
        <div class="log" id="cmdLog"></div>
      </div>
    </div>
  </div>
</div>
</main>
<script>
function stateClass(s){if(s==='RUNNING')return 'tag-running';if(s==='PENDING')return 'tag-pending';if(['FAILED','CANCELLED','TIMEOUT'].includes(s))return 'tag-failed';return 'tag-other'}

async function loadQueue(){
  const btn=document.getElementById('refreshBtn');
  btn.innerHTML='<span class="spinning">↻</span> Refresh';
  const r=await fetch('/api/slurm/queue');const d=await r.json();
  btn.innerHTML='↻ Refresh';
  const body=document.getElementById('queueBody');
  if(d.error){body.innerHTML=`<tr><td class="empty-row" colspan="9" style="color:var(--red)">${d.error}</td></tr>`;return}
  document.getElementById('badge').textContent=d.jobs.length?`${d.jobs.length} jobs`:'idle';
  if(!d.jobs.length){body.innerHTML='<tr><td class="empty-row" colspan="9">No jobs in queue</td></tr>';return}
  body.innerHTML=d.jobs.map(j=>`<tr>
    <td style="color:var(--accent);font-weight:500">${j.id}</td><td>${j.name}</td>
    <td><span class="tag ${stateClass(j.state)}">${j.state}</span></td>
    <td>${j.time}</td><td>${j.time_limit}</td><td>${j.nodes}</td><td>${j.partition}</td>
    <td style="color:var(--muted);font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${j.reason}</td>
    <td><button class="btn btn-danger btn-sm" onclick="cancelJob('${j.id}')">✕</button></td>
  </tr>`).join('');
}

async function cancelJob(id){if(!confirm(`Cancel job ${id}?`))return;
  const r=await fetch('/api/slurm/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:id})});
  const d=await r.json();if(d.error)alert(d.error);else loadQueue()}

async function submitJob(){const path=document.getElementById('scriptPath').value.trim();if(!path)return;
  const r=await fetch('/api/slurm/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({script_path:path})});
  const d=await r.json();const log=document.getElementById('submitLog');log.style.display='';
  const isErr=!!d.error&&!d.output;log.className='log'+(isErr?' err':'');log.textContent=d.output||d.error||'Done.';
  if(!isErr)setTimeout(loadQueue,800)}

async function runCmd(){const cmd=document.getElementById('cmdInput').value.trim();if(!cmd)return;
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});
  const d=await r.json();const log=document.getElementById('cmdLog');log.style.display='';
  const out=(d.stdout||'')+(d.stderr||'')||d.error||'(no output)';
  log.className='log'+(!d.stdout&&(d.stderr||d.error)?' err':'');log.textContent=out}

loadQueue();setInterval(loadQueue,15000);
</script>
"""

TERMINAL_HTML = BASE_STYLE + SIDEBAR + """
<style>
.page{display:flex;flex-direction:column;flex:1;overflow:hidden}
.term-bar{display:flex;align-items:center;gap:10px;padding:7px 14px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0;font-size:11px;color:var(--muted)}
.term-led{width:7px;height:7px;border-radius:50%;background:var(--green);flex-shrink:0;animation:blink 2.5s ease-in-out infinite}
.term-led.off{background:var(--red);animation:none}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.term-wrap{flex:1;overflow:hidden;padding:6px;background:#050911}
.xterm{height:100%}.xterm-viewport{overflow-y:auto!important}
</style>
<div class="page">
  <div class="term-bar">
    <div class="term-led" id="led"></div>
    <span>{{ username }}@{{ host }}</span>
    <span style="margin-left:auto;font-size:10px">xterm-256color · WebSocket</span>
  </div>
  <div class="term-wrap" id="termWrap"></div>
</div>
</main>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
<script>
const term=new Terminal({fontFamily:"'Fira Code','Courier New',monospace",fontSize:13,lineHeight:1.5,cursorBlink:true,scrollback:8000,
  theme:{background:'#050911',foreground:'#cdd9e5',cursor:'#38bdf8',cursorAccent:'#050911',
    selectionBackground:'rgba(56,189,248,0.2)',black:'#1e2d45',brightBlack:'#546070',
    red:'#f87171',brightRed:'#fca5a5',green:'#4ade80',brightGreen:'#86efac',
    yellow:'#fbbf24',brightYellow:'#fde68a',blue:'#38bdf8',brightBlue:'#7dd3fc',
    magenta:'#a78bfa',brightMagenta:'#c4b5fd',cyan:'#22d3ee',brightCyan:'#67e8f9',
    white:'#cdd9e5',brightWhite:'#f4f4f5'}});
const fitAddon=new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById('termWrap'));
fitAddon.fit();
const proto=location.protocol==='https:'?'wss:':'ws:';
const ws=new WebSocket(`${proto}//${location.host}/ws/terminal`);
const led=document.getElementById('led');
ws.onopen=()=>term.write('\x1b[38;5;81m▶ Connected to {{ host }}\x1b[0m\r\n');
ws.onmessage=e=>term.write(typeof e.data==='string'?e.data:new Uint8Array(e.data));
ws.onclose=()=>{led.classList.add('off');term.write('\r\n\x1b[31m● Connection closed.\x1b[0m\r\n')};
ws.onerror=()=>{led.classList.add('off');term.write('\r\n\x1b[31m● WebSocket error.\x1b[0m\r\n')};
term.onData(data=>{if(ws.readyState===WebSocket.OPEN)ws.send(data)});
new ResizeObserver(()=>fitAddon.fit()).observe(document.getElementById('termWrap'));
</script>
"""

# ═══════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════

@app.route('/')
def index():
    return redirect(url_for('files') if get_sess() else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        host     = request.form.get('host', '').strip()
        port     = int(request.form.get('port') or 22)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        keyfile  = request.form.get('keyfile', '').strip()
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kw = dict(hostname=host, port=port, username=username, timeout=10)
            if keyfile:  kw['key_filename'] = keyfile
            elif password: kw['password'] = password
            client.connect(**kw)
            sftp = client.open_sftp()
            _, stdout, _ = client.exec_command('echo $HOME')
            home = stdout.read().decode().strip() or f'/home/{username}'
            sid = str(uuid.uuid4())
            ssh_sessions[sid] = dict(client=client, sftp=sftp, host=host, port=port, username=username, home=home)
            session['sid'] = sid
            return redirect(url_for('files'))
        except Exception as e:
            error = str(e)
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/disconnect')
def disconnect():
    sid = session.get('sid')
    if sid and sid in ssh_sessions:
        try: ssh_sessions[sid]['sftp'].close(); ssh_sessions[sid]['client'].close()
        except: pass
        del ssh_sessions[sid]
    session.clear()
    return redirect(url_for('login'))

@app.route('/files')
def files():
    d = get_sess()
    if not d: return redirect(url_for('login'))
    return render_template_string(FILES_HTML, active='files', host=d['host'], username=d['username'], home=d['home'])

@app.route('/slurm')
def slurm():
    d = get_sess()
    if not d: return redirect(url_for('login'))
    return render_template_string(SLURM_HTML, active='slurm', host=d['host'], username=d['username'])

@app.route('/terminal')
def terminal():
    d = get_sess()
    if not d: return redirect(url_for('login'))
    return render_template_string(TERMINAL_HTML, active='terminal', host=d['host'], username=d['username'])

# ── File API ──────────────────────────────────────────

@app.route('/api/files/list')
def api_list():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    path = request.args.get('path', d['home'])
    try:
        items = []
        for attr in d['sftp'].listdir_attr(path):
            if attr.filename.startswith('.'): continue
            is_dir = stat_module.S_ISDIR(attr.st_mode)
            items.append(dict(name=attr.filename, is_dir=is_dir,
                              size=0 if is_dir else attr.st_size,
                              path=path.rstrip('/')+'/'+attr.filename))
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        parent = '/'.join(path.rstrip('/').split('/')[:-1]) or '/'
        return jsonify({'path': path, 'items': items, 'parent': parent})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/files/read')
def api_read():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try:
        with d['sftp'].open(request.args.get('path'), 'r') as f:
            return jsonify({'content': f.read().decode('utf-8', errors='replace')})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/files/write', methods=['POST'])
def api_write():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    body = request.json
    try:
        with d['sftp'].open(body['path'], 'w') as f: f.write(body.get('content','').encode())
        return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/files/touch', methods=['POST'])
def api_touch():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try:
        with d['sftp'].open(request.json['path'], 'w') as f: f.write(b'')
        return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/files/mkdir', methods=['POST'])
def api_mkdir():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try: d['sftp'].mkdir(request.json['path']); return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/files/delete', methods=['POST'])
def api_delete():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try:
        _, stdout, _ = d['client'].exec_command(f'rm -rf -- "{request.json["path"]}"')
        stdout.channel.recv_exit_status()
        return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/files/rename', methods=['POST'])
def api_rename():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    body = request.json
    try: d['sftp'].rename(body['old_path'], body['new_path']); return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

# ── Run API ──────────────────────────────────────────

@app.route('/api/run', methods=['POST'])
def api_run():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    body = request.json
    cmd = body.get('command', '')
    if body.get('cwd'): cmd = f'cd "{body["cwd"]}" && {cmd}'
    try:
        _, stdout, stderr = d['client'].exec_command(cmd, timeout=30)
        return jsonify({'stdout': stdout.read().decode('utf-8', errors='replace'),
                        'stderr': stderr.read().decode('utf-8', errors='replace'),
                        'rc': stdout.channel.recv_exit_status()})
    except Exception as e: return jsonify({'error': str(e)}), 500

# ── SLURM API ────────────────────────────────────────

@app.route('/api/slurm/queue')
def api_slurm_queue():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try:
        _, stdout, _ = d['client'].exec_command(
            f'squeue -u {d["username"]} -o "%i|%j|%T|%M|%l|%D|%P|%R" --noheader 2>&1')
        jobs = []
        for line in stdout.read().decode().strip().split('\n'):
            if not line.strip(): continue
            p = line.split('|')
            if len(p) >= 8:
                jobs.append(dict(id=p[0].strip(), name=p[1].strip(), state=p[2].strip(),
                                 time=p[3].strip(), time_limit=p[4].strip(),
                                 nodes=p[5].strip(), partition=p[6].strip(), reason=p[7].strip()))
        return jsonify({'jobs': jobs})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/slurm/submit', methods=['POST'])
def api_slurm_submit():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try:
        _, stdout, stderr = d['client'].exec_command(f'sbatch "{request.json["script_path"]}"')
        return jsonify({'output': stdout.read().decode().strip(), 'error': stderr.read().decode().strip()})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/slurm/cancel', methods=['POST'])
def api_slurm_cancel():
    d = get_sess()
    if not d: return jsonify({'error': 'Not connected'}), 401
    try:
        _, stdout, _ = d['client'].exec_command(f'scancel {request.json["job_id"]}')
        stdout.channel.recv_exit_status()
        return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

# ── WebSocket Terminal ────────────────────────────────

@sock.route('/ws/terminal')
def ws_terminal(ws):
    d = get_sess()
    if not d: return
    channel = d['client'].invoke_shell(term='xterm-256color', width=220, height=50)
    stop = threading.Event()
    def read_ssh():
        while not stop.is_set():
            try:
                r, _, _ = select.select([channel], [], [], 0.1)
                if r:
                    data = channel.recv(4096)
                    if not data: stop.set(); break
                    ws.send(data.decode('utf-8', errors='replace'))
            except: stop.set(); break
    threading.Thread(target=read_ssh, daemon=True).start()
    try:
        while not stop.is_set():
            msg = ws.receive()
            if msg is None: break
            channel.send(msg.encode() if isinstance(msg, str) else msg)
    except: pass
    finally:
        stop.set()
        try: channel.close()
        except: pass

# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    def _open():
        time.sleep(1.2)
        import webbrowser; webbrowser.open('http://localhost:5000')
    threading.Thread(target=_open, daemon=True).start()
    print('\n  ⬡ SSH Manager → http://localhost:5000\n')
    app.run(debug=False, port=5000, threaded=True)