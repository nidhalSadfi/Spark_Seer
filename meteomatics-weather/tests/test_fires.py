import tempfile
import unittest
from pathlib import Path

from pipeline.fires import read_history
from pipeline.grid import make_grid


class FireTests(unittest.TestCase):
    def test_coordinate_join_and_absent_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fires.csv"
            self.assertFalse(read_history(path, make_grid())["available"])
            path.write_text("latitude,longitude,acq_date,acq_time,frp\n"
                            "37.27,9.87,2024-07-01,1230,12.4\n"
                            "37.28,9.88,2024-07-01,1240,8\n"
                            "0,0,2024-07-01,1230,5\n"
                            "bad,9,2024-07-01,1230,5\n")
            history = read_history(path, make_grid())
            self.assertTrue(history["available"])
            self.assertEqual(len(history["features"]), 2)
            self.assertEqual(history["calendar"], [{"date": "2024-07-01", "count": 2}])
            self.assertEqual(history["features"][0]["properties"]["grid_id"], "tn_37.0_9.5")
            self.assertEqual(history["skipped_rows"], 2)

    def test_markers_are_bounded_without_losing_calendar_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fires.csv"
            path.write_text("latitude,longitude,acq_date\n" + "37.27,9.87,2024-07-01\n" * 5)
            history = read_history(path, make_grid(), limit=2)
            self.assertEqual(len(history["features"]), 2)
            self.assertEqual(history["matching_detections"], 5)
            self.assertTrue(history["truncated"])
            self.assertEqual(history["calendar"][0]["count"], 5)
