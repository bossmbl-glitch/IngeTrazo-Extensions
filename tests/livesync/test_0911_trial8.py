"""Timer retention and user-facing export controls on real files and Qt."""
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
import test_bridge_backups as backups
import test_0910_trial2 as host_ui
from ingetrazo_d5_livesync import bridge_backups as b
from ingetrazo_d5_livesync.backup_schedule import half_hour
from ingetrazo_d5_livesync.transport import write_json


class RetentionTests(unittest.TestCase):
    setUp=backups.BackupTests.setUp
    save=backups.BackupTests.save

    def test_six_ordinary_points_independent_of_protected_and_daily_tags(self):
        timers=[];protected=[]
        for index in range(9):
            when=self.day+dt.timedelta(days=index)
            timers.append(b.create_point(self.project,['interval'],now=when,force_new=True))
            protected.append(b.create_point(self.project,['half_hour'],now=when+dt.timedelta(seconds=1),
                protected_slots=[half_hour(when)],force_new=True))
            protected.append(b.create_point(self.project,['manual'],pinned=True,now=when+dt.timedelta(seconds=2),force_new=True))
            b.prune_points(self.project)
        rows=b.list_points(self.project,verify=True)
        self.assertEqual({Path(r['path']) for r in rows if not r['pinned']},set(timers[-6:]))
        self.assertTrue(all(p.exists() for p in protected));self.assertEqual(len(rows),24)
        self.assertEqual(b.current_state(self.project)['revision'],1)

    def test_failed_new_backup_does_not_rotate_old_six(self):
        timers=[b.create_point(self.project,['interval'],now=self.day+dt.timedelta(minutes=i*10),force_new=True) for i in range(6)]
        from ingetrazo_d5_livesync.backup_service import BackupService
        service=BackupService(self.project,auto_schedule=False)
        try:
            with patch.object(b,'store_shared',side_effect=OSError('disk full')):
                service.submit(['interval'],now=self.day+dt.timedelta(minutes=60));service.queue.join()
            self.assertTrue(service.error);self.assertTrue(all(p.exists() for p in timers))
        finally:service.close()


class UiTests(unittest.TestCase):
    setUpClass=classmethod(host_ui.PanelTests.setUpClass.__func__)
    setUp=host_ui.PanelTests.setUp
    baseline=host_ui.PanelTests.baseline
    session=host_ui.PanelTests.session
    panel=host_ui.PanelTests.panel

    def isolated_panel(self):
        from PySide6.QtCore import QSettings
        panel=self.panel();panel.settings=QSettings(str(self.directory/'ui-settings.ini'),QSettings.Format.IniFormat)
        return panel

    def test_conflict_unknown_to_previous_report_is_first_and_red(self):
        from PySide6.QtWidgets import QTableWidget
        from ingetrazo_d5_livesync.material_review import review_dialog
        panel=self.isolated_panel()
        report={'materials':{'AAA':{'targets':{'old':{}}},'Wood':{'targets':{'wood':{}}}}}
        conflict={'name':'ZZZ','reason':'同名多個舊識別','suggestion':'ZZZ－新版'}
        dialog=review_dialog(panel,report,conflict);self.addCleanup(dialog.close)
        table=dialog.findChild(QTableWidget,'material_review_table')
        self.assertEqual(table.rowCount(),3);self.assertEqual(table.item(0,0).text(),'ZZZ')
        self.assertEqual(table.item(0,1).text(),'待核對')
        self.assertIn(conflict['reason'],table.item(0,2).text())
        for col in range(3):self.assertEqual(table.item(0,col).foreground().color().name(),'#b42318')
        self.assertFalse(table.isSortingEnabled())
        from ingetrazo_d5_livesync.material_review import review_rows
        self.assertEqual(len(review_rows(report,{'name':'Wood','reason':'conflict'})),2)

    def test_daily_buttons_and_advanced_confirmation(self):
        from PySide6.QtWidgets import QMessageBox
        panel=self.isolated_panel();self.assertTrue(panel.advanced_section.isHidden())
        self.assertEqual(panel.now_button.text(),'重新整理')
        panel.advanced_toggle.click();self.assertFalse(panel.advanced_section.isHidden())
        panel.set_paused(True);panel.now_button.click()
        self.assertTrue(panel.schedule.paused);self.assertTrue(panel.schedule.manual)
        panel.force_full_next=False
        with patch('ingetrazo_d5_livesync.panel.QMessageBox.question',return_value=QMessageBox.StandardButton.No):panel.sync_full_now()
        self.assertFalse(panel.force_full_next)
        with patch('ingetrazo_d5_livesync.panel.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):panel.sync_full_now()
        self.assertTrue(panel.force_full_next);self.assertTrue(panel.schedule.paused)

    def test_status_export_real_zip_and_remembered_destination(self):
        panel=self.isolated_panel();target=self.directory/'custom'/'我的診斷';target.parent.mkdir()
        write_json(panel.session.directory/'sync-decision-latest.json',{'reason':'first_full_sync','revision':1})
        write_json(panel.session.directory/'material-conflicts.json',{'name':'Wood','reason':'conflict'})
        with patch('ingetrazo_d5_livesync.panel.QFileDialog.getSaveFileName',return_value=(str(target),'')),patch('ingetrazo_d5_livesync.panel.QMessageBox.information'):
            self.assertEqual(panel.progress.dialog.records.text(),'匯出診斷包');panel.progress.dialog.records.click()
        path=target.with_suffix('.zip');self.assertTrue(path.exists())
        with zipfile.ZipFile(path) as z:
            self.assertIsNone(z.testzip());self.assertEqual(json.loads(z.read('material-conflicts.json'))['name'],'Wood')
            self.assertIn('首次',z.read('更新原因.txt').decode('utf-8-sig'))
            self.assertIn('bridge',json.loads(z.read('diagnostics.json')))
        self.assertEqual(panel.settings.value('diagnostic_export_directory'),str(target.parent))
        with patch('ingetrazo_d5_livesync.panel.QFileDialog.getSaveFileName',return_value=('','')) as choose:
            panel.export_performance_bundle()
        self.assertEqual(Path(choose.call_args.args[2]).parent,target.parent)
        self.assertRegex(Path(choose.call_args.args[2]).name,r'\d{8}-\d{6}\.zip$')

    def test_export_cancel_and_failure_do_not_remember_or_overwrite(self):
        panel=self.isolated_panel();target=self.directory/'keep.zip';target.write_bytes(b'existing')
        with patch('ingetrazo_d5_livesync.panel.QFileDialog.getSaveFileName',return_value=('','')),patch('ingetrazo_d5_livesync.perf_diagnostics.export_bundle') as export:
            panel.export_performance_bundle();export.assert_not_called()
        with patch('ingetrazo_d5_livesync.panel.QFileDialog.getSaveFileName',return_value=(str(target),'')),patch('ingetrazo_d5_livesync.perf_diagnostics.export_bundle',side_effect=OSError('disk full')),patch('ingetrazo_d5_livesync.panel.QMessageBox.warning') as warning:
            panel.export_performance_bundle();warning.assert_called_once()
        self.assertEqual(target.read_bytes(),b'existing');self.assertIsNone(panel.settings.value('diagnostic_export_directory'))
