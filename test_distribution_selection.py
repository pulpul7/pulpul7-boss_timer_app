"""Master checklist and file-level publishing checks; no build or live UI."""
import ast
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from distribution_catalog import OPTIONS, OPTION_IDS
from distribution_defaults import DECLARATION, apply_declared_defaults, read_declaration
from distribution_publish import publish_selection, selection_at
from distribution_settings_keys import GENERAL_KEYS


class FileSelectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='distribution-selection-unit-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.project=self.root/'project';self.data=self.root/'data';self.runtime=self.root/'runtime'
        self.put(self.project/'boss_timer_gui.spec','fixture, not executed')
        for _,_,_,name,_ in OPTIONS:
            target=self.project/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(Path(name),target)
        shutil.copy2(Path(DECLARATION),self.project/DECLARATION)
        self.sources={}

    @staticmethod
    def put(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(value if isinstance(value,bytes) else value.encode('utf-8'))

    def source(self,group,value):
        path=self.runtime/(group+'.json' if group not in {'settings','voice'} else group+'.ini')
        self.put(path,value);self.sources[group]=path;return path

    def test_old_group_selection_becomes_individual_boss_and_area_checkboxes(self):
        self.put(self.project/DECLARATION,json.dumps(dict(schema=1,enabled=True,groups=['boss','fixed','spectrum'])))
        self.assertEqual(selection_at(self.project),{'boss_definition','areas','fixed','spectrum'})
        self.assertEqual(len(OPTIONS),13)
        self.assertEqual(len(OPTION_IDS),len(OPTIONS))

    def test_single_file_save_keeps_area_and_other_seed_files(self):
        area=(self.project/'init/schedule_area_definitions.txt').read_bytes()
        raw='새 이름|약칭|002:00|지역|1|0|색|색|1|0\n'
        self.source('boss_definition',raw)
        result=publish_selection(self.project,{'boss_definition'},self.sources)
        self.assertEqual((self.project/'init/schedule_boss_definitions.txt').read_text(encoding='utf-8'),raw)
        self.assertEqual((self.project/'init/schedule_area_definitions.txt').read_bytes(),area)
        self.assertEqual(result['declaration']['groups'],['boss_definition'])
        self.assertEqual(read_declaration(self.project)[0]['groups'],['boss_definition'])
        self.assertTrue(Path(result['backup']).is_dir())

    def test_general_preferences_export_never_copies_credentials_or_season_identity(self):
        self.source('settings','[settings]\nfont_family=테스트 글꼴\nschedule_color_data_enabled=false\ndiscord_token=SECRET\ncurrent_season_no=999\nweb_password=SECRET2\n')
        publish_selection(self.project,{'settings'},self.sources)
        raw=(self.project/'init/default_settings.ini').read_text(encoding='utf-8')
        self.assertIn('테스트 글꼴',raw)
        for forbidden in ('SECRET','current_season_no','web_password','discord_token'):
            self.assertNotIn(forbidden,raw)

    def test_notice_font_voice_and_music_exports_drop_private_runtime_fields(self):
        prefs=json.loads((self.project/'init/default_notice_settings.json').read_bytes())
        self.source('notice',json.dumps(dict(prefs,events={'private':'history'},delivery_ledger=[{'keep':True}],server_id='9')))
        self.source('font_sizes',json.dumps(dict(schema=1,font_size_defaults={'system':2},custom=[{'private':'font'}],disabled=['mine'])))
        self.source('voice','[edge_tts]\nenabled=1\nvoice=ko-KR-SunHiNeural\nrate=2\nvolume=0\npitch=5\ntoken=SECRET\n')
        self.source('music',json.dumps(dict(search_terms=['봄비'],private='SECRET')))
        publish_selection(self.project,{'notice','font_sizes','voice','music'},self.sources)
        data=json.loads((self.project/'init/default_notice_settings.json').read_bytes())
        self.assertNotIn('events',data);self.assertNotIn('delivery_ledger',data);self.assertNotIn('server_id',data)
        self.assertEqual(json.loads((self.project/'web_assets/fonts/size_defaults.json').read_bytes()),dict(schema=1,sizes={'system':2}))
        self.assertNotIn('SECRET',(self.project/'init/default_edge_tts.ini').read_text())
        self.assertEqual(json.loads((self.project/'init/default_background_music_settings.json').read_bytes()),dict(search_terms=['봄비']))

    def test_notice_preferences_do_not_reset_installed_module_unless_explicitly_selected(self):
        state=dict(schema=1,active='1.1.0',pending='1.2.0',previous='',history=[])
        target=self.data/'update_ai/state.json';self.put(target,json.dumps(state))
        publish_selection(self.project,{'notice'},self.sources)
        apply_declared_defaults(self.project,self.data,season='season_18',server='9')
        self.assertEqual(json.loads(target.read_bytes()),state)

    def test_notice_module_option_exports_policy_without_copying_runtime_code(self):
        self.source('notice_module','private installed code must not be distributed')
        publish_selection(self.project,{'notice_module'},self.sources)
        raw=(self.project/'init/default_notice_module.json').read_bytes()
        self.assertEqual(json.loads(raw),dict(schema=1,use_bundled_once=True))
        self.assertEqual(selection_at(self.project),{'notice_module'})
        self.assertIsNotNone(read_declaration(self.project))

    def test_empty_selection_disables_overwrite_without_changing_seeds(self):
        before=(self.project/'init/schedule_fixed_bosses.txt').read_bytes()
        publish_selection(self.project,set(),{})
        self.assertIsNone(read_declaration(self.project))
        self.assertEqual((self.project/'init/schedule_fixed_bosses.txt').read_bytes(),before)

    def test_failed_declaration_restores_seed_files_and_previous_selection(self):
        path=self.project/'init/schedule_fixed_bosses.txt';before=path.read_bytes();manifest=(self.project/DECLARATION).read_bytes()
        self.source('fixed','새 이름|시간|요일|주기|기준|사용|글자색|배경색\n')
        with patch('distribution_publish.declare',side_effect=ValueError('fixture failure')),self.assertRaises(ValueError):
            publish_selection(self.project,{'fixed'},self.sources)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual((self.project/DECLARATION).read_bytes(),manifest)

    def test_nonproject_folder_cannot_be_used_as_a_deployment_destination(self):
        with self.assertRaises(ValueError):publish_selection(self.root/'elsewhere',{'fixed'},self.sources)
        self.assertFalse((self.root/'elsewhere').exists())

    def test_all_files_apply_without_resetting_notices_or_personal_fonts(self):
        from notice_module.payload.notice_management import NoticeStore,KST
        now=datetime(2026,10,15,20,tzinfo=KST)
        store=NoticeStore(self.data/'notice_data','9',clock=lambda:now)
        store.register(dict(id='manual-keep',title='보존할 공지',tts_text='직접 쓴 안내',valid_from=now,valid_until=now+timedelta(hours=1)))
        state=store._load();state['delivery_ledger']=[dict(at=now.isoformat(),event_id='keep',opportunity_id='keep',group='participation')]
        self.put(store.path,json.dumps(state,ensure_ascii=False))
        fonts=dict(schema=1,default='system',disabled=['my-font'],custom=[dict(id='private-font')],font_size_defaults={'system':-1})
        self.put(self.data/'web_service/font_settings.json',json.dumps(fonts))
        self.put(self.data/'boss_timer_settings.ini','[settings]\ndiscord_token=KEEP\ncurrent_season_no=18\n')
        self.put(self.data/'server_profiles/season_18/9/schedule_state.json','keep live schedule')
        publish_selection(self.project,OPTION_IDS,{})
        result=apply_declared_defaults(self.project,self.data,season='season_18',server='9')
        self.assertTrue(result['applied'])
        notice=json.loads(store.path.read_bytes())
        self.assertIn('manual-keep',notice['events']);self.assertEqual(notice['delivery_ledger'],state['delivery_ledger'])
        current_fonts=json.loads((self.data/'web_service/font_settings.json').read_bytes())
        for key in ('custom','disabled','default'):self.assertEqual(current_fonts[key],fonts[key])
        self.assertIn('KEEP',(self.data/'boss_timer_settings.ini').read_text(encoding='utf-8'))
        self.assertIn('current_season_no = 18',(self.data/'boss_timer_settings.ini').read_text(encoding='utf-8'))
        self.assertEqual((self.data/'server_profiles/season_18/9/schedule_state.json').read_text(),'keep live schedule')
        self.assertFalse(apply_declared_defaults(self.project,self.data,season='season_18',server='9')['applied'])

    def test_individual_area_receipt_does_not_allow_an_older_legacy_boss_declaration(self):
        from distribution_defaults import declare,json_bytes
        old=declare(self.project,['boss']);old['sequence']=100
        self.put(self.project/DECLARATION,json_bytes(old))
        apply_declared_defaults(self.project,self.data,season='season_18',server='9')
        newer=declare(self.project,['boss_definition','areas']);newer['sequence']=200
        self.put(self.project/DECLARATION,json_bytes(newer))
        apply_declared_defaults(self.project,self.data,season='season_18',server='9')
        self.put(self.project/DECLARATION,json_bytes(old))
        self.assertFalse(apply_declared_defaults(self.project,self.data,season='season_18',server='8')['applied'])

    def test_one_file_rollback_refresh_preserves_other_files_in_same_group(self):
        base=self.data/'server_profiles/season_18/9/settings_rollback/baseline'
        entries=[dict(source='old',snapshot='init/schedule_area_definitions.txt',exists=True),
                 dict(source='old',snapshot='init/schedule_boss_definitions.txt',exists=True)]
        self.put(base/'manifest.json',json.dumps(dict(groups={'boss':entries,'alarm':[dict(keep=True)]})))
        self.put(base/'init/schedule_area_definitions.txt','keep area baseline')
        self.put(base/'boss_timer_settings.ini','[settings]\nschedule_alarm_enabled=false\n')
        publish_selection(self.project,{'boss_definition','voice'},self.sources)
        apply_declared_defaults(self.project,self.data,season='season_18',server='9')
        self.assertEqual((base/'init/schedule_area_definitions.txt').read_text(),'keep area baseline')
        manifest=json.loads((base/'manifest.json').read_bytes())
        self.assertIn(entries[0],manifest['groups']['boss'])
        self.assertIn(dict(keep=True),manifest['groups']['alarm'])
        self.assertEqual((base/'global/edge_tts.ini').read_bytes(),(self.project/'init/default_edge_tts.ini').read_bytes())

    def test_generated_setting_payloads_keep_the_declared_choices(self):
        # Execute only the seed-file function in an OS-temp fixture, never a build spec.
        import configparser,os
        publish_selection(self.project,OPTION_IDS,{})
        tree=ast.parse(Path('boss_timer_gui.spec').read_text(encoding='utf-8'))
        names={'build_distribution_default_seed_datas','build_distribution_font_size_seed','build_distribution_boss_spectrum_seed'}
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        ns=dict(Path=Path,os=os,json=json,shutil=shutil,configparser=configparser,project_root=self.project,
                BUILD_VERSION='fixture-version',DISTRIBUTION_DEFAULT_SETTING_OVERRIDES={'precision_auto_apply':'false'},
                DISTRIBUTION_DEFAULT_ALARM_OVERRIDES={},read_distribution_default_setting_keys=lambda:GENERAL_KEYS,
                read_declaration=read_declaration)
        exec(compile(ast.Module(body=functions,type_ignores=[]),'<seed-files-only>','exec'),ns)
        env=self.root/'empty-profile'
        with patch.dict(os.environ,APPDATA=str(env),LOCALAPPDATA=str(env)):
            # Force the normal generated INI path with conflicting developer values.
            self.put(env/'BossTimer/boss_timer_settings.ini','[settings]\nfont_family=wrong\ndiscord_token=SECRET\n')
            generated=ns['build_distribution_default_seed_datas']()
        bundle=self.root/'bundle-fixture'
        for source in (self.project/'init').iterdir():
            if source.is_file():self.put(bundle/'init'/source.name,source.read_bytes())
        for _,_,_,name,_ in OPTIONS:
            if name.startswith('web_assets/'):
                self.put(bundle/name,(self.project/name).read_bytes())
        for source,destination in generated:
            self.put(bundle/destination/Path(source).name,Path(source).read_bytes())
        self.assertIsNotNone(read_declaration(bundle))
        self.assertNotIn('SECRET',(bundle/'init/default_settings.ini').read_text(encoding='utf-8'))
        self.assertEqual((bundle/'notice_module/payload/notice_defaults.json').read_bytes(),
                         (self.project/'init/default_notice_settings.json').read_bytes())


class MasterDistributionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree=ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))

    def function(self,name,namespace):
        node=next(n for n in ast.walk(self.tree) if isinstance(n,ast.FunctionDef) and n.name==name)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<master-checklist>','exec'),namespace)
        return namespace[name]

    def test_only_master_sees_button_and_banner_never_overlaps_it(self):
        tk=NS(Button=Mock(return_value=Mock()))
        button=tk.Button.return_value;banner=Mock()
        app=NS(schedule_window=Mock(),_is_master_developer_mode_enabled=Mock(return_value=False),percent_font='font',
               _widget_available=lambda w:w is not None,_bind_hover_button=Mock(),_open_distribution_settings=Mock(),schedule_banner_check=banner)
        refresh=self.function('_refresh_distribution_settings_button',dict(tk=tk,SCHEDULE_WINDOW_WIDTH=1180,SCHEDULE_WINDOW_HEIGHT=760))
        refresh(app);tk.Button.assert_not_called()
        self.assertEqual(banner.place.call_args.kwargs['x'],962)
        app._is_master_developer_mode_enabled.return_value=True
        refresh(app);tk.Button.assert_called_once()
        self.assertEqual(button.place.call_args.kwargs['x'],962)
        self.assertEqual(banner.place.call_args.kwargs['x'],858)
        refresh(app);tk.Button.assert_called_once()
        app._is_master_developer_mode_enabled.return_value=False
        refresh(app);button.place_forget.assert_called_once()

    def test_nonmaster_cannot_open_or_save_by_calling_handlers_directly(self):
        from distribution_settings_ui import DistributionSettingsWindow
        app=NS(_is_master_developer_mode_enabled=lambda:False)
        open_window=self.function('_open_distribution_settings',{})
        with patch('distribution_settings_ui.tk.Toplevel') as top:
            open_window(app)
            with self.assertRaises(PermissionError):DistributionSettingsWindow(app,'unused','unused')
            top.assert_not_called()
        controller=DistributionSettingsWindow.__new__(DistributionSettingsWindow);controller.app=app;controller.close=Mock()
        with patch('distribution_settings_ui.publish_selection') as publish:
            controller.save();publish.assert_not_called()
        controller.close.assert_called_once()

    def test_general_export_keys_match_gui_allowlist_and_have_no_credentials(self):
        node=next(n for n in self.tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DEFAULT_SETTINGS_SEED_KEYS' for t in n.targets))
        self.assertEqual(ast.literal_eval(node.value),GENERAL_KEYS)
        self.assertFalse(any('token' in key or 'password' in key or 'season_no' in key for key in GENERAL_KEYS))


if __name__=='__main__':unittest.main()
