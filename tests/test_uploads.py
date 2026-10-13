"""Upload identity, content fingerprints and legacy metadata compatibility."""

import hashlib
import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.datastructures import FileStorage

from backend.app import create_app


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-uploads-")
        self.config = {"TESTING": True, "DATA_DIR": self.temp.name,
                       "GITEE_AI_API_KEY": "", "IMPORT_LEGACY_HISTORY": False}
        self.app = create_app(self.config)
        self.service = self.app.extensions["workbench_service"]
        self.client = self.app.test_client()
        self.owner = self.register(self.client, "upload@example.test")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def register(self, client, email):
        response = client.post("/desktop/api/register", json={"email": email, "password": "test-only-password"})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["user"]["id"]

    def upload(self, data=b"category,amount\nA,10\n", name="sales.csv", project="", client=None):
        response = (client or self.client).post("/desktop/api/files", data={
            "file": (io.BytesIO(data), name), "projectId": project,
        })
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_same_content_same_name_same_project_reuses_id_and_full_hashes(self):
        data = b"category,amount\nA,10\n"
        first = self.upload(data)
        second = self.upload(data)
        self.assertEqual(first, second)
        self.assertEqual(first["originalName"], "sales.csv")
        self.assertEqual(first["md5"], hashlib.md5(data, usedforsecurity=False).hexdigest())
        self.assertEqual(first["sha256"], hashlib.sha256(data).hexdigest())
        files = self.client.get("/desktop/api/state").json["files"]
        self.assertEqual(files, [first])
        self.assertEqual(len(list((self.service.store.directory(self.owner) / "files").iterdir())), 1)

    def test_different_content_name_project_or_account_stays_separate(self):
        first = self.upload(b"first")
        changed = self.upload(b"other")
        renamed = self.upload(b"first", name="renamed.csv")
        project = self.client.post("/desktop/api/projects", json={"name": "Project"}).json["id"]
        in_project = self.upload(b"first", project=project)
        other = self.app.test_client()
        self.register(other, "other@example.test")
        in_other = self.upload(b"first", client=other)
        self.assertEqual(len({value["id"] for value in (first, changed, renamed, in_project, in_other)}), 5)
        for metadata, expected in ((first, b"first"), (changed, b"other")):
            response = self.client.get("/desktop/api/files/" + metadata["id"])
            self.assertEqual(response.data, expected)
            response.close()
        self.assertEqual(other.get("/desktop/api/files/" + first["id"]).status_code, 404)
        self.assertEqual(self.upload(b"first", project=project)["id"], in_project["id"])

    def test_md5_collision_cannot_merge_different_sha256(self):
        digest = Mock()
        digest.hexdigest.return_value = "0" * 32
        with patch("backend.app.modules.workbench.service.hashlib.md5", return_value=digest):
            first = self.upload(b"first")
            second = self.upload(b"other")
        self.assertEqual(first["md5"], second["md5"])
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertNotEqual(first["id"], second["id"])

    def test_original_long_names_are_not_merged_after_display_truncation(self):
        first = self.upload(name="a" * 180 + "-first.csv")
        second = self.upload(name="a" * 180 + "-other.csv")
        self.assertEqual(first["name"], second["name"])
        self.assertNotEqual(first["originalName"], second["originalName"])
        self.assertNotEqual(first["id"], second["id"])

    def test_legacy_hashes_backfilled_persisted_and_file_stream_rewound(self):
        original = self.upload()
        def remove_hashes(state):
            state["files"][0].pop("md5")
            state["files"][0].pop("sha256")
            state["files"][0].pop("originalName")
        self.service.store.update(self.owner, remove_hashes)
        response = self.client.get("/desktop/api/files/" + original["id"])
        self.assertEqual(response.data, b"category,amount\nA,10\n")
        response.close()
        state = self.client.get("/desktop/api/state").json["files"][0]
        self.assertEqual(state["md5"], original["md5"])
        self.assertEqual(state["sha256"], original["sha256"])
        self.assertEqual(self.upload()["id"], original["id"])
        self.service.close()
        self.app = create_app(self.config)
        self.service = self.app.extensions["workbench_service"]
        restored = self.service.store.get(self.owner)["files"][0]
        self.assertEqual(restored["md5"], original["md5"])
        self.assertEqual(restored["sha256"], original["sha256"])

    def test_state_backfill_preserves_existing_sha256_binding(self):
        original = self.upload(b"first")
        self.service.store.update(self.owner, lambda state: state["files"][0].pop("md5"))
        path = self.service.store.directory(self.owner) / "files" / original["id"]
        path.write_bytes(b"other")
        result = self.client.get("/desktop/api/state").json["files"][0]
        self.assertEqual(result["md5"], hashlib.md5(b"other", usedforsecurity=False).hexdigest())
        self.assertEqual(result["sha256"], original["sha256"])
        self.assertNotEqual(self.upload(b"first")["id"], original["id"])

    def test_matching_stale_metadata_never_reuses_changed_or_missing_bytes(self):
        first = self.upload(b"first")
        path = self.service.store.directory(self.owner) / "files" / first["id"]
        path.write_bytes(b"other")
        second = self.upload(b"first")
        self.assertNotEqual(second["id"], first["id"])
        (path.parent / second["id"]).unlink()
        third = self.upload(b"first")
        self.assertNotIn(third["id"], {first["id"], second["id"]})

    def test_concurrent_identical_uploads_produce_one_file(self):
        ready = threading.Barrier(4)
        def upload():
            ready.wait(timeout=5)
            return self.service.upload_file(self.owner, FileStorage(stream=io.BytesIO(b"concurrent"), filename="sales.csv"), "")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: upload(), range(4)))
        self.assertEqual(len({item["id"] for item in results}), 1)
        self.assertEqual(len(self.service.store.get(self.owner)["files"]), 1)


if __name__ == "__main__":
    unittest.main()
