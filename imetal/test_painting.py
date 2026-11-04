import os
os.environ['QT_QPA_PLATFORM']='offscreen'

import unittest
from PySide6.QtWidgets import QApplication,QTableWidgetItem
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from application import Window
from ui import load_fonts,font
from theme import apply_light_theme
from painting import inner_shadow


class PaintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        apply_light_theme(cls.app);load_fonts();cls.app.setFont(font(10))

    def setUp(self):
        self.window=Window(preview=True)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close();self.window.deleteLater();self.app.processEvents()

    def test_window_corners_have_partial_alpha_coverage(self):
        image=self.window.grab().toImage()
        self.assertEqual(image.pixelColor(0,0).alpha(),0)
        coverage=[image.pixelColor(x,y).alpha() for x in range(20) for y in range(20)]
        self.assertTrue(any(0<a<255 for a in coverage))

    def test_separator_fades_at_both_ends(self):
        form=self.window.scan_button.parentWidget()
        marker=form.separator_marker
        image=form.grab().toImage()
        center=image.pixelColor(marker.x()+100,marker.y()).lightness()
        left=image.pixelColor(marker.x(),marker.y()).lightness()
        right=image.pixelColor(marker.x()+199,marker.y()).lightness()
        self.assertLess(center,min(left,right)-20)

    def test_inset_shadow_has_blurred_directional_falloff(self):
        image=inner_shadow(300,69,1)
        self.assertGreater(image.pixelColor(100,2).alpha(),image.pixelColor(100,66).alpha())
        falloff=[image.pixelColor(100,y).alpha() for y in range(2,9)]
        self.assertGreater(len(set(falloff)),3)
        self.assertEqual(image.pixelColor(100,35).alpha(),0)

    def test_scroll_thumb_is_contained_and_drags_the_table(self):
        table=self.window.table
        table.setRowCount(80)
        for row in range(80):table.setItem(row,0,QTableWidgetItem(str(row)))
        self.app.processEvents();self.app.processEvents()
        frame=self.window.table_frame
        bar=frame.scrollbar
        self.assertTrue(bar.isVisible())
        self.assertLessEqual(bar.x()+bar.width(),frame.width()-1)
        QTest.mousePress(bar,Qt.LeftButton,pos=bar.thumb_rect().center().toPoint())
        QTest.mouseMove(bar,QPoint(6,bar.height()-8))
        QTest.mouseRelease(bar,Qt.LeftButton,pos=QPoint(6,bar.height()-8))
        self.assertEqual(bar.value(),bar.maximum())
        self.assertEqual(table.verticalScrollBar().value(),bar.value())
        self.assertLessEqual(bar.thumb_rect().bottom(),bar.height()-7)
        self.assertGreaterEqual(bar.thumb_rect().top(),7)


if __name__=='__main__':unittest.main()
