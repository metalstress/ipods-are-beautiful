import os
os.environ['QT_QPA_PLATFORM']='offscreen'

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette,QColor
from PySide6.QtCore import Qt
from application import Window
from theme import apply_light_theme
from ui import load_fonts,font,APP_NAME,DeviceCard


class FakeJob:
    def __init__(self,config,paths=None,**kwargs):
        self.paths=list(paths) if paths is not None else None
        self.stop=threading.Event()
    def isRunning(self):return True


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        load_fonts()
        apply_light_theme(cls.app)
        cls.app.setFont(font(10))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.data_patch=patch('application.DATA',self.root);self.data_patch.start()
        self.a=self.root/'a.mp3';self.a.write_bytes(b'a'*10)
        self.b=self.root/'b.mp3';self.b.write_bytes(b'b'*20)
        self.save=patch('application.save_settings');self.save.start()
        self.jobs=patch('application.Job',FakeJob);self.jobs.start()
        self.window=Window(preview=True)
        self.window.preview=False
        self.window.watch=Mock()
        self.window.devices={'touch':{'id':'touch','name':'iPod touch 4','mode':'USB','free':10**10,'total':2*10**10,'config':{}}}
        self.window.show();self.app.processEvents()

    def tearDown(self):
        self.window.worker=None
        self.window.jobs=[]
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.jobs.stop();self.save.stop();self.data_patch.stop();self.tmp.cleanup()

    def test_manual_queue_waits_for_upload(self):
        self.window.auto.setChecked(False)
        self.window.add_paths([str(self.a)])
        self.window.watch.assert_not_called()
        self.assertTrue(self.window.upload.isEnabled())
        self.window.upload.click()
        self.assertEqual(self.window.worker.paths,[self.a])
        self.window.watch.assert_called_once()
        self.assertFalse(self.window.upload.isEnabled())

    def test_auto_upload_starts_and_manual_button_stays_disabled(self):
        self.window.auto.setChecked(True)
        self.window.add_paths([str(self.a)])
        self.window.watch.assert_called_once()
        self.assertEqual(self.window.worker.paths,[self.a])
        self.assertFalse(self.window.upload.isEnabled())
        self.assertEqual(self.window.auto.text(),'auto upload')

    def test_eject_cancels_active_device_and_removes_selection(self):
        self.window.auto.setChecked(False)
        self.window.add_paths([str(self.a)])
        self.window.start()
        stop=self.window.worker.stop
        self.window.eject('touch')
        self.assertTrue(stop.is_set())
        self.assertNotIn('touch',self.window.devices)

    def test_removing_first_row_keeps_remaining_title(self):
        self.window.auto.setChecked(False)
        self.window.add_paths([str(self.a),str(self.b)])
        self.window.table.selectRow(0)
        self.window.remove_selected()
        self.assertEqual(self.window.paths,[self.b])
        self.assertEqual(self.window.table.item(0,0).text(),'b')

    def test_reference_layout_and_product_name(self):
        self.assertEqual(self.window.windowTitle(),APP_NAME)
        self.assertEqual((self.window.width(),self.window.height()),(910,661))
        for widget,expected in [(self.window.drop,(238,122,655,184)),
            (self.window.table_frame,(238,338,655,251)),(self.window.upload,(780,599,113,25))]:
            p=widget.mapTo(self.window,widget.rect().topLeft())
            self.assertEqual((p.x(),p.y(),widget.width(),widget.height()),expected)

    def test_popup_and_checkbox_keep_light_palette(self):
        dark=QPalette();dark.setColor(QPalette.Base,QColor('#181818'))
        self.app.setPalette(dark)
        apply_light_theme(self.app)
        # Offscreen QPA reports Unknown; validate the real popup pixels as well.
        self.assertNotEqual(self.app.styleHints().colorScheme(),Qt.ColorScheme.Dark)
        self.assertEqual(self.window.mode.view().palette().color(QPalette.Base).name(),'#ffffff')
        self.window.mode.showPopup();self.app.processEvents()
        image=self.window.mode.view().grab().toImage()
        colors=[image.pixelColor(x,y).lightness() for x in range(5,image.width()-5,8)
                for y in range(3,image.height()-3,8)]
        self.assertGreater(sum(colors)/len(colors),180)
        self.window.mode.hidePopup()

    def test_track_and_byte_counts_are_visible(self):
        self.window.show_progress('Uploading',{'tracks_total':6,'imported':2,
            'bytes_transferred':1024**3,'bytes_total':2*1024**3,'bytes_estimated':False},.5)
        self.assertIn('Imported 2 / 6 tracks',self.window.lcd.detail)
        self.assertIn('1.00 / 2.00 GB',self.window.lcd.detail)
        self.assertIn('MB/s',self.window.lcd.detail)

    def test_multiple_devices_require_explicit_ejection(self):
        self.window.auto.setChecked(False)
        self.window.devices['phone']={'id':'phone','kind':'iphone','config':{}}
        self.window.add_paths([str(self.a)])
        self.window.start()
        self.window.watch.assert_not_called()
        self.assertIn('Eject',self.window.status.text())

    def test_iphone_card_actually_paints_the_phone(self):
        card=DeviceCard(dict(id='phone',kind='iphone',name='iPhone',mode='Wi-Fi',free=10**10,total=10**11))
        card.show();self.app.processEvents()
        image=card.grab().toImage()
        colors={image.pixelColor(x,y).rgb() for x in range(13,37) for y in range(12,65)}
        self.assertGreater(len(colors),100)
        card.close();card.deleteLater()

    def test_scan_replaces_stale_usb_with_fresh_wifi_result(self):
        self.window.auto.setChecked(False)
        self.window.connection_result({'devices':[dict(id='phone',kind='iphone',name='iPhone',
            mode='Wi-Fi',free=10**10,total=10**11,config={})]})
        self.assertEqual(list(self.window.devices),['phone'])
        self.assertEqual(self.window.devices['phone']['mode'],'Wi-Fi')


if __name__=='__main__':unittest.main()
