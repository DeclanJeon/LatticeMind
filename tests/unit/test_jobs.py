import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from latticemind_core.jobs import (
    JOB_DEFINITIONS, SlotState, catch_up_expired, next_run, ownership_collision,
    render_launchd, render_systemd, render_task_scheduler, result_mapping,
    run_slot, should_run_slot, slot_identity,
)


class JobsTest(unittest.TestCase):
    def test_matrix_defaults(self):
        self.assertEqual(len(JOB_DEFINITIONS), 5)
        self.assertEqual([j.enabled for j in JOB_DEFINITIONS], [False, False, False, True, True])
        self.assertTrue(all(j.kill_grace_seconds == 10 for j in JOB_DEFINITIONS))
        self.assertEqual([(j.networkPolicy, j.permissionCapability, j.lockScope) for j in JOB_DEFINITIONS], [
            ("none", "scheduled-write:morning", "user-install"),
            ("none", "scheduled-write:nightly", "user-install"),
            ("none", "scheduled-write:weekly", "user-install"),
            ("research", "observe", "user-install"),
            ("none", "observe", "user-install"),
        ])

    def test_dst_gap_first_valid_and_fold_first(self):
        gap_job = JOB_DEFINITIONS[0]
        gap = next_run(gap_job, datetime(2024, 3, 9, 23, tzinfo=ZoneInfo("America/New_York")), "America/New_York")
        self.assertEqual(gap.date(), date(2024, 3, 10))
        gap_job = gap_job.__class__("gap", "gap", "daily", "local", "first-valid", 21600, 0, "skip", "user-install", 900, enabledByDefault=True, weekday=None, hour=2, minute=30)
        gap = next_run(gap_job, datetime(2024, 3, 9, 23, tzinfo=ZoneInfo("America/New_York")), "America/New_York")
        self.assertEqual((gap.hour, gap.minute), (3, 0))
        fold_job = gap_job.__class__("fold", "fold", "daily", "local", "first-valid", 21600, 0, "skip", "user-install", 900, enabledByDefault=True, weekday=None, hour=1, minute=30)
        fold = next_run(fold_job, datetime(2024, 11, 2, 23, tzinfo=ZoneInfo("America/New_York")), "America/New_York")
        self.assertEqual(fold.fold, 0)

    def test_catch_up_and_once_per_slot(self):
        job = JOB_DEFINITIONS[0]
        scheduled = datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC"))
        self.assertFalse(catch_up_expired(scheduled, scheduled + timedelta(hours=6), job))
        self.assertTrue(catch_up_expired(scheduled, scheduled + timedelta(hours=6, seconds=1), job))
        slot = slot_identity(job, date(2025, 1, 1))
        state = SlotState()
        self.assertEqual(run_slot(job, slot, state, lambda: 0), "succeeded")
        self.assertEqual(run_slot(job, slot, state, lambda: 0), "skipped")
        self.assertEqual({j.ownershipMarker for j in JOB_DEFINITIONS}, {"latticemind-job-v1"})
        required = {"calendar", "timezone", "dstPolicy", "catchUpWindowSeconds", "jitterSeconds", "overlapPolicy", "lockScope", "timeoutSeconds", "killGraceSeconds", "retry", "networkPolicy", "permissionCapability", "executable", "argv", "environmentAllowlist", "ownershipMarker", "enabledByDefault"}
        self.assertTrue(all(required <= set(j.to_dict()) for j in JOB_DEFINITIONS))
        self.assertTrue(should_run_slot(job, slot, set()))
        self.assertFalse(should_run_slot(job, slot, {slot}))
        self.assertFalse(should_run_slot(job, slot, set(), running=True))

    def test_renderer_parity_and_arguments(self):
        for job in JOB_DEFINITIONS:
            for text in (render_systemd(job), render_launchd(job), render_task_scheduler(job)):
                self.assertIn("latticemind-job-v1", text)
                self.assertIn(job.mode, text)
                self.assertIn(str(job.timeout_seconds), text)
                self.assertIn(str(job.kill_grace_seconds), text)
        launchd = render_launchd(JOB_DEFINITIONS[0])
        self.assertIn("<string>$HOME/.local/bin/latticemind-maintain</string><string>morning</string>", launchd)

    def test_result_and_ownership_collision(self):
        self.assertEqual(result_mapping(124), "timed_out")
        self.assertEqual(result_mapping(1), "failed")
        self.assertFalse(ownership_collision("latticemind-job-v1"))
        self.assertTrue(ownership_collision("other-owner"))
        self.assertFalse(ownership_collision(None))
    def test_valid_windows_xml_generation(self):
        import xml.etree.ElementTree as ET
        text = render_task_scheduler(JOB_DEFINITIONS[0])
        root = ET.fromstring(text)
        values = " ".join(x for x in root.itertext() if x)
        self.assertIn("latticemind-job-v1 schema=job-definition-v1 mode=morning", values)
        self.assertIn(r"\LatticeMind\morning", values)

    def test_malformed_hashtable_rejection(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text(json.dumps({"jobs": [{"job_id": "morning", "owner": "latticemind-job-v1", "schema": "job-definition-v1", "path": str(path)}]}))
            with self.assertRaises(ValueError):
                from latticemind_core.jobs import reinstall_owned_jobs
                reinstall_owned_jobs(path, platform="windows")

    def test_windows_marker_collision(self):
        import json
        import tempfile
        from pathlib import Path
        from latticemind_core.jobs import reinstall_owned_jobs
        with tempfile.TemporaryDirectory() as directory:
            xml = Path(directory) / "morning.xml"
            xml.write_text("<Task><Description>other-owner</Description></Task>")
            manifest = Path(directory) / "jobs.json"
            manifest.write_text(json.dumps({"jobs": [{"job_id": "morning", "owner": "latticemind-job-v1", "schema": "job-definition-v1", "platform": "windows", "path": str(xml)}]}))
            with self.assertRaises(ValueError):
                reinstall_owned_jobs(manifest, platform="windows")

    def test_systemd_record_coverage(self):
        for job in JOB_DEFINITIONS:
            text = render_systemd(job)
            self.assertIn("# owner=latticemind-job-v1 schema=job-definition-v1", text)
            self.assertIn("[Service]", text)
            self.assertIn("[Timer]", text)


if __name__ == "__main__":
    unittest.main()
