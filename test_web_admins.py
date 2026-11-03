"""Web-only grants, revocation and retained master access; no live files/network."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
from http.client import HTTPMessage
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_access_settings import AccessSettings, validate_settings, export_access, import_access
from web_admin_permissions import registered_web_admin
from web_font_library import font_developer_allowed
from web_service_manager import WebServiceManager


class WebAdminTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='web-admin-unit-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / 'web'
        self.policy = AccessSettings(self.root)
        self.policy.ensure_saved()
        self.identity = 'a'*32
        self.context = dict(visitor=self.identity, authenticated=True, role='member', blocked=False)
        self.access = NS(root=self.root, lock=threading.RLock(), settings=self.policy.data,
                         visitors={self.identity:dict(nickname='registered user')})

    def add(self):
        self.policy.change('admin_add',dict(id=self.identity,nickname='registered user'))
        self.access.settings=self.policy.data

    def test_registration_preserves_login_keys_and_persists_across_restart_and_transfer(self):
        before = deepcopy(self.policy.data)
        self.add()
        for key in ('key','epoch','guest_epoch','salt','verifier'):
            self.assertEqual(before[key], self.policy.data[key])
        self.assertEqual(AccessSettings(self.root).data['web_admins'], self.policy.data['web_admins'])
        exported,pins=export_access(self.policy.data)
        self.assertEqual(import_access(exported,pins)['web_admins'],self.policy.data['web_admins'])
        self.assertTrue(registered_web_admin(self.access,self.context))

    def test_guest_unauthenticated_blocked_and_same_nickname_other_browser_are_denied(self):
        self.add()
        for changes in [dict(role='guest'),dict(authenticated=False),dict(blocked=True),dict(visitor='b'*32),dict(role='')]:
            self.assertFalse(registered_web_admin(self.access,dict(self.context,**changes)))
        self.access.visitors['b'*32]=dict(nickname='registered user')
        self.assertFalse(font_developer_allowed(self.access,dict(self.context,visitor='b'*32),'127.0.0.1'))

    def test_role_comes_from_signed_session_even_if_visitor_record_claims_member(self):
        from web_access_runtime import AccessRuntime, VISITOR_COOKIE, SESSION_COOKIE
        with patch('web_access_settings.protect_secret',return_value='opaque'):
            self.policy.change('pin','1234');self.policy.change('guest_pin','5678')
        self.add()
        runtime=AccessRuntime(self.root,'c'*32,None)
        runtime.refresh()
        for role,prefix in [('member',''),('guest','guest_')]:
            raw=(VISITOR_COOKIE+'='+runtime._sign('visitor',self.identity)+'; '+SESSION_COOKIE+'='+
                 runtime._sign('session',self.identity+':'+self.policy.data[prefix+'epoch']))
            runtime.visitors[self.identity]=dict(id=self.identity,role='member',nickname='same name')
            context=runtime.context(raw,'127.0.0.1','Chrome',record_visit=False)
            self.assertEqual(context['role'],role)
            self.assertEqual(registered_web_admin(runtime,context),role=='member')

    def test_revocation_takes_effect_without_invalidating_login(self):
        self.add()
        epoch=self.policy.data['epoch']
        self.assertTrue(font_developer_allowed(self.access,self.context,'127.0.0.1'))
        self.policy.change('admin_remove',self.identity)
        self.access.settings=self.policy.data
        self.assertFalse(font_developer_allowed(self.access,self.context,'127.0.0.1'))
        self.assertEqual(self.policy.data['epoch'],epoch)

    def test_existing_developer_and_local_master_conditions_remain(self):
        self.access.visitors[self.identity]['nickname']='나츠'
        self.assertTrue(font_developer_allowed(self.access,self.context,'203.0.113.10'))
        self.access.visitors[self.identity]['nickname']='ordinary'
        (self.root.parent/'master_developer.ini').write_text('[master_developer]\nenabled=true\n',encoding='utf-8')
        self.assertTrue(font_developer_allowed(self.access,self.context,'127.0.0.1'))
        self.assertFalse(font_developer_allowed(self.access,dict(self.context,authenticated=False),'127.0.0.1'))

    def test_legacy_settings_default_empty_and_malformed_grants_fail_closed(self):
        legacy=deepcopy(self.policy.data);legacy.pop('web_admins')
        self.assertEqual(validate_settings(legacy)['web_admins'],{})
        for bad in [[],{'not-id':{}},{self.identity:dict(nickname='x',registered_at=4)},
                    {self.identity:dict(nickname='x\n',registered_at='now')}]:
            with self.assertRaises(ValueError):validate_settings(dict(legacy,web_admins=bad))

    def manager(self, rows):
        manager=WebServiceManager.__new__(WebServiceManager)
        manager.lock=threading.RLock();manager.access=self.policy
        manager.access_jobs=deque([('job','admin_add',self.identity)])
        manager.access_results={};manager.access_view={}
        manager.access_status=lambda:dict(visitors=rows)
        manager._log=Mock()
        return manager

    def test_desktop_queue_registers_verified_member_record_not_user_supplied_name(self):
        manager=self.manager([dict(id=self.identity,role='member',state='조회 중',nickname='verified name')])
        manager._access_jobs()
        self.assertTrue(manager.access_results['job']['ok'])
        self.assertEqual(self.policy.data['web_admins'][self.identity]['nickname'],'verified name')

    def test_desktop_queue_rejects_guest_or_non_logged_in_record(self):
        for role,state in [('guest','조회 중'),('member','최근 접속')]:
            manager=self.manager([dict(id=self.identity,role=role,state=state,nickname='name')])
            manager._access_jobs()
            self.assertFalse(manager.access_results['job']['ok'])
            self.assertEqual(self.policy.data['web_admins'],{})

    def test_https_editor_companion_requires_login_and_supports_revalidation(self):
        from web_https_app import HTTPSApplication
        context=dict(self.context,cookies=[],available=True,configured=True)
        self.access.context=Mock(return_value=context)
        self.access.read_wait=Mock(return_value=0)
        assets={name:b'asset' for name in ('index.html','login.html','style.css','schedule.js','login.js')}
        app=HTTPSApplication('example.test',8766,NS(),self.access,None,assets=assets,authority_check=lambda:True)
        env=dict(REQUEST_METHOD='GET',PATH_INFO='/custom-editor.js',HTTP_HOST='example.test:8766',
                 REMOTE_ADDR='127.0.0.1',**{'wsgi.url_scheme':'https'})
        result={}
        def start(status,headers):result.update(code=int(status.split()[0]),headers=dict(headers))
        raw=b''.join(app(env,start))
        self.assertEqual(result['code'],200)
        self.assertIn(b'WebCustomEditor',raw)
        env['HTTP_IF_NONE_MATCH']=result['headers']['ETag']
        self.assertEqual(b''.join(app(env,start)),b'')
        self.assertEqual(result['code'],304)
        context['authenticated']=False
        b''.join(app(env,start));self.assertEqual(result['code'],401)

    def test_loopback_handler_serves_same_editor_companion(self):
        from web_service_worker import LocalHandler
        handler=LocalHandler.__new__(LocalHandler)
        handler.path='/custom-editor.js';handler.headers=HTTPMessage()
        handler.client_address=('127.0.0.1',12345)
        handler.server=NS(access=self.access,assets={})
        handler._context=Mock(return_value=dict(self.context,cookies=[]))
        handler._send=Mock();handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0],200)
        self.assertIn(b'WebCustomEditor',handler._send.call_args.args[1])


if __name__=='__main__':unittest.main()
