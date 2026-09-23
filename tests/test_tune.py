import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('tune', Path(__file__).parents[1] / 'scripts/tune.py')
tune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tune)


class TuneTests(unittest.TestCase):
    def doctor(self, sensing=None, diagnostics=None, aps=None, scan_error=False):
        s = {'ready': True, 'calibrating': False, 'threshold': .05}
        d = {'csi_occupancy': .9, 'csi_accepted_pps': 20, 'traffic_tx_pps': 20}
        s.update(sensing or {})
        d.update(diagnostics or {})
        def call(host, path, **kwargs):
            if path == '/sensing': return s
            if path == '/wifi': return {'ssid': 'Home', 'bssid': 'aa', 'rssi_dbm': -55}
            if path == '/wifi/scans' and scan_error: raise OSError('scan failed')
            if path == '/wifi/access-points':
                return {'access_points': aps if aps is not None else [
                    {'ssid': 'Home', 'bssid': 'AA', 'rssi_dbm': -55}]}
            return {}
        out = io.StringIO()
        with patch.object(tune, 'call', side_effect=call), patch.object(tune, 'diag', return_value=d), patch.object(tune.time, 'sleep'), contextlib.redirect_stdout(out):
            result = tune.cmd_doctor('test')
        return result, out.getvalue()

    def test_healthy(self):
        self.assertEqual(self.doctor()[0], 0)

    def test_neighbor_never_recommended(self):
        result, out = self.doctor(aps=[{'ssid':'Neighbor', 'bssid':'BB', 'rssi_dbm':-30}])
        self.assertEqual(result, 2)
        self.assertNotIn('pin BB', out)

    def test_same_ssid_stronger_ap(self):
        result, out = self.doctor(aps=[{'ssid':'Home', 'bssid':'BB', 'rssi_dbm':-30}])
        self.assertEqual(result, 1)
        self.assertIn('pin BB', out)

    def test_low_coverage_is_problem(self):
        result, out = self.doctor(diagnostics={'csi_occupancy': .1})
        self.assertEqual(result, 1)
        self.assertNotIn('=> Healthy', out)

    def test_invalid_measurements_never_healthy(self):
        for field in ('csi_occupancy', 'csi_accepted_pps'):
            for value in (None, 'unknown', True, float('nan'), -1):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.doctor(diagnostics={field:value})[0], 2)

    def test_detector_unknown_or_busy(self):
        for state in ({'ready':None}, {'calibrating':None}, {'calibrating':True}, {'threshold':None}):
            with self.subTest(state=state):
                self.assertEqual(self.doctor(sensing=state)[0], 2)

    def test_scan_failure(self):
        self.assertEqual(self.doctor(scan_error=True)[0], 2)

    def test_known_problem_takes_precedence(self):
        self.assertEqual(self.doctor(sensing={'ready':False}, scan_error=True)[0], 1)

    def test_ap_filter_handles_missing_data(self):
        self.assertEqual(tune.same_network_aps([None, {}, {'ssid':'Home','bssid':'x','rssi_dbm':None}], 'Home'), [])
        self.assertEqual(tune.same_network_aps([{'ssid':'','bssid':'x','rssi_dbm':-30}], ''), [])

    def calibrate(self, states):
        clock = [0]
        def sleep(seconds): clock[0] += seconds
        states = iter(states)
        last = [{}]
        def call(host, path, **kwargs):
            if path.endswith('/calibrations'): return {}
            last[0] = next(states, last[0])
            return last[0]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(tune, 'call', side_effect=call), patch.object(tune.time,'sleep',side_effect=sleep), patch.object(tune.time,'monotonic',side_effect=lambda:clock[0]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = tune.cmd_calibrate('test', timeout=20)
        return result, out.getvalue(), err.getvalue()

    def test_calibration_waits_for_ready_and_finished(self):
        result, out, err = self.calibrate([
            {'calibrating':True,'ready':True},
            {'calibrating':False,'ready':False},
            {'calibrating':False,'ready':True}])
        self.assertEqual(result, 0)
        self.assertIn('[15s]', out)
        self.assertEqual(err, '')

    def test_calibration_timeout(self):
        for state in ({'calibrating':True,'ready':True}, {'calibrating':False,'ready':False}, {}, 'invalid'):
            with self.subTest(state=state):
                result, out, err = self.calibrate([state])
                self.assertEqual(result, 1)
                self.assertNotIn('done.', out)
                self.assertIn('timed out', err)

    def test_unreachable_device_is_insufficient(self):
        with patch.object(tune, 'call', side_effect=OSError('offline')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tune.cmd_doctor('test'), 2)

    def test_cli_propagates_result(self):
        with patch('sys.argv', ['tune.py','--host','test','doctor']), patch.object(tune,'cmd_doctor',return_value=2):
            self.assertEqual(tune.main(), 2)


if __name__ == '__main__':
    unittest.main()
