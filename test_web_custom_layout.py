"""User-editor layout checks against real HTML/CSS; no browser or build."""
from html.parser import HTMLParser
from pathlib import Path
import unittest


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.stack=[];self.ids={}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if attrs.get('id'):
            if attrs['id'] in self.ids:raise ValueError('duplicate element ID: '+attrs['id'])
            self.ids[attrs['id']]=(attrs,list(self.stack))
        if tag not in {'meta','link','input','img','br','hr','source','wbr'}:
            self.stack.append((tag,attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:]
                break


class CustomLayoutTests(unittest.TestCase):
    def setUp(self):
        self.html=Path('web_assets/index.html').read_text(encoding='utf-8')
        self.css=Path('web_assets/style.css').read_text(encoding='utf-8')
        self.ids=Elements(self.html).ids

    def test_theme_tab_has_a_separate_panel_and_keeps_all_settings_together(self):
        theme=self.ids['tab-theme'][0]
        self.assertEqual(theme['aria-controls'],'theme-panel')
        self.assertNotIn('data-filter',theme)
        self.assertIn('hidden',self.ids['theme-panel'][0])
        for identity in ('theme-settings','web-font-controls','notice-settings','hidden-help-balloon'):
            ancestors={attrs.get('id') for _,attrs in self.ids[identity][1]}
            self.assertIn('theme-panel',ancestors)
            self.assertNotIn('schedule-panel',ancestors)
            self.assertNotIn('schedule-view',ancestors)
        for identity in ('frame-heading','overview','custom-editor','push-controls'):
            self.assertIn('schedule-view',{attrs.get('id') for _,attrs in self.ids[identity][1]})
        self.assertNotIn('hidden-menu-toggle',self.ids)

    def test_palette_occupies_its_own_footer_row_above_bulk_actions(self):
        for identity in ('boss-spectrum-controls','boss-spectrum-palette'):
            ancestors=self.ids[identity][1]
            self.assertTrue(any(attrs.get('id')=='boss-spectrum-tools' for _,attrs in ancestors))
            self.assertTrue(any('custom-savebar' in attrs.get('class','').split() for _,attrs in ancestors))
            self.assertFalse(any('custom-save-actions' in attrs.get('class','').split() for _,attrs in ancestors))
        self.assertGreater(self.html.index('id="boss-spectrum-palette"'),self.html.index('id="custom-groups"'))
        self.assertLess(self.html.index('id="boss-spectrum-palette"'),self.html.index('id="custom-stars-bottom"'))
        self.assertIn('.custom-savebar{display:grid;grid-template-columns:minmax(0,1fr);',self.css)
        self.assertIn('.boss-spectrum-palette{position:static;',self.css)
        self.assertIn('.custom-spectrum-badge{position:absolute;top:-8px;left:4px;',self.css)

    def test_bottom_bulk_actions_precede_undo_in_persistent_savebar(self):
        for identity in ('custom-stars-bottom','custom-clear-bottom'):
            self.assertTrue(any('custom-save-actions' in attrs.get('class','').split()
                                for _,attrs in self.ids[identity][1]))
            self.assertLess(self.html.index('id="'+identity+'"'),self.html.index('id="custom-undo"'))
        self.assertIn('id="custom-stars-bottom" type="button">주요보스선택</button>',self.html)
        self.assertIn('id="custom-clear-bottom" class="quiet" type="button">전체해제</button>',self.html)


if __name__=='__main__':unittest.main()
