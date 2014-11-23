import json
from flask import (flash,
                   render_template,
                   request,
                   Blueprint,
                   url_for,
                   redirect)

from dashboard import app
from dashboard import http_request
from dashboard import list_integers_string
from dashboard import check_connection
from dashboard import http_server_request

# TODO: find a better way to fill/use this variable
BRENDER_SERVER = app.config['BRENDER_SERVER']


# Name of the Blueprint
projects = Blueprint('projects', __name__)


@projects.route('/')
def index():
    #projects = http_request(BRENDER_SERVER, '/projects')
    settings = http_request(BRENDER_SERVER, '/settings/')

    projects = http_server_request('get', '/projects')

    return render_template('projects/index.html', 
        projects=projects, 
        settings=settings, 
        title='projects')


@projects.route('/update/<project_id>', methods=['POST'])
def update(project_id):

    params = dict(
        path_server=request.form['path_server'],
        path_linux=request.form['path_linux'],
        path_win=request.form['path_win'],
        path_osx=request.form['path_osx'])

    projects = http_server_request('put', '/projects/' + project_id, params)
    print projects
    # http_request(BRENDER_SERVER, '/projects/update', params)

    return redirect(url_for('projects.index'))


@projects.route('/delete/<project_id>', methods=['GET', 'POST'])
def delete(project_id):
    http_server_request('delete', '/projects/' + project_id)
    #http_request(BRENDER_SERVER, '/projects/delete/' + project_id)
    return redirect(url_for('projects.index'))


@projects.route('/add', methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        params = dict(
            name=request.form['name'],
            path_server=request.form['path_server'],
            path_linux=request.form['path_linux'],
            path_win=request.form['path_win'],
            path_osx=request.form['path_osx'],
            is_active=request.form['set_project_option'])
        http_server_request('post', '/projects', params)
        return redirect(url_for('projects.index'))
    else:
        render_settings = http_request(BRENDER_SERVER, '/settings/render')
        projects = http_request(BRENDER_SERVER, '/projects/')
        settings = http_request(BRENDER_SERVER, '/settings/')
        return render_template('projects/add_modal.html',
                        render_settings=render_settings,
                        settings=settings,
                        projects=projects)