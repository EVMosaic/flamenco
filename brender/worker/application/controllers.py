import socket
import urllib
import time
import sys
import subprocess
import platform
import psutil
import flask
import os
import json
import select
import requests
import gocept.cache.method
from threading import Thread
from threading import Lock
import Queue # for windows

from flask import Flask
from flask import redirect
from flask import url_for
from flask import request
from flask import jsonify
from flask import Blueprint
from uuid import getnode as get_mac_address

from application import app

MAC_ADDRESS = get_mac_address()  # the MAC address of the worker
HOSTNAME = socket.gethostname()  # the hostname of the worker
SYSTEM = platform.system() + ' ' + platform.release()
PROCESS = None
LOCK = Lock()

if platform.system() is not 'Windows':
    from fcntl import fcntl, F_GETFL, F_SETFL
    from signal import SIGKILL
else:
    from signal import CTRL_C_EVENT


BRENDER_MANAGER = app.config['BRENDER_MANAGER']

controller_bp = Blueprint('controllers', __name__)

def http_request(command, values):
    params = urllib.urlencode(values)
    try:
        urllib.urlopen('http://' + BRENDER_MANAGER + '/' + command, params)
        #print(f.read())
    except IOError:
        print("[Warning] Could not connect to server to register")

def register_worker(port):
    """This is going to be an HTTP request to the server with all the info
    for registering the render node.
    """
    import httplib
    while True:
        try:
            manager_url = "http://{0}/info".format(app.config['BRENDER_MANAGER'])
            requests.get(manager_url)
            break
        except socket.error:
            pass
        time.sleep(0.1)

    http_request('workers', {'port': port,
                               'hostname': HOSTNAME,
                               'system': SYSTEM})

def _checkProcessOutput(process):
    ready = select.select([process.stdout.fileno(),
                           process.stderr.fileno()],
                          [], [])
    full_buffer = ''
    for fd in ready[0]:
        while True:
            try:
                buffer = os.read(fd, 1024)
                if not buffer:
                    break
                print buffer
            except OSError:
                break
            full_buffer += buffer
    return full_buffer

def _checkOutputThreadWin(fd, q):
    while True:
        buffer = os.read(fd, 1024)
        if not buffer:
            break
        else:
            print buffer
            q.put(buffer)

def _checkProcessOutputWin(process, q):
    full_buffer = ''
    while True:
        try:
            buffer = q.get_nowait()
            if not buffer:
                break
        except:
            break
        full_buffer += buffer
    return full_buffer

def _interactiveReadProcessWin(process, task_id):
    full_buffer = ''
    tmp_buffer = ''
    q = Queue.Queue()
    t_out = Thread(target=_checkOutputThreadWin, args=(process.stdout.fileno(), q,))
    t_err = Thread(target=_checkOutputThreadWin, args=(process.stderr.fileno(), q,))

    t_out.start()
    t_err.start()

    while True:
        tmp_buffer += _checkProcessOutputWin(process, q)
        if tmp_buffer:
            pass
        full_buffer += tmp_buffer
        if process.poll() is not None:
            break

    t_out.join()
    t_err.join()
    full_buffer += _checkProcessOutputWin(process, q)
    return (process.returncode, full_buffer)

import re

def send_thumbnail(manager_url, file_path, params):
            thumbnail_file = open(file_path, 'r')
            requests.post(manager_url, files={'file': thumbnail_file}, data=params)
            thumbnail_file.close()

def parser(output, task_id):
    """Parser test, TODO: move this.
    """

    re_frame = re.compile(
    r'Saved: (.*?)\s'
    )

    match = re_frame.findall(output)
    if len(match):
        file_name = "thumbnail_%s.png" % task_id
        output_path = os.path.join(app.config['TMP_FOLDER'], file_name)
        #subprocess.call(["convert", "-identify", match[-1], "-thumbnail", "50x50^", "-gravity", "center", "-extent", "50x50", "-colorspace", "RGB", output_path ])
        subprocess.call(["convert", "-identify", match[-1], "-colorspace", "RGB", output_path ])

        params = dict(task_id=task_id)
        manager_url = "http://%s/thumbnails" % (app.config['BRENDER_MANAGER'])

        request_thread = Thread(target=send_thumbnail, args=(manager_url, output_path, params))
        request_thread.start()

def _interactiveReadProcess(process, task_id):
    full_buffer = ''
    tmp_buffer = ''
    while True:
        tmp_buffer = _checkProcessOutput(process)

        if tmp_buffer:
            parser(tmp_buffer,task_id)
            logpath = os.path.join(app.config['TMP_FOLDER'], "{0}.log".format(task_id))
            f = open(logpath,"a")
            f.write(tmp_buffer)
            f.close()
            pass
        if process.poll() is not None:
            break
    # It might be some data hanging around in the buffers after
    # the process finished
    #full_buffer += _checkProcessOutput(process)

    return (process.returncode, full_buffer)

@app.route('/')
def index():
    return redirect(url_for('info'))

@app.route('/info')
def info():
    if PROCESS:
        status = 'rendering'
    else:
        status = 'enabled'
    return jsonify(mac_address=MAC_ADDRESS,
                   hostname=HOSTNAME,
                   system=SYSTEM,
                   status=status)

