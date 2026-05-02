import tempfile
import unittest
from pathlib import Path
import plistlib

import watchdog


class TestWatchdogSchedule(unittest.TestCase):
    def test_compute_stale_seconds_from_plist(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "job.plist"
            plist = {
                "Label": "com.wechat.articlecrawler.runproject",
                "StartCalendarInterval": [
                    {"Hour": 3, "Minute": 0},
                    {"Hour": 7, "Minute": 0},
                    {"Hour": 9, "Minute": 0},
                    {"Hour": 11, "Minute": 30},
                    {"Hour": 15, "Minute": 0},
                    {"Hour": 17, "Minute": 0},
                    {"Hour": 19, "Minute": 0},
                    {"Hour": 21, "Minute": 0},
                    {"Hour": 23, "Minute": 0},
                ],
            }
            p.write_bytes(plistlib.dumps(plist))
            stale = watchdog._compute_stale_seconds_from_plist(p)
            self.assertEqual(stale, 29700)

    def test_parse_calendar_intervals(self):
        points = watchdog._parse_launchd_calendar_intervals(
            [{"Hour": 8, "Minute": 0}, {"Hour": 8, "Minute": 0}, {"Hour": 12, "Minute": 30}]
        )
        self.assertEqual(points, [(8, 0), (12, 30)])


if __name__ == "__main__":
    unittest.main()

