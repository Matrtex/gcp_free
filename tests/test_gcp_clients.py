from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gcp_clients


class GcpClientsTestCase(unittest.TestCase):
    def tearDown(self):
        gcp_clients.instances_client.cache_clear()

    def test_instances_client_is_cached_within_process(self):
        created_clients = []

        def create_instance_client(**kwargs):
            client = SimpleNamespace(kwargs=kwargs, index=len(created_clients))
            created_clients.append(client)
            return client

        fake_compute = SimpleNamespace(InstancesClient=create_instance_client)
        with patch.object(gcp_clients, "compute_v1", fake_compute), patch.object(
            gcp_clients,
            "resourcemanager_v3",
            SimpleNamespace(),
        ), patch.object(gcp_clients, "google_exceptions", SimpleNamespace()):
            first_client = gcp_clients.instances_client()
            second_client = gcp_clients.instances_client()

        self.assertIs(first_client, second_client)
        self.assertEqual(len(created_clients), 1)
        self.assertEqual(first_client.kwargs["transport"], "rest")


if __name__ == "__main__":
    unittest.main()
