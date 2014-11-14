from cookielib import Cookie, parse_ns_headers
from json import loads
from pyramid.renderers import render
from pyramid.security import remember
from pyramid.testing import DummyRequest
from pyramid.testing import setUp, tearDown
from pyramid_mailer import get_mailer
from pytest import fixture
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from transaction import abort
from urllib import unquote
from webtest import TestApp as TestAppBase


def project_name():
    from . import project_name
    return project_name


def as_dict(content, **kw):
    return dict(loads(render('json', content, DummyRequest())), **kw)


def route_url(name, **kwargs):
    return unquote(DummyRequest().route_url(name, **kwargs))


def auth_cookies(login):
    environ = dict(HTTP_HOST='example.com')
    cookies = [c for _, c in remember(DummyRequest(environ=environ), login)]
    for data in parse_ns_headers(cookies):
        name, value = data.pop(0)
        rest = dict(port='80', port_specified=True, domain_specified=True,
            path_specified=True, secure=False, expires=None, discard=False,
            comment='', comment_url=None, rest=None, domain='')
        rest.update(dict(data))
        rest['domain_initial_dot'] = rest['domain'].startswith('.')
        yield Cookie(name=name, value=value, **rest)


# settings for test configuration
settings = {
    'auth.secret': 's3crit',
    'pyramid.includes': 'pyramid_mailer.testing',
    'redirect.reset_password.form': '/#/reset/%s',
    'redirect.change_email.success': '/#/email-changed',
    'redirect.signup_confirm.success': '/#/welcome',
    'testing': True,
    'debug': True,
    'demo': True,
}


@fixture
def config(request, tmpdir):
    """ Sets up a Pyramid `Configurator` instance suitable for testing. """
    config = setUp(settings=dict(settings, filesafe=str(tmpdir)))
    request.addfinalizer(tearDown)
    return config


@fixture(scope='session')
def connection(models, request):
    """ Sets up an SQLAlchemy engine and returns a connection
        to the database.  The connection string used can be overriden
        via the `PGDATABASE` environment variable. """
    from .models import db_session, metadata
    from .utils import create_db_engine
    engine = create_db_engine(suffix='_test',
        project_name=project_name(), **settings)
    try:
        connection = engine.connect()
    except OperationalError:
        # try to create the database...
        db_url = str(engine.url).replace(engine.url.database, 'template1')
        e = create_engine(db_url)
        c = e.connect()
        c.connection.connection.set_isolation_level(0)
        c.execute('create database %s' % engine.url.database)
        c.connection.connection.set_isolation_level(1)
        c.close()
        # ...and connect again
        connection = engine.connect()
    db_session.registry.clear()
    db_session.configure(bind=connection)
    metadata.bind = engine
    metadata.drop_all(connection.engine)
    metadata.create_all(connection.engine)
    return connection


@fixture
def db_session(config, connection, request):
    """ Returns a database session object and sets up a transaction
        savepoint, which will be rolled back after running a test. """
    trans = connection.begin()          # begin a non-orm transaction
    request.addfinalizer(trans.rollback)
    request.addfinalizer(abort)
    from .models import db_session
    return db_session()


class TestApp(TestAppBase):

    def get_json(self, url, params=None, headers=None, *args, **kw):
        if headers is None:
            headers = {}
        headers['Accept'] = 'application/json'
        return self.get(url, params, headers, *args, **kw)


@fixture(scope='session')
def testing():
    """ Returns the `testing` module. """
    from sys import modules
    return modules[__name__]    # `testing.py` has already been imported


@fixture(scope='session')
def models():
    """ Returns the `models` module. """
    from . import models
    return models


@fixture(scope='session')
def principals():
    """ Returns the `principals` module. """
    from . import principals
    return principals


@fixture(scope='session')
def views():
    """ Returns the `views` module. """
    from . import views
    return views


@fixture
def app(config):
    """ Returns WSGI application wrapped in WebTest's testing interface. """
    from . import configure
    return configure({}, **config.registry.settings).make_wsgi_app()


@fixture
def dummy_request(request, config):
    config.manager.get()['request'] = req = DummyRequest()
    if 'user' in request.keywords:
        # set user directly on request
        username = request.keywords['user'].args[0]
        req.user = request.getfuncargvalue(username)    # get user from their fixture
    else:
        req.user = None
    return req


@fixture
def browser(db_session, app, request):
    """ Returns an instance of `webtest.TestApp`.  The `user` pytest marker
        (`pytest.mark.user`) can be used to pre-authenticate the browser
        with the given login name: `@user('admin')`. """
    extra_environ = dict(HTTP_HOST='example.com')
    browser = TestApp(app, extra_environ=extra_environ)
    if 'user' in request.keywords:
        # set auth cookie directly on the browser instance...
        name = request.keywords['user'].args[0]
        user = request.getfuncargvalue(name)    # get user from their fixture
        for cookie in auth_cookies(str(user.id)):
            browser.cookiejar.set_cookie(cookie)
    return browser


@fixture
def mailer(config):
    """ Return mock mailer from `pyramid_mailer`. """
    return get_mailer(DummyRequest())


def includeme(config):
    view = lambda request: dict(foo='bar')
    config.add_route('dummy', '/dummy')
    config.add_view(view=view, route_name='dummy', renderer='json', permission='view')
