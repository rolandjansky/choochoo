
import datetime as dt
import time as t
from collections import defaultdict
from importlib.resources import read_binary
from json import dumps
from logging import getLogger
from os.path import split, sep, splitext

from werkzeug import Response, Request, run_simple
from werkzeug.exceptions import HTTPException
from werkzeug.routing import Map, Rule

from ..commands.args import TUI, LOG, DATABASE, SYSTEM, WEB, SERVICE, VERBOSITY, BIND, PORT, DEV
from ..diary.database import read_date, read_schedule
from ..diary.views.web import rewrite_db
from ..lib.schedule import Schedule
from ..lib.server import BaseController

log = getLogger(__name__)


class WebController(BaseController):

    def __init__(self, args, sys, db, max_retries=1, retry_secs=1):
        super().__init__(args, sys, WebServer, max_retries=max_retries, retry_secs=retry_secs)
        self.__bind = args[BIND] if BIND in args else None
        self.__port = args[PORT] if BIND in args else None
        self.__dev = args[DEV]
        self.__db = db

    def _build_cmd_and_log(self, ch2):
        log_name = 'web-service.log'
        cmd = f'{ch2} --{VERBOSITY} {self._log_level} --{TUI} --{LOG} {log_name} --{DATABASE} {self._database} ' \
              f'--{SYSTEM} {self._system} {WEB} {SERVICE} --{BIND} {self.__bind} --{PORT} {self.__port}'
        return cmd, log_name

    def _run(self):
        run_simple(self.__bind, self.__port, WebServer(self.__db),
                   use_debugger=self.__dev, use_reloader=self.__dev)


class WebServer:

    def __init__(self, db):
        self.__db = db
        api = Api()
        static = Static('.static')
        self.url_map = Map([
            Rule('/api/diary/<date>', endpoint=api.diary, methods=('GET',)),
            Rule('/api/statistics', endpoint=api.statistics, methods=('POST',)),
            Rule('/static/<path:path>', endpoint=static, methods=('GET', )),
            Rule('/<path:_>', defaults={'path': 'index.html'}, endpoint=static, methods=('GET',)),
            Rule('/', defaults={'path': 'index.html'}, endpoint=static, methods=('GET',))
        ])

    def dispatch_request(self, request):
        adapter = self.url_map.bind_to_environ(request.environ)
        try:
            endpoint, values = adapter.match()
            values.pop('_', None)
            with self.__db.session_context() as s:
                return endpoint(request, s, **values)
        except HTTPException as e:
            return e

    def wsgi_app(self, environ, start_response):
        request = Request(environ)
        response = self.dispatch_request(request)
        return response(environ, start_response)

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)


def parse_date(date):
    for schedule, format in (('y', '%Y'), ('m', '%Y-%m'), ('d', '%Y-%m-%d')):
        try:
            return schedule, dt.date(*t.strptime(date, format)[:3])
        except:
            pass
    raise Exception(f'Cannot parse {date}')


class Api:

    def diary(self, request, s, date):
        schedule, date = parse_date(date)
        if schedule == 'd':
            data = read_date(s, date)
        else:
            data = read_schedule(s, Schedule(schedule), date)
        return Response(dumps(rewrite_db(list(data))))

    def statistics(self, request, s):
        return Response()


class Static:

    CONTENT_TYPE = defaultdict(lambda: 'text/plain', {
        'js': 'text/javascript',
        'html': 'text/html',
        'css': 'text/css'
    })

    def __init__(self, package):
        if package.startswith('.'):
            self.__package = __name__.rsplit('.', maxsplit=1)[0] + package
        else:
            self.__package = package

    def __call__(self, request, s, path):
        package, file = self.parse_path(path)
        log.info(f'Reading {file} from {package}')
        response = Response(read_binary(package, file))
        self.set_content_type(response, file)
        return response

    def parse_path(self, path):
        package = self.__package
        head, tail = split(path)
        log.debug(f'{path} -> {head}, {tail}')
        if not tail:
            raise Exception(f'{path} is a directory')
        if tail == '__init__.py':
            raise Exception('Refusing to serve package marker')
        if '.' in head:
            raise Exception(f'Package separators in {head}')
        if head:
            package += '.' + '.'.join(head.split(sep))
        return package, tail

    def set_content_type(self, response, name):
        ext = splitext(name)[1].lower()
        if ext:
            ext = ext[1:]
        response.content_type = self.CONTENT_TYPE[ext]