def run_blender_in_thread(options):
    global PROCESS
    """We take the command and run it
    """
    render_command = json.loads(options['task_command'])

    print("[Info] Running %s" % render_command)

    PROCESS = subprocess.Popen(render_command,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)

    # Make I/O non blocking for unix
    if platform.system() is not 'Windows':
        flags = fcntl(PROCESS.stdout, F_GETFL)
        fcntl(PROCESS.stdout, F_SETFL, flags | os.O_NONBLOCK)
        flags = fcntl(PROCESS.stderr, F_GETFL)
        fcntl(PROCESS.stderr, F_SETFL, flags | os.O_NONBLOCK)

    #flask.g.blender_process = process
    (retcode, full_output) =  _interactiveReadProcess(PROCESS, options["task_id"]) \
        if (platform.system() is not "Windows") \
        else _interactiveReadProcessWin(PROCESS, options["task_id"])

    print ('[DEBUG] return code: %d') % retcode
    PROCESS = None

    #flask.g.blender_process = None
    #print(full_output)
    script_dir = os.path.dirname(__file__)
    rel_path = 'render_log_' + HOSTNAME + '.log'
    abs_file_path = os.path.join(script_dir, rel_path)
    with open(abs_file_path, 'w') as f:
        f.write(full_output)


    time.sleep(1)

    if retcode == 137:
        requests.patch('http://' + BRENDER_MANAGER  + '/tasks/' + options['task_id'], data={'status': 'aborted'})
    elif retcode != 0:
        requests.patch('http://' + BRENDER_MANAGER  + '/tasks/' + options['task_id'], data={'status': 'failed'})
    else:
        requests.patch('http://' + BRENDER_MANAGER  + '/tasks/' + options['task_id'], data={'status': 'finished'})

@app.route('/execute_task', methods=['POST'])
def execute_task():
    global PROCESS
    global LOCK
    options = {
        'task_id': request.form['task_id'],
        'task_command': request.form['task_command'],
    }

    LOCK.acquire()
    PROCESS = None
    render_thread = Thread(target=run_blender_in_thread, args=(options,))
    render_thread.start()

    while PROCESS is None:
        time.sleep(1)

    LOCK.release()
    if PROCESS.poll():
        return '{error:Processus failed}', 500

    return jsonify(dict(pid=PROCESS.pid))

@app.route('/pid')
def get_pid():
    global PROCESS
    response = dict(pid=PROCESS.pid)
    return jsonify(response)

@app.route('/command', methods=['HEAD'])
def get_command():
    # TODO Return the running command
    return '', 503

@app.route('/kill/<int:pid>', methods=['DELETE'])
def update(pid):
    global PROCESS
    global LOCK
    print('killing')
    if platform.system() is 'Windows':
        os.kill(pid, CTRL_C_EVENT)
    else:
        os.kill(pid, SIGKILL)

    LOCK.acquire()
    PROCESS = None
    LOCK.release()
    return '', 204

def online_stats(system_stat):
    '''
    if 'blender_cpu' in [system_stat]:
        try:
            find_blender_process = [x for x in psutil.process_iter() if x.name == 'blender']
            cpu = []
            if find_blender_process:
                for process in find_blender_process:
                    cpu.append(process.get_cpu_percent())
                    return round(sum(cpu), 2)
            else:
                return int(0)
        except psutil._error.NoSuchProcess:
            return int(0)
    if 'blender_mem' in [system_stat]:
        try:
            find_blender_process = [x for x in psutil.get_process_list() if x.name == 'blender']
            mem = []
            if find_blender_process:
                for process in find_blender_process:
                    mem.append(process.get_memory_percent())
                    return round(sum(mem), 2)
            else:
                return int(0)
        except psutil._error.NoSuchProcess:
            return int(0)
    '''

    if 'system_cpu' in [system_stat]:
        try:
            cputimes = psutil.cpu_percent(interval=1)
            return cputimes
        except:
            return int(0)
    if 'system_mem' in [system_stat]:
        mem_percent = psutil.phymem_usage().percent
        return mem_percent
    if 'system_disk' in [system_stat]:
        disk_percent = psutil.disk_usage('/').percent
        return disk_percent

def offline_stats(offline_stat):
    if 'number_cpu' in [offline_stat]:
        return psutil.NUM_CPUS

    if 'arch' in [offline_stat]:
        return platform.machine()

@gocept.cache.method.Memoize(5)
def get_system_load_frequent():
    if platform.system() is not "Windows":
        load = os.getloadavg()
        return ({
            "load_average": ({
                "1min": round(load[0], 2),
                "5min": round(load[1], 2),
                "15min": round(load[2], 2)
                }),
            "worker_cpu_percent": online_stats('system_cpu'),
            #'worker_blender_cpu_usage': online_stats('blender_cpu')
            })
    else:
        # os.getloadavg does not exists on Windows
        return ({
            "load_average":({
                "1min": '?',
                "5min": '?',
                "15min": '?'
            }),
           "worker_cpu_percent": online_stats('system_cpu')
        })

@gocept.cache.method.Memoize(120)
def get_system_load_less_frequent():
    return ({
        "worker_num_cpus": offline_stats('number_cpu'),
        "worker_architecture": offline_stats('arch'),
        "worker_mem_percent": online_stats('system_mem'),
        "worker_disk_percent": online_stats('system_disk'),
        # "worker_blender_mem_usage": online_stats('blender_mem')
        })

@app.route('/run_info')
def run_info():
    print('[Debug] get_system_load for %s') % HOSTNAME
    return jsonify(mac_address=MAC_ADDRESS,
                   hostname=HOSTNAME,
                   system=SYSTEM,
                   update_frequent=get_system_load_frequent(),
                   update_less_frequent=get_system_load_less_frequent()
                   )
